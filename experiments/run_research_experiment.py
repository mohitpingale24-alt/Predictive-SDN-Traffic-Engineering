#!/usr/bin/env python3
"""Run one new, isolated safe traffic-engineering experiment on Linux.

The baseline campaign and its runner remain untouched. The output directory
must be new. Concurrent flows share an aggregate A/B route; this implementation
does not claim independent per-flow path selection.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time

try:
    from .quality_gate import receiver_summary
    from .scenarios import PROTOCOL_VERSION, SCENARIOS, SYSTEM_CONFIG, TOPOLOGIES, scenario_definition, system_definition
    from .transition_metrics import load_probe_rows, load_ping_rows, read_csv, summarize_transitions, write_transition_metrics
except ImportError:
    from quality_gate import receiver_summary
    from scenarios import PROTOCOL_VERSION, SCENARIOS, SYSTEM_CONFIG, TOPOLOGIES, scenario_definition, system_definition
    from transition_metrics import load_probe_rows, load_ping_rows, read_csv, summarize_transitions, write_transition_metrics


RECEIVER_INTERVAL_FIELDS = (
    "flow_id", "segment", "start_timestamp", "end_timestamp", "bytes", "packets",
    "lost_packets", "seconds", "jitter_ms", "receiver", "interval_reference",
)
SEGMENT_FIELDS = (
    "segment", "flow_id", "source", "receiver", "rate_mbps", "duration_seconds",
    "launch_timestamp", "finished_timestamp", "result_file",
)
PROBE_INTERVAL_SECONDS = .02
PROBE_PAYLOAD_BYTES = 128
PING_INTERVAL_SECONDS = .1


def write_metadata(path, metadata):
    # Readers never see a partially written JSON document.
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(metadata, indent=2, allow_nan=False))
    temporary.replace(path)


def command_version(command):
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    value = (result.stdout or result.stderr).strip()
    return value.splitlines()[0] if value else "unavailable"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repository_provenance(project_root):
    files = ["path_switch_controller.py", "telemetry_runtime.py", "confidence_policy.py",
             "route_transition.py", "openflow_routes.py", "experiments/scenarios.py", "experiments/run_research_experiment.py",
             "experiments/transition_probe.py", "experiments/transition_metrics.py", "experiments/quality_gate.py",
             # Every behavior-changing input the controller can load:
             # CN policy engines and their data files, the conformal
             # runtime + its calibration artifact, link mapping, failure
             # recovery, drift and anomaly modules. Omitting any of these
             # lets behavior change under an unchanged fingerprint.
             "conformal_runtime.py", "link_map.py", "failure_recovery.py",
             "qos_policy.py", "acl_firewall.py", "slicing.py",
             "acl_rules.example.json", "slices.example.json",
             "drift_detection.py", "drift_adaptation.py",
             "anomaly_mitigation.py", "explainability.py", "link_latency.py",
             "topology_graph.py",
             "cnt-ml/models/gbdt_portable.json", "cnt-ml/models/gbdt_parity_fixture.json",
             "cnt-ml/models/feature_list.json", "cnt-ml/models/gbdt_confidence.json",
             "cnt-ml/models/conformal_calibration.json"]
    files.extend(str(path.relative_to(project_root)) for path in sorted((project_root / "cnt-ml/src").glob("*.py")))
    commit = subprocess.run(["git", "-C", str(project_root), "rev-parse", "HEAD"],
                            capture_output=True, text=True, check=False)
    return dict(commit=commit.stdout.strip() if commit.returncode == 0 else "unavailable",
                source_sha256={name: sha256_file(project_root / name) for name in sorted(set(files))
                               if (project_root / name).exists()})


def host_environment():
    versions = {name: command_version(command) for name, command in (
        ("mininet", ["mn", "--version"]), ("ryu", ["ryu-manager", "--version"]),
        ("iperf3", ["iperf3", "--version"]), ("open_vswitch", ["ovs-vswitchd", "--version"]),
    )}
    return dict(host_os=platform.platform(), kernel=platform.release(), architecture=platform.machine(),
                python=platform.python_version(), **versions)


def require_runtime():
    if not sys.platform.startswith("linux") or os.geteuid() != 0:
        raise SystemExit("The research experiment requires root in a Linux Mininet guest.")
    for binary in ("iperf3", "ping", "ryu-manager", "ovs-vsctl"):
        if not shutil.which(binary):
            raise SystemExit("Required command not found: " + binary)


def build_network(controller_port, definition):
    from mininet.link import TCLink
    from mininet.net import Mininet
    from mininet.node import OVSSwitch, RemoteController

    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
    controller = net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=controller_port)
    hosts = {}
    switches = {}
    try:
        names = sorted({flow[key] for flow in definition["flows"] for key in ("source", "receiver")})
        for name in names:
            hosts[name] = net.addHost(name, ip="10.0.0.%d/24" % int(name[1:]))
        switch_count = 4 if definition["topology"] == "diamond4" else 6
        for index in range(1, switch_count + 1):
            switches[index] = net.addSwitch("s%d" % index, dpid="%016x" % index,
                                            protocols="OpenFlow13", failMode="secure")
        if switch_count == 4:
            net.addLink(hosts["h1"], switches[1], port2=1)
            net.addLink(hosts["h2"], switches[4], port2=1)
        else:
            for index, flow in enumerate(definition["flows"], 1):
                net.addLink(hosts[flow["source"]], switches[5], port2=index)
                net.addLink(hosts[flow["receiver"]], switches[6], port2=index)
            # The core edge port1 remains the host-side aggregation port.
            net.addLink(switches[5], switches[1], port1=4, port2=1)
            net.addLink(switches[6], switches[4], port1=4, port2=1)
        common = dict(max_queue_size=100, use_htb=True)
        a, b = definition["path_a_capacity_mbps"], definition["path_b_capacity_mbps"]
        net.addLink(switches[1], switches[2], port1=2, port2=1, bw=a, delay="2ms", **common)
        net.addLink(switches[2], switches[4], port1=2, port2=2, bw=a, delay="2ms", **common)
        net.addLink(switches[1], switches[3], port1=3, port2=1, bw=b, delay="4ms", **common)
        net.addLink(switches[3], switches[4], port1=2, port2=3, bw=b, delay="4ms", **common)
        net.build()
        controller.start()
        for switch in switches.values():
            switch.start([controller])
    except BaseException:
        net.stop()
        raise
    return net, hosts


def controller_environment(project_root, output_dir, args, definition):
    config = system_definition(args.system)
    environment = os.environ.copy()
    environment.update({
        "SDN_CONTROL_MODE": config["control_mode"], "SDN_ACTIVE_MODEL": config["model"],
        "SDN_INITIAL_PATH": config.get("initial_path", "A"),
        "SDN_SAFE_REROUTE": "1" if config["safe_reroute"] else "0",
        "SDN_CONFIDENCE_GATE": "1" if config["confidence_gate"] else "0",
        "SDN_PATH_A_CAPACITY_MBPS": str(definition["path_a_capacity_mbps"]),
        "SDN_PATH_B_CAPACITY_MBPS": str(definition["path_b_capacity_mbps"]),
        "SDN_MONITOR_INTERVAL_SECONDS": "1", "SDN_CONGESTION_THRESHOLD": "85",
        "SDN_CONTIGUOUS_HISTORY": "1",
        "SDN_RECOVERY_THRESHOLD": "70", "SDN_MINIMUM_IMPROVEMENT": "5",
        "SDN_CONSECUTIVE_PREDICTIONS": "3", "SDN_REROUTE_COOLDOWN_SECONDS": "15",
        "SDN_TELEMETRY_CSV": str(output_dir / "telemetry.csv"),
        "SDN_PREDICTIONS_CSV": str(output_dir / "predictions.csv"),
        "SDN_CONTROL_EVENTS_CSV": str(output_dir / "control_events.csv"),
        "SDN_TRANSITIONS_CSV": str(output_dir / "transitions.csv"),
        "SDN_DECISIONS_CSV": str(output_dir / "decisions.csv"),
        "SDN_PING_LOG": str(output_dir / "ping.log"), "PYTHONUNBUFFERED": "1",
    })
    return environment, config


def monitor_cpu(pid, output_path, stop_event):
    previous = None
    with output_path.open("x", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "cpu_percent", "memory_percent"])
        writer.writeheader()
        while not stop_event.wait(1):
            try:
                fields = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()
                ticks = int(fields[11]) + int(fields[12])
                now = time.monotonic()
                if previous:
                    cpu = 100 * (ticks - previous[1]) / os.sysconf("SC_CLK_TCK") / (now - previous[0])
                    result = subprocess.run(["ps", "-p", str(pid), "-o", "%mem="],
                                            capture_output=True, text=True, check=False)
                    writer.writerow(dict(timestamp=time.time(), cpu_percent=cpu,
                                         memory_percent=result.stdout.strip()))
                    handle.flush()
                previous = now, ticks
            except (OSError, ValueError, IndexError):
                if not Path("/proc/%d" % pid).exists():
                    break


def receiver_intervals(payload, launch_timestamp, flow, segment):
    receiver_summary(payload)
    rows = payload.get("server_output_json", {}).get("intervals", [])
    if not rows:
        raise ValueError("Receiver per-second interval capture is required")
    results = []
    for item in rows:
        row = item.get("sum", {})
        if row.get("sender") is not False or row.get("omitted", False):
            raise ValueError("Interval is not explicit receiver UDP data")
        for key in ("start", "end", "bytes", "packets", "lost_packets", "seconds", "jitter_ms"):
            if key not in row or not math.isfinite(float(row[key])) or float(row[key]) < 0:
                raise ValueError("Missing/invalid receiver interval field: " + key)
        if not row["seconds"] or row["end"] <= row["start"]:
            raise ValueError("Receiver interval has no duration")
        results.append(dict(flow_id=flow["flow_id"], segment=segment,
                            start_timestamp=launch_timestamp + float(row["start"]),
                            end_timestamp=launch_timestamp + float(row["end"]),
                            bytes=int(row["bytes"]), packets=int(row["packets"]),
                            lost_packets=int(row["lost_packets"]), seconds=float(row["seconds"]),
                            jitter_ms=float(row["jitter_ms"]), receiver=flow["receiver"],
                            interval_reference="client_process_launch_plus_receiver_offset_approximate"))
    return results


def validate_controller_log(controller_log):
    """Reject controller/transport/audit failures even without a traceback."""
    lowered = controller_log.casefold()
    markers = ("traceback (most recent call last)", "prediction failed", "error sending",
               "openflow error", "transition audit failed")
    if any(marker in lowered for marker in markers):
        raise ValueError("Controller error found in research log")


def validate_transition_audit(transition_rows):
    """Require complete acknowledged transactions before verifying a safe run.

    A normal low-load run can contain only controller/ready at transaction ID0.
    An acknowledged ingress change still draining or cleaning at verification
    is incomplete, even when no error has yet occurred. Failed and rolled-back
    captures remain preserved but are excluded from verified comparisons.
    """
    unsafe = {"failed", "degraded", "indeterminate", "aborted"}
    if any(row.get("status") in unsafe for row in transition_rows):
        raise ValueError("Failed, aborted or indeterminate OpenFlow route transition")
    grouped = {}
    ready = False
    for row in transition_rows:
        try:
            identifier = int(row["transition_id"])
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError("Invalid transition audit identifier")
        if identifier == 0:
            if (row.get("stage"), row.get("status")) != ("controller", "ready"):
                raise ValueError("Invalid controller readiness audit record")
            ready = True
            continue
        if identifier < 0:
            raise ValueError("Invalid transition audit identifier")
        grouped.setdefault(identifier, []).append((row.get("stage"), row.get("status")))
    if not ready:
        raise ValueError("Missing safe-controller readiness audit")
    for identifier, rows in grouped.items():
        required = (("transaction", "started"), ("commit", "committed"),
                    ("transaction", "complete"))
        if any(rows.count(event) != 1 for event in required):
            raise ValueError("Incomplete or duplicate transition audit: %s" % identifier)
        positions = [rows.index(event) for event in required]
        if positions != sorted(positions) or positions[-1] != len(rows) - 1:
            raise ValueError("Invalid transition audit ordering: %s" % identifier)
    return dict(completed_transactions=len(grouped), aborted_transitions=0)


def check_research_run(directory, metadata):
    """Extension gate without applying the legacy fixed-inference fraction.

    Confidence may suppress routing while forecasts still remain observable.
    Missing RTT may deliberately skip some inference; complete telemetry and
    actual finite inference are checked independently and their counts logged.
    """
    directory = Path(directory)
    controller_log = (directory / "controller.log").read_text(errors="replace")
    validate_controller_log(controller_log)
    expected = len(metadata["workload"]) * len(metadata["flows"])
    files = sorted(directory.glob("iperf_flow_*.json"))
    if len(files) != expected:
        raise ValueError("Incomplete concurrent receiver captures")
    for path in files:
        receiver_summary(json.loads(path.read_text()))
    intervals = read_csv(directory / "receiver_intervals.csv")
    segments = read_csv(directory / "iperf_segments.csv")
    if len(segments) != expected or not intervals:
        raise ValueError("Missing receiver interval timeline")
    for segment in segments:
        subset = [row for row in intervals if row["segment"] == segment["segment"] and int(row["flow_id"]) == int(segment["flow_id"])]
        seconds = sum(float(row["seconds"]) for row in subset)
        if not subset or abs(seconds - float(segment["duration_seconds"])) > .25:
            raise ValueError("Receiver intervals do not cover a full flow segment")
    start, end = float(metadata["workload_started_at"]), float(metadata["workload_finished_at"])
    duration = end - start
    telemetry = read_csv(directory / "telemetry.csv")
    predictions = read_csv(directory / "predictions.csv")
    forecast_counts = {}
    for link in ("S1-S2", "S1-S3"):
        times = sorted({float(row["timestamp"]) for row in telemetry if row["link_id"] == link and start <= float(row["timestamp"]) <= end})
        if len(times) < max(3, .9 * duration) or any(right - left > 3 for left, right in zip(times, times[1:])):
            raise ValueError("Incomplete telemetry coverage: " + link)
        subset = [row for row in predictions if row["link_id"] == link and start + 11 <= float(row["timestamp"]) <= end]
        valid = [row for row in subset if row.get("prediction_status") == "predicted"
                 and row.get("predicted_utilization") not in (None, "")
                 and math.isfinite(float(row["predicted_utilization"]))]
        forecast_counts[link] = dict(eligible=len(subset), finite_predictions=len(valid))
        if metadata["control_mode"] == "predictive" and duration >= 20 and len(valid) < 5:
            raise ValueError("No substantial actual model inference: " + link)
    tx, rx = load_probe_rows(directory)
    expected_flows = {int(flow["flow_id"]) for flow in metadata["flows"]}
    if {row["flow_id"] for row in tx} != expected_flows or {row["flow_id"] for row in rx} != expected_flows:
        raise ValueError("Missing sequence-probe sender or receiver flow capture")
    for flow in expected_flows:
        times = [row["tx_timestamp"] for row in tx if row["flow_id"] == flow and start <= row["tx_timestamp"] <= end]
        if len(times) < .8 * duration / PROBE_INTERVAL_SECONDS:
            raise ValueError("Insufficient sequence-probe send coverage")
        if min(times) > start + .25 or max(times) < end - .25:
            raise ValueError("Sequence-probe capture does not span the workload")
    ping_rows = load_ping_rows(directory, metadata)
    if any(sum(row["flow_id"] == flow for row in ping_rows) < 5 for flow in expected_flows):
        raise ValueError("Missing high-frequency ping RTT measurements")
    if metadata["controller"]["safe_reroute"]:
        if not (directory / "transitions.csv").exists() or not (directory / "decisions.csv").exists():
            raise ValueError("Missing safe transition or decision audit log")
        transition_rows = read_csv(directory / "transitions.csv")
        transition_audit = validate_transition_audit(transition_rows)
    else:
        transition_audit = dict(completed_transactions=0, aborted_transitions=0)
    return dict(status="PASS", receiver_segments=len(files), receiver_intervals=len(intervals),
                probe_tx_records=len(tx), probe_rx_records=len(rx), successful_rtt_samples=len(ping_rows),
                forecast_coverage=forecast_counts, **transition_audit)


def _stop_process(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=tuple(SYSTEM_CONFIG), required=True)
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), default="ramp")
    parser.add_argument("--topology", choices=TOPOLOGIES)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repetition", type=int, default=1)
    parser.add_argument("--path-a-capacity", type=float)
    parser.add_argument("--path-b-capacity", type=float)
    parser.add_argument("--controller-port", type=int, default=6633)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--duration-scale", type=float, default=1.0)
    return parser.parse_args()


def run(args=None):
    args = args or parse_args()
    require_runtime()
    if args.repetition < 1 or not 1024 <= args.controller_port <= 65535:
        raise ValueError("Invalid repetition index or controller port")
    if not math.isfinite(args.settle_seconds) or not 1 <= args.settle_seconds <= 60:
        raise ValueError("Settle seconds must be between 1 and 60")
    definition = scenario_definition(args.scenario, args.topology, args.path_a_capacity,
                                     args.path_b_capacity, args.duration_scale)
    project_root = Path(__file__).resolve().parents[1]
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    environment, config = controller_environment(project_root, output_dir, args, definition)
    pairs = definition["flows"]
    metadata = dict(
        experiment_id=output_dir.name, protocol_version=PROTOCOL_VERSION,
        research_schema=1, capture_schema="research1",
        created_at=time.time(), created_at_iso=datetime.now(timezone.utc).isoformat(),
        command_line=[sys.executable, *sys.argv], status="initializing",
        system=args.system, control_mode=config["control_mode"], model=config["model"],
        scenario=args.scenario, repetition=args.repetition, repetitions=1,
        topology=definition["topology"], scenario_definition=definition,
        smoke=definition["smoke"], workload=definition["workload"], flows=pairs,
        path_a_capacity_mbps=definition["path_a_capacity_mbps"],
        path_b_capacity_mbps=definition["path_b_capacity_mbps"],
        path_capacities_mbps=dict(A=definition["path_a_capacity_mbps"], B=definition["path_b_capacity_mbps"]),
        congestion_threshold=85.0, recovery_threshold=70.0, sampling_interval_seconds=1.0,
        prediction_horizon_seconds=5, history_samples_required=11,
        controller=dict(config, congestion_threshold_percent=85.0, recovery_threshold_percent=70.0,
                        minimum_improvement_percent=5.0, consecutive_signals=3, cooldown_seconds=15.0,
                        openflow_port=args.controller_port, routing_scope="aggregate shared A/B path bundle",
                        limitation="All concurrent flows move together. There is no independent per-flow path selection."),
        measurement=dict(
            clock="shared Linux guest CLOCK_REALTIME", probe_interval_seconds=PROBE_INTERVAL_SECONDS,
            probe_payload_bytes=PROBE_PAYLOAD_BYTES, ping_interval_seconds=PING_INTERVAL_SECONDS,
            probe_flows=[dict(flow_id=flow["flow_id"], tx_file="probe_tx_flow_%02d.csv" % flow["flow_id"],
                              rx_file="probe_rx_flow_%02d.csv" % flow["flow_id"]) for flow in pairs],
            ping_logs=[dict(flow_id=flow["flow_id"], file="ping.log" if flow["flow_id"] == 1 else "ping_flow_%02d.log" % flow["flow_id"]) for flow in pairs],
            udp_probe_loss_definition="Successful userspace socket enqueues minus distinct matched receiver sequence IDs; not a wire transmission measurement.",
            receiver_interval_time_definition="Approximate client process launch plus receiver interval offsets; precise transition datagram loss uses probe timestamps.",
            rtt_definition="Successful ICMP round trip at 0.1 second nominal interval; logged ping timestamp is reply time.",
            additional_traffic="50 small UDP probes/s and 10 ICMP requests/s per host pair, in addition to offered iperf application rate."),
        environment=host_environment(), repository=repository_provenance(project_root),
        # The effective controller configuration, recorded verbatim:
        # two runs are only comparable when every SDN_* setting that
        # reached the controller matches, not just the nominal system
        # name. (Output paths are run-specific and excluded.)
        effective_controller_environment={
            key: value for key, value in sorted(environment.items())
            if key.startswith("SDN_") and not key.endswith(("_CSV", "_LOG"))},
    )
    confidence_path = project_root / "cnt-ml/models/gbdt_confidence.json"
    if confidence_path.exists():
        calibration = json.loads(confidence_path.read_text())
        metadata["confidence"] = dict(enabled=config["confidence_gate"],
            calibration_file=str(confidence_path.relative_to(project_root)),
            calibration_sha256=sha256_file(confidence_path),
            method=calibration.get("method"), nominal_coverage=calibration.get("nominal_coverage"),
            radius_percentage_points=calibration.get("radius_percentage_points"),
            exploratory=calibration.get("exploratory"), coverage_guarantee=calibration.get("coverage_guarantee"),
            routing_gate=calibration.get("routing_gate"), limitations=calibration.get("limitations", []))
    else:
        metadata["confidence"] = dict(enabled=config["confidence_gate"], calibration_file=None)
    metadata_path = output_dir / "metadata.json"
    write_metadata(metadata_path, metadata)
    handles = []
    processes = []
    net = controller = None
    cpu_stop = threading.Event()
    cpu_thread = None
    sender_processes = []
    receiver_processes = []
    def log_handle(name):
        handle = (output_dir / name).open("x")
        handles.append(handle)
        return handle
    def terminate_signal(_number, _frame):
        raise KeyboardInterrupt("Research experiment interrupted")
    previous_sigterm = signal.signal(signal.SIGTERM, terminate_signal)
    try:
        preflight = subprocess.run([sys.executable, str(project_root / "experiments/preflight_model.py")],
                                   env=environment, capture_output=True, text=True, check=True)
        (output_dir / "model_preflight.json").write_text(preflight.stdout)
        controller = subprocess.Popen(["ryu-manager", "--ofp-tcp-listen-port", str(args.controller_port),
                                       str(project_root / "path_switch_controller.py")],
                                      cwd=str(project_root), env=environment,
                                      stdout=log_handle("controller.log"), stderr=subprocess.STDOUT)
        processes.append(controller)
        cpu_thread = threading.Thread(target=monitor_cpu, args=(controller.pid, output_dir / "controller_resources.csv", cpu_stop), daemon=True)
        cpu_thread.start()
        time.sleep(args.settle_seconds)
        if controller.poll() is not None:
            raise RuntimeError("Controller exited before network startup")
        net, hosts = build_network(args.controller_port, definition)
        time.sleep(args.settle_seconds)
        for flow in pairs:
            if net.ping([hosts[flow["source"]], hosts[flow["receiver"]]], timeout="2") != 0:
                raise RuntimeError("Initial host-pair connectivity check failed")
        probe_script = str(project_root / "experiments/transition_probe.py")
        # Upper limit is a failsafe. Probes are explicitly stopped after actual
        # workload completion and draining; segment connection overhead is logged.
        probe_duration = definition["duration_seconds"] + 30 * len(definition["workload"]) + 30
        for flow in pairs:
            source, receiver = hosts[flow["source"]], hosts[flow["receiver"]]
            fid = flow["flow_id"]
            server = receiver.popen(["iperf3", "-s", "-J", "-i", "1", "-p", str(flow["iperf_port"])],
                                    stdout=log_handle("iperf_server_flow_%02d.log" % fid), stderr=subprocess.STDOUT)
            processes.append(server)
            ping_name = "ping.log" if fid == 1 else "ping_flow_%02d.log" % fid
            ping = source.popen(["ping", "-D", "-i", str(PING_INTERVAL_SECONDS), receiver.IP()],
                               stdout=log_handle(ping_name), stderr=subprocess.STDOUT)
            processes.append(ping)
            ready_file = output_dir / ("probe_receiver_flow_%02d.ready" % fid)
            probe = receiver.popen([sys.executable, probe_script, "--role", "receiver", "--host", receiver.IP(),
                                    "--port", str(flow["probe_port"]), "--flow-id", str(fid),
                                    "--output", str(output_dir / ("probe_rx_flow_%02d.csv" % fid)),
                                    "--ready-file", str(ready_file), "--duration", str(probe_duration)],
                                   stdout=log_handle("probe_receiver_flow_%02d.log" % fid), stderr=subprocess.STDOUT)
            receiver_processes.append(probe)
            processes.append(probe)
        deadline = time.monotonic() + 5
        while not all((output_dir / ("probe_receiver_flow_%02d.ready" % flow["flow_id"])).exists() for flow in pairs):
            if time.monotonic() > deadline or any(process.poll() is not None for process in receiver_processes):
                raise RuntimeError("UDP probe receivers did not become ready")
            time.sleep(.05)
        workload_start = time.time() + 1
        for flow in pairs:
            fid = flow["flow_id"]
            probe = hosts[flow["source"]].popen([sys.executable, probe_script, "--role", "sender",
                "--host", hosts[flow["receiver"]].IP(), "--port", str(flow["probe_port"]),
                "--flow-id", str(fid), "--output", str(output_dir / ("probe_tx_flow_%02d.csv" % fid)),
                "--duration", str(probe_duration), "--start-at", str(workload_start),
                "--interval", str(PROBE_INTERVAL_SECONDS), "--payload-bytes", str(PROBE_PAYLOAD_BYTES)],
                stdout=log_handle("probe_sender_flow_%02d.log" % fid), stderr=subprocess.STDOUT)
            sender_processes.append(probe)
            processes.append(probe)
        metadata.update(status="running", workload_started_at=workload_start)
        write_metadata(metadata_path, metadata)
        time.sleep(max(0.0, workload_start - time.time()))
        interval_handle = log_handle("receiver_intervals.csv")
        interval_writer = csv.DictWriter(interval_handle, fieldnames=RECEIVER_INTERVAL_FIELDS)
        interval_writer.writeheader()
        segment_handle = log_handle("iperf_segments.csv")
        segment_writer = csv.DictWriter(segment_handle, fieldnames=SEGMENT_FIELDS)
        segment_writer.writeheader()
        for index, segment in enumerate(definition["workload"], 1):
            clients = []
            metadata["current_segment"] = segment["name"]
            metadata["current_segment_started_at"] = time.time()
            write_metadata(metadata_path, metadata)
            print("Segment %d/%d %s aggregate=%sMbps flows=%d" % (index, len(definition["workload"]), segment["name"], segment["rate_mbps"], len(pairs)), flush=True)
            for flow in pairs:
                rate = segment["rate_mbps"] * flow["rate_fraction"]
                filename = "iperf_flow_%02d_segment_%02d_%s.json" % (flow["flow_id"], index, segment["name"])
                result_handle = log_handle(filename)
                launched = time.time()
                client = hosts[flow["source"]].popen(["iperf3", "-c", hosts[flow["receiver"]].IP(),
                    "-u", "-b", "%.6fM" % rate, "-t", str(segment["duration_seconds"]),
                    "-l", "1200", "-i", "1", "-p", str(flow["iperf_port"]), "-J", "--get-server-output"],
                    stdout=result_handle, stderr=subprocess.STDOUT)
                processes.append(client)
                clients.append((client, result_handle, launched, flow, rate, filename))
            deadline = time.monotonic() + segment["duration_seconds"] + 30
            while any(client.poll() is None for client, *_rest in clients):
                if controller.poll() is not None:
                    raise RuntimeError("Controller exited during research workload")
                if any(process.poll() is not None for process in sender_processes + receiver_processes):
                    raise RuntimeError("Sequence-probe process exited during workload")
                if time.monotonic() > deadline:
                    raise RuntimeError("Concurrent iperf segment timed out")
                time.sleep(.1)
            finished = time.time()
            for client, handle, launched, flow, rate, filename in clients:
                handle.flush()
                if client.returncode != 0:
                    raise RuntimeError("iperf flow %d segment %s failed" % (flow["flow_id"], segment["name"]))
                payload = json.loads((output_dir / filename).read_text())
                interval_writer.writerows(receiver_intervals(payload, launched, flow, segment["name"]))
                segment_writer.writerow(dict(segment=segment["name"], flow_id=flow["flow_id"],
                    source=flow["source"], receiver=flow["receiver"], rate_mbps=rate,
                    duration_seconds=segment["duration_seconds"], launch_timestamp=launched,
                    finished_timestamp=finished, result_file=filename))
            interval_handle.flush()
            segment_handle.flush()
        metadata["workload_finished_at"] = time.time()
        write_metadata(metadata_path, metadata)
        # Continue both streams after the workload so a late transition still has
        # a full +2-second window, then drain queued packets before closing RX.
        time.sleep(max(2.1, args.settle_seconds))
        for process in sender_processes:
            _stop_process(process)
        time.sleep(.5)
        for process in receiver_processes:
            _stop_process(process)
        if any(process.returncode != 0 for process in sender_processes + receiver_processes):
            raise RuntimeError("Sequence-probe capture did not finish cleanly")
        if controller.poll() is not None:
            raise RuntimeError("Controller exited before quality validation")
        metadata["finished_at"] = time.time()
        metadata["finished_at_iso"] = datetime.now(timezone.utc).isoformat()
        metadata["quality_gate"] = check_research_run(output_dir, metadata)
        write_metadata(metadata_path, metadata)
        transition_result = summarize_transitions(output_dir)
        write_transition_metrics(transition_result, output_dir)
        metadata["transition_measurements"] = dict(reroutes=len(transition_result["reroutes"]),
            rows=len(transition_result["rows"]), complete_windows=sum(row["Window_Complete"] for row in transition_result["rows"]))
        metadata["status"] = "complete"
    except BaseException as error:
        metadata.update(finished_at=time.time(), finished_at_iso=datetime.now(timezone.utc).isoformat(),
                        status="cancelled" if isinstance(error, KeyboardInterrupt) else "failed",
                        error="%s: %s" % (type(error).__name__, error))
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)
        cpu_stop.set()
        cleanup_errors = []
        for process in reversed(processes):
            try:
                _stop_process(process)
            except Exception as error:
                cleanup_errors.append("process cleanup: " + str(error))
        if net is not None:
            try:
                net.stop()
            except Exception as error:
                cleanup_errors.append("network cleanup: " + str(error))
        if cpu_thread:
            cpu_thread.join(timeout=3)
        for handle in handles:
            handle.close()
        if cleanup_errors:
            metadata["cleanup_errors"] = cleanup_errors
            if metadata.get("status") == "complete":
                metadata.update(status="failed", error="; ".join(cleanup_errors))
        write_metadata(metadata_path, metadata)
    print("Completed research %s/%s -> %s" % (args.system, args.scenario, output_dir), flush=True)


if __name__ == "__main__":
    run()
