"""Measurements in [-2, +2] seconds around committed OpenFlow reroutes.

UDP probe loss is application datagram loss: successful sender socket enqueue
records are matched to distinct receiver sequence IDs. It is not a wire packet
capture and must not be described as one. All probe clocks are shared by the
Mininet namespaces on one Linux guest. ICMP measurements are round-trip times.
"""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import statistics


PING_PATTERN = re.compile(r"^\[(?P<timestamp>\d+(?:\.\d+)?)\].*time[=<](?P<latency>\d+(?:\.\d+)?)\s*ms")
DEFINITIONS = {
    "Probe_Packets_Queued": "Successful userspace UDP socket enqueue records with send time in the window; not wire transmission count.",
    "Probe_Packets_Delivered": "Distinct receiver sequence IDs matched to queued probes from the send-time window, including delivery after the window but before capture closes.",
    "Probe_Loss_pct": "100 * (queued - distinct matched delivered) / queued. No queued probes yields null, never zero.",
    "Probe_Receiver_Delivered_In_Window": "Distinct probe sequence IDs with actual receiver timestamp in the window, regardless of their transmit window.",
    "Probe_Mean_Abs_Interarrival_Variation_ms": "Mean absolute ((receive_i - receive_previous) - (send_i - send_previous)), over adjacent distinct arrival-order packets whose receive_i is in the window.",
    "Probe_RFC3550_Jitter_End_ms": "RFC3550-style J += (abs(D) - J)/16 from the complete arrival stream; final estimator value within the window, averaged across observed flows for all-flow rows.",
    "Probe_P95_OneWay_ms": "P95 receiver_timestamp minus embedded send_timestamp for received probes in the window. Same guest clock; userspace endpoints.",
    "Average_RTT_ms": "Mean successful ICMP reply RTT with ping reply timestamp in the window; timestamps are reply times, not transmit times.",
    "P95_RTT_ms": "Interpolated P95 of successful ICMP reply RTT values in the window.",
    "Iperf_Receiver_Window_Goodput_Mbps": "Approximate bytes delivered in overlapping receiver iperf intervals, prorated by overlap/window seconds. Interval origin is client process launch, not packet timestamp.",
    "Window_Complete": "Every expected flow spans window_start/window_end within one probe interval and has at least 80% of the nominal queued probe count; false at capture edges or severe sender scheduling gaps.",
}


def read_csv(path):
    if not Path(path).exists():
        return []
    with Path(path).open(newline="") as handle:
        return list(csv.DictReader(handle))


def finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Nonfinite transition measurement")
    return result


def percentile(values, percentage):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def mean(values):
    return statistics.mean(values) if values else None


def load_probe_rows(directory):
    directory = Path(directory)
    tx = []
    rx = []
    for path in sorted(directory.glob("probe_tx_flow_*.csv")):
        for row in read_csv(path):
            if row.get("send_status") != "queued":
                raise ValueError("Unsuccessful or unrecognized probe enqueue")
            tx.append(dict(flow_id=int(row["flow_id"]), sequence=int(row["sequence"]),
                           tx_timestamp=finite_float(row["tx_timestamp"]),
                           payload_bytes=int(row["payload_bytes"]),
                           schedule_lateness_ms=finite_float(row.get("schedule_lateness_ms", 0))))
    for path in sorted(directory.glob("probe_rx_flow_*.csv")):
        for row in read_csv(path):
            rx.append(dict(flow_id=int(row["flow_id"]), sequence=int(row["sequence"]),
                           tx_timestamp=finite_float(row["tx_timestamp"]),
                           rx_timestamp=finite_float(row["rx_timestamp"]),
                           payload_bytes=int(row["payload_bytes"])))
    tx.sort(key=lambda row: (row["tx_timestamp"], row["flow_id"], row["sequence"]))
    rx.sort(key=lambda row: (row["rx_timestamp"], row["flow_id"], row["sequence"]))
    identities = {(row["flow_id"], row["sequence"]): row for row in tx}
    if len(identities) != len(tx):
        raise ValueError("Duplicate sender sequence identity")
    for row in rx:
        origin = identities.get((row["flow_id"], row["sequence"]))
        if origin is None:
            raise ValueError("Receiver probe has no captured sender sequence identity")
        if abs(origin["tx_timestamp"] - row["tx_timestamp"]) > 1e-6 or origin["payload_bytes"] != row["payload_bytes"]:
            raise ValueError("Receiver probe identity or payload does not match sender")
        if row["rx_timestamp"] + .000001 < row["tx_timestamp"]:
            raise ValueError("Probe clock moved backwards")
    return tx, rx


def load_ping_rows(directory, metadata=None):
    directory = Path(directory)
    metadata = metadata or {}
    mappings = metadata.get("measurement", {}).get("ping_logs", [])
    if not mappings:
        mappings = [dict(flow_id=1, file="ping.log")]
    results = []
    for mapping in mappings:
        path = directory / mapping["file"]
        if not path.exists():
            continue
        for line in path.read_text(errors="replace").splitlines():
            match = PING_PATTERN.search(line)
            if match:
                results.append(dict(flow_id=int(mapping["flow_id"]),
                                    timestamp=float(match.group("timestamp")),
                                    rtt_ms=float(match.group("latency"))))
    return results


def committed_reroutes(directory):
    """Control events are recorded after path cutover is confirmed.

    A richer transition log may expose the same transition ID and its precise
    confirmation timestamp. Failed/aborted preparations never become windows.
    """
    directory = Path(directory)
    richer = []
    for row in read_csv(directory / "transitions.csv"):
        stage = str(row.get("stage", row.get("phase", row.get("event", "")))).lower()
        if stage in ("committed", "commit_complete", "transition_committed") or (stage == "commit" and row.get("status") == "committed"):
            richer.append(row)
    result = []
    for index, event in enumerate(read_csv(directory / "control_events.csv"), 1):
        if event.get("event") != "reroute" or event.get("old_path") not in ("A", "B") or event.get("new_path") not in ("A", "B"):
            continue
        timestamp = finite_float(event["timestamp"])
        candidates = [row for row in richer
                      if row.get("old_path", event["old_path"]) == event["old_path"]
                      and row.get("new_path", event["new_path"]) == event["new_path"]
                      and abs(finite_float(row["timestamp"]) - timestamp) <= 2]
        confirmation = min(candidates, key=lambda row: abs(finite_float(row["timestamp"]) - timestamp)) if candidates else None
        if confirmation is not None:
            timestamp = finite_float(confirmation["timestamp"])
        result.append(dict(transition_id=(confirmation or event).get("transition_id") or "reroute-%03d" % index,
                           timestamp=timestamp, old_path=event["old_path"], new_path=event["new_path"],
                           reason=event.get("reason", ""), commit_confirmed=confirmation is not None,
                           timestamp_origin="transition_commit_confirmation" if confirmation else "controller_reroute_request_unacknowledged"))
    return result


def receiver_window_goodput(intervals, first, last, flow_id=None):
    covered = []
    for row in intervals:
        if flow_id is not None and int(row["flow_id"]) != flow_id:
            continue
        start = finite_float(row["start_timestamp"])
        end = finite_float(row["end_timestamp"])
        overlap = max(0, min(last, end) - max(first, start))
        if end > start and overlap:
            covered.append(int(row["bytes"]) * overlap / (end - start))
    return sum(covered) * 8 / (last - first) / 1e6 if covered else None


def _arrival_variation(rx):
    previous = {}
    estimator = {}
    results = []
    seen = set()
    for row in sorted(rx, key=lambda item: item["rx_timestamp"]):
        identity = (row["flow_id"], row["sequence"])
        if identity in seen:
            continue
        seen.add(identity)
        flow = row["flow_id"]
        prior = previous.get(flow)
        if prior:
            d_ms = abs((row["rx_timestamp"] - prior["rx_timestamp"] - row["tx_timestamp"] + prior["tx_timestamp"]) * 1000)
            estimator[flow] = estimator.get(flow, 0.0) + (d_ms - estimator.get(flow, 0.0)) / 16
            results.append(dict(flow_id=flow, timestamp=row["rx_timestamp"], variation_ms=d_ms,
                                jitter_ms=estimator[flow]))
        previous[flow] = row
    return results


def window_metrics(tx, rx, pings, intervals, first, last, expected_flows, probe_interval=.02, flow_id=None):
    if last <= first:
        raise ValueError("Transition window must have positive duration")
    flows = [flow_id] if flow_id is not None else list(expected_flows)
    if not flows:
        raise ValueError("No expected probe flows")
    selected_tx = [row for row in tx if row["flow_id"] in flows and first <= row["tx_timestamp"] < last]
    queued = {(row["flow_id"], row["sequence"]): row for row in selected_tx}
    all_received = {}
    for row in sorted(rx, key=lambda item: item["rx_timestamp"]):
        if row["flow_id"] in flows:
            all_received.setdefault((row["flow_id"], row["sequence"]), row)
    matched = set(queued) & set(all_received)
    selected_rx = {}
    for row in sorted(rx, key=lambda item: item["rx_timestamp"]):
        if row["flow_id"] in flows and first <= row["rx_timestamp"] < last:
            selected_rx.setdefault((row["flow_id"], row["sequence"]), row)
    rtt = [row["rtt_ms"] for row in pings if row["flow_id"] in flows and first <= row["timestamp"] < last]
    delay = [(row["rx_timestamp"] - row["tx_timestamp"]) * 1000 for row in selected_rx.values()]
    variations = [row for row in _arrival_variation(rx) if row["flow_id"] in flows and first <= row["timestamp"] < last]
    last_jitter = {}
    for row in variations:
        last_jitter[row["flow_id"]] = row["jitter_ms"]
    complete = True
    for flow in flows:
        samples = [row["tx_timestamp"] for row in tx if row["flow_id"] == flow]
        if not samples or min(samples) > first + probe_interval or max(samples) < last - probe_interval:
            complete = False
        window_count = sum(first <= timestamp < last for timestamp in samples)
        if window_count < .8 * (last - first) / probe_interval:
            complete = False
    return dict(
        flow_id=flow_id if flow_id is not None else "all", start_timestamp=first, end_timestamp=last,
        Window_Complete=complete,
        Probe_Packets_Queued=len(queued), Probe_Packets_Delivered=len(matched),
        Probe_Lost_Packets=len(queued) - len(matched),
        Probe_Loss_pct=100 * (len(queued) - len(matched)) / len(queued) if queued else None,
        Probe_Receiver_Delivered_In_Window=len(selected_rx),
        Probe_Duplicate_Receives_In_Window=sum(1 for row in rx if row["flow_id"] in flows and first <= row["rx_timestamp"] < last) - len(selected_rx),
        Probe_Mean_Abs_Interarrival_Variation_ms=mean([row["variation_ms"] for row in variations]),
        Probe_RFC3550_Jitter_End_ms=mean(list(last_jitter.values())),
        Probe_P95_OneWay_ms=percentile(delay, 95),
        RTT_Samples=len(rtt), Average_RTT_ms=mean(rtt), P95_RTT_ms=percentile(rtt, 95),
        Iperf_Receiver_Window_Goodput_Mbps=receiver_window_goodput(intervals, first, last, flow_id),
    )


def summarize_transitions(directory, window_seconds=2.0):
    directory = Path(directory)
    metadata = json.loads((directory / "metadata.json").read_text())
    tx, rx = load_probe_rows(directory)
    pings = load_ping_rows(directory, metadata)
    intervals = read_csv(directory / "receiver_intervals.csv")
    flows = [int(row["flow_id"]) for row in metadata.get("measurement", {}).get("probe_flows", [])]
    if not flows:
        flows = sorted({row["flow_id"] for row in tx})
    reroutes = committed_reroutes(directory)
    rows = []
    bins = []
    probe_interval = float(metadata.get("measurement", {}).get("probe_interval_seconds", .02))
    for event in reroutes:
        at = event["timestamp"]
        windows = (("transition", at - window_seconds, at + window_seconds),
                   ("before", at - window_seconds, at), ("after", at, at + window_seconds))
        for label, first, last in windows:
            for flow in [None] + flows:
                metrics = window_metrics(tx, rx, pings, intervals, first, last, flows, probe_interval, flow)
                rows.append(dict(event, window=label, **metrics))
        # One-second bins support honest charts rather than invented smooth data.
        offset = -window_seconds
        while offset < window_seconds:
            last_offset = min(offset + 1.0, window_seconds)
            metrics = window_metrics(tx, rx, pings, intervals, at + offset, at + last_offset, flows, probe_interval)
            bins.append(dict(event, relative_start_seconds=offset, relative_end_seconds=last_offset, **metrics))
            offset = last_offset
    return dict(protocol_version=metadata.get("protocol_version", "safe-te-v1"),
                window_seconds=window_seconds, measurement_clock="shared Linux guest CLOCK_REALTIME",
                definitions=DEFINITIONS, reroutes=reroutes, rows=rows, bins=bins,
                probe_tx_records=len(tx), probe_rx_records=len(rx))


def write_transition_metrics(result, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "transition_metrics.json").open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
    for name, key in (("transition_metrics.csv", "rows"), ("transition_bins.csv", "bins")):
        values = result[key]
        fields = list(values[0]) if values else ["transition_id", "timestamp", "window", "flow_id", "Window_Complete"]
        with (directory / name).open("x", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(values)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--window-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not math.isfinite(args.window_seconds) or args.window_seconds <= 0:
        raise SystemExit("Window seconds must be finite and positive")
    result = summarize_transitions(args.capture, args.window_seconds)
    if args.output_dir:
        write_transition_metrics(result, args.output_dir)
    else:
        print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
