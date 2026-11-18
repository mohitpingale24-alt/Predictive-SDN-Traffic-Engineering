#!/usr/bin/env python3
"""Small sequence-numbered UDP probes for route-transition measurements.

Run sender and receiver in Mininet host namespaces. Every timestamp uses the
shared Linux guest CLOCK_REALTIME. Sender records successful userspace socket
enqueue, not wire transmission; missing matched receive records are measured
application datagram loss. These probes are separate from iperf offered load.
"""
import argparse
import csv
import math
from pathlib import Path
import signal
import socket
import struct
import time


MAGIC = b"SDNP"
HEADER = struct.Struct("!4sBHQq")
VERSION = 1
TX_FIELDS = ("flow_id", "sequence", "tx_timestamp", "payload_bytes", "schedule_lateness_ms", "send_status")
RX_FIELDS = ("flow_id", "sequence", "tx_timestamp", "rx_timestamp", "payload_bytes")


def encode_packet(flow_id, sequence, tx_ns, payload_bytes=128):
    if not 1 <= flow_id <= 65535 or sequence < 1:
        raise ValueError("Invalid probe identity")
    if not HEADER.size <= payload_bytes <= 1400:
        raise ValueError("Probe payload must fit in one small UDP datagram")
    return HEADER.pack(MAGIC, VERSION, flow_id, sequence, tx_ns) + b"\0" * (payload_bytes - HEADER.size)


def decode_packet(data):
    if len(data) < HEADER.size:
        raise ValueError("Truncated UDP probe")
    magic, version, flow_id, sequence, tx_ns = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION or flow_id < 1 or sequence < 1:
        raise ValueError("Unrecognized UDP probe")
    return flow_id, sequence, tx_ns


def run(args):
    for value in (args.duration, args.interval):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Probe duration and interval must be finite and positive")
    if args.start_at is not None and not math.isfinite(args.start_at):
        raise ValueError("Invalid probe start timestamp")
    if not 1 <= args.port <= 65535:
        raise ValueError("Invalid UDP port")
    stop = [False]
    def finish(_signal, _frame):
        stop[0] = True
    signal.signal(signal.SIGTERM, finish)
    signal.signal(signal.SIGINT, finish)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally merging two experiment captures.
    with args.output.open("x", newline="") as handle:
        fields = TX_FIELDS if args.role == "sender" else RX_FIELDS
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        handle.flush()
        start_at = args.start_at if args.start_at is not None else time.time()
        stop_at = start_at + args.duration
        last_flush = time.monotonic()
        if args.role == "receiver":
            sock.bind((args.host, args.port))
            sock.settimeout(.2)
            if args.ready_file:
                args.ready_file.write_text("ready\n")
            print("UDP probe receiver ready", flush=True)
            while not stop[0] and time.time() < stop_at:
                try:
                    data, _peer = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                rx_ns = time.time_ns()
                flow_id, sequence, tx_ns = decode_packet(data)
                if flow_id != args.flow_id:
                    raise ValueError("Received probe from an unexpected flow")
                writer.writerow(dict(flow_id=flow_id, sequence=sequence,
                                     tx_timestamp="%.9f" % (tx_ns / 1e9),
                                     rx_timestamp="%.9f" % (rx_ns / 1e9), payload_bytes=len(data)))
                if time.monotonic() - last_flush >= .2:
                    handle.flush()
                    last_flush = time.monotonic()
        else:
            start_mono = time.monotonic() + max(0.0, start_at - time.time())
            next_send = start_mono
            sequence = 0
            while not stop[0] and time.time() < stop_at:
                delay = next_send - time.monotonic()
                if delay > 0:
                    time.sleep(min(delay, .1))
                    continue
                now_mono = time.monotonic()
                sequence += 1
                tx_ns = time.time_ns()
                packet = encode_packet(args.flow_id, sequence, tx_ns, args.payload_bytes)
                queued = sock.sendto(packet, (args.host, args.port))
                if queued != len(packet):
                    raise RuntimeError("UDP datagram was not completely queued")
                writer.writerow(dict(flow_id=args.flow_id, sequence=sequence,
                                     tx_timestamp="%.9f" % (tx_ns / 1e9), payload_bytes=len(packet),
                                     schedule_lateness_ms="%.6f" % (max(0.0, now_mono - next_send) * 1000),
                                     send_status="queued"))
                # Do not create an artificial catch-up burst after scheduling lag.
                next_send += args.interval
                if now_mono - next_send > 4 * args.interval:
                    next_send = now_mono + args.interval
                if time.monotonic() - last_flush >= .2:
                    handle.flush()
                    last_flush = time.monotonic()
        handle.flush()
    sock.close()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("sender", "receiver"), required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=56000)
    parser.add_argument("--flow-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--start-at", type=float)
    parser.add_argument("--interval", type=float, default=.02)
    parser.add_argument("--payload-bytes", type=int, default=128)
    return parser.parse_args()


if __name__ == "__main__":
    run(parse_args())
