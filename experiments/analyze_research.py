#!/usr/bin/env python3
"""Read-only analysis of validated research1 captures, separate from campaign-02.

Counterfactual metrics require an explicitly paired static capture with the same
scenario, topology, repetition and controlled configuration. Concurrent goodput
uses total receiver bytes divided by the actual workload wall time.
"""

import argparse
from collections import OrderedDict, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics
import threading

try:
    from .analyze_results import (SUMMARY_METRICS, T_CRITICAL_95, active_utilization_rows,
                                 event_timeline)
    from .quality_gate import receiver_summary
    from .run_research_experiment import check_research_run
    from .scenarios import PRIMARY_SYSTEMS, ABLATION_SYSTEMS
    from .transition_metrics import (committed_reroutes, load_ping_rows, load_probe_rows,
                                     read_csv, window_metrics, percentile)
except ImportError:
    from analyze_results import (SUMMARY_METRICS, T_CRITICAL_95, active_utilization_rows,
                                 event_timeline)
    from quality_gate import receiver_summary
    from run_research_experiment import check_research_run
    from scenarios import PRIMARY_SYSTEMS, ABLATION_SYSTEMS
    from transition_metrics import (committed_reroutes, load_ping_rows, load_probe_rows,
                                     read_csv, window_metrics, percentile)

ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "static": "Static", "reactive": "Reactive", "predictive-baseline": "Persistence",
    "predictive-ml": "Predictive GB", "predictive-safe": "GB + confidence + safe routing",
    "predictive-confidence": "GB + confidence", "predictive-safe-only": "GB + safe routing",
}
METRICS = [
    ("Average_Latency_ms", "Average RTT", "ms"), ("P95_Latency_ms", "P95 RTT", "ms"),
    ("Average_Throughput_Mbps", "Receiver goodput", "Mbps"),
    ("Average_Packet_Loss_pct", "UDP packet loss", "%"),
    ("Average_Jitter_ms", "UDP receiver jitter", "ms"),
    ("Congestion_Duration_seconds", "Congestion duration", "s"),
    ("Congestion_Events", "Congestion events", ""), ("Reroutes", "Reroutes", ""),
    ("Recovery_Reroutes", "Recovery reroutes", ""),
    ("Successful_Reroutes", "Matched relief actions", ""),
    ("False_Reroutes", "Matched false reroutes", ""),
    ("Congestion_Avoidance_Rate", "Matched episode avoidance", "fraction"),
    ("Congestion_Relief_Rate", "Matched congestion relief", "fraction"),
    ("False_Reroute_Rate", "Matched false reroute rate", "fraction"),
    ("Prediction_to_Reroute_ms", "Forecast to reroute", "ms"),
    ("Mean_Inference_ms", "Mean inference", "ms"),
    ("P95_Inference_ms", "P95 inference", "ms"),
    ("Mean_Controller_CPU_pct", "Controller CPU", "%"),
    ("Transition_Probe_Loss_pct", "Transition probe datagram loss", "%"),
    ("Transition_P95_RTT_ms", "Transition P95 RTT", "ms"),
    ("Transition_RTT_Variation_ms", "Transition RTT variation", "ms"),
    ("Acknowledged_Commit_ms", "Acknowledged commit time", "ms"),
    ("Fallback_Decisions", "Measured fallback samples", ""),
    ("Blocked_Decisions", "Blocked decision samples", ""),
    ("Completed_Safe_Transitions", "Completed safe transitions", ""),
    ("Failed_Transitions", "Failed transition transactions", ""),
]
DEFINITIONS = {
    "Average_Latency_ms": "Mean successful ICMP round-trip replies across all flows during workload wall bounds.",
    "P95_Latency_ms": "Pooled interpolated P95 of successful ICMP round-trip replies; unreceived replies are excluded.",
    "Average_Throughput_Mbps": "All explicit receiver iperf bytes *8 / actual workload wall seconds /1e6; simultaneous flow durations are not added in the denominator.",
    "Average_Packet_Loss_pct": "100*total receiver lost packets /total receiver expected packets, pooled across flows and segments.",
    "Average_Jitter_ms": "Receiver UDP jitter summaries weighted by each flow-segment receiver duration; not RTT variability.",
    "Congestion_Duration_seconds": "Observed active-path high samples held for at most one configured sampling interval, truncated at the next sample and workload end; missing telemetry is not filled.",
    "Transition_Probe_Loss_pct": "Application datagram loss: unique successfully queued UDP sequence IDs in the union of complete [-2,+2] reroute windows minus unique matched deliveries before capture closes. It is not wire packet capture loss.",
    "Transition_P95_RTT_ms": "Pooled interpolated P95 successful ICMP replies in the union of complete reroute windows.",
    "Transition_RTT_Variation_ms": "Mean absolute consecutive RTT difference within each flow and contiguous complete transition window. It is not the UDP jitter estimator.",
    "Acknowledged_Commit_ms": "Mean transaction/started to commit/committed wall-clock duration, only where an acknowledgement audit record exists.",
    "Fallback_Decisions": "Count of logged decision samples with source measured_fallback; not a count of reroutes.",
    "Blocked_Decisions": "Count of logged decision samples with source blocked; not a count of rejected unique congestion episodes.",
    "Completed_Safe_Transitions": "Unique safe transaction IDs with transaction/complete after acknowledged route installation and retirement.",
    "Failed_Transitions": "Unique transaction IDs with failed, aborted, indeterminate or degraded audit status; a recovered rollback is still a failed attempted transition.",
    "Counterfactual": "Time-aligned paired static reference proxies, not causal guarantees. No matching static reference yields null false/relief/avoidance metrics.",
    "Statistics": "Means and sample SD across runs with equal controlled configuration. Two-sided Student t 95% intervals need at least two repetitions; n=1 has null SD and CI.",
}
_RUN_CACHE = OrderedDict()
_SUMMARY_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_CACHE_LIMIT = 64


def _finite(value):
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _mean(values):
    clean = [number for number in (_finite(value) for value in values) if number is not None]
    return statistics.mean(clean) if clean else None


def _congestion_window(rows, first, last, threshold=85.0, interval=1.0):
    duration, events = 0.0, 0
    was_high, previous = False, None
    rows = sorted(rows)
    for index, (timestamp, utilization, _path) in enumerate(rows):
        high = utilization >= threshold
        if high and (not was_high or (previous is not None and timestamp - previous > interval * 1.5)):
            events += 1
        if high:
            next_time = rows[index + 1][0] if index + 1 < len(rows) else last
            duration += max(0.0, min(last, next_time, timestamp + interval) - max(first, timestamp))
        was_high, previous = high, timestamp
    return duration, events


def _signature(directory):
    return tuple((path.name, path.stat().st_mtime_ns, path.stat().st_size)
                 for path in sorted(directory.iterdir()) if path.is_file())


def _cache_put(cache, key, value):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > _CACHE_LIMIT:
        cache.popitem(last=False)


def controlled_configuration(metadata):
    """Fields that must match before comparing research repetitions/systems."""
    common_controller = ("congestion_threshold_percent", "recovery_threshold_percent",
                         "minimum_improvement_percent", "consecutive_signals", "cooldown_seconds",
                         "routing_scope")
    common_measurement = ("probe_interval_seconds", "probe_payload_bytes", "ping_interval_seconds")
    return {
        "protocol_version": metadata.get("protocol_version"), "scenario": metadata.get("scenario"),
        "topology": metadata.get("topology"), "scenario_definition": metadata.get("scenario_definition"),
        "workload": metadata.get("workload"), "flows": metadata.get("flows"),
        "path_a_capacity_mbps": metadata.get("path_a_capacity_mbps"),
        "path_b_capacity_mbps": metadata.get("path_b_capacity_mbps"),
        "congestion_threshold": metadata.get("congestion_threshold"),
        "recovery_threshold": metadata.get("recovery_threshold"),
        "sampling_interval_seconds": metadata.get("sampling_interval_seconds"),
        "smoke": metadata.get("smoke", False),
        "controller": {key: metadata.get("controller", {}).get(key) for key in common_controller},
        "measurement": {key: metadata.get("measurement", {}).get(key) for key in common_measurement},
        "source_sha256": metadata.get("repository", {}).get("source_sha256", {}),
        "environment": metadata.get("environment", {}),
    }


def configuration_id(metadata):
    encoded = json.dumps(controlled_configuration(metadata), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _merge_windows(windows):
    merged = []
    for first, last in sorted(windows):
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
        else:
            merged.append((first, last))
    return merged


def _transition_summary(directory, metadata, pings):
    tx, rx = load_probe_rows(directory)
    expected_flows = [int(flow["flow_id"]) for flow in metadata["flows"]]
    probe_interval = float(metadata.get("measurement", {}).get("probe_interval_seconds", .02))
    intervals = read_csv(directory / "receiver_intervals.csv")
    first, last = float(metadata["workload_started_at"]), float(metadata["workload_finished_at"])
    events = [event for event in committed_reroutes(directory) if first <= event["timestamp"] <= last]
    windows = []
    for event in events:
        at = event["timestamp"]
        metrics = window_metrics(tx, rx, pings, intervals, at - 2, at + 2, expected_flows, probe_interval)
        if metrics["Window_Complete"]:
            windows.append((at - 2, at + 2))
    complete_windows = len(windows)
    windows = _merge_windows(windows)

    def inside(timestamp):
        return any(start <= timestamp < end for start, end in windows)

    queued = {(row["flow_id"], row["sequence"]) for row in tx if inside(row["tx_timestamp"])}
    delivered = {(row["flow_id"], row["sequence"]) for row in rx}
    rtts = [row["rtt_ms"] for row in pings if inside(row["timestamp"])]
    variation = []
    for start, end in windows:
        for flow in expected_flows:
            values = [row["rtt_ms"] for row in sorted(pings, key=lambda row: row["timestamp"])
                      if row["flow_id"] == flow and start <= row["timestamp"] < end]
            variation.extend(abs(right - left) for left, right in zip(values, values[1:]))
    return {
        "Transition_Probe_Loss_pct": 100 * len(queued - delivered) / len(queued) if queued else None,
        "Transition_P95_RTT_ms": percentile(rtts, 95),
        "Transition_RTT_Variation_ms": _mean(variation),
        "Transition_Complete_Windows": complete_windows,
        "Transition_Window_Probe_Queued": len(queued),
        "Transition_Window_Probe_Delivered": len(queued & delivered),
        "Transition_Window_Definition": "union of complete [-2,+2] seconds around reroute events; overlapping windows count each sequence once",
    }


def load_research_run(directory):
    """Reject incomplete, non-research, or failing captures; cache immutable reads."""
    directory = Path(directory).resolve()
    signature = _signature(directory)
    key = str(directory)
    with _CACHE_LOCK:
        cached = _RUN_CACHE.get(key)
        if cached and cached[0] == signature:
            _RUN_CACHE.move_to_end(key)
            return cached[1]
        metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
        if metadata.get("capture_schema") != "research1" or metadata.get("research_schema") != 1:
            raise ValueError("Not a research1 capture: " + str(directory))
        if metadata.get("status") != "complete":
            raise ValueError("Research capture is not complete: " + str(directory))
        if metadata.get("quality_gate", {}).get("status") != "PASS":
            raise ValueError("Research capture did not pass recorded quality validation")
        start, end = _finite(metadata.get("workload_started_at")), _finite(metadata.get("workload_finished_at"))
        if start is None or end is None or end <= start:
            raise ValueError("Invalid research workload wall-time bounds")
        if metadata.get("system") not in LABELS:
            raise ValueError("Unknown research controller system")
        if not isinstance(metadata.get("repetition"), int) or isinstance(metadata["repetition"], bool) or metadata["repetition"] < 1:
            raise ValueError("Invalid research repetition index")
        quality = check_research_run(directory, metadata)
        if quality.get("status") != "PASS":
            raise ValueError("Research capture failed current quality validation")
        telemetry = read_csv(directory / "telemetry.csv")
        events = read_csv(directory / "control_events.csv")
        timeline = event_timeline(events)
        active_rows = [row for row in active_utilization_rows(telemetry, timeline) if start <= row[0] <= end]
        pings = [row for row in load_ping_rows(directory, metadata) if start <= row["timestamp"] <= end]
        segments = read_csv(directory / "iperf_segments.csv")
        results = []
        for segment in segments:
            filename = segment["result_file"]
            if Path(filename).name != filename:
                raise ValueError("Receiver filename must stay within its capture directory")
            results.append(receiver_summary(json.loads((directory / filename).read_text(encoding="utf-8"))))
        transitions = read_csv(directory / "transitions.csv")
        run = {
            "run_dir": directory, "metadata": metadata, "quality": quality,
            "telemetry": telemetry, "events": events, "timeline": timeline,
            "active_rows": active_rows, "pings": pings, "receiver_results": results,
            "predictions": read_csv(directory / "predictions.csv"),
            "decisions": read_csv(directory / "decisions.csv"), "transitions": transitions,
            "resources": read_csv(directory / "controller_resources.csv"),
            "reroutes": [row for row in events if row.get("event") == "reroute"
                         and start <= float(row["timestamp"]) <= end],
            "transition_summary": _transition_summary(directory, metadata,
                                                       load_ping_rows(directory, metadata)),
            "signature": signature,
        }
        if _signature(directory) != signature:
            raise ValueError("Completed research capture changed during validation")
        _cache_put(_RUN_CACHE, key, (signature, run))
        return run


def _has_window(rows, first, last, interval=1.0):
    times = sorted({timestamp for timestamp, _util, _path in rows if first <= timestamp <= last})
    return bool(times) and times[0] <= first + interval and times[-1] >= last - interval and not any(
        right - left > 2.1 * interval for left, right in zip(times, times[1:]))


def _counterfactual(run, reference):
    result = {key: None for key in ("Successful_Reroutes", "False_Reroutes", "False_Reroute_Rate",
                                    "Congestion_Avoidance_Rate", "Congestion_Relief_Rate")}
    result["Reference_Status"] = "no_paired_static_reference"
    if reference is None:
        return result
    meta, ref_meta = run["metadata"], reference["metadata"]
    if (ref_meta["system"] != "static" or meta["repetition"] != ref_meta["repetition"]
            or controlled_configuration(meta) != controlled_configuration(ref_meta)):
        result["Reference_Status"] = "static_reference_configuration_mismatch"
        return result
    result["Reference_Status"] = "paired_static_time_alignment_proxy"
    run_rows = [(ts - meta["workload_started_at"], util, path) for ts, util, path in run["active_rows"]]
    ref_rows = [(ts - ref_meta["workload_started_at"], util, path) for ts, util, path in reference["active_rows"]]
    threshold = float(meta.get("congestion_threshold", 85))
    actions = [event for event in run["reroutes"] if event["old_path"] == "A" and event["new_path"] == "B"]
    if not actions:
        result.update(Successful_Reroutes=0, False_Reroutes=0)
    else:
        false, relief, evaluable = 0, 0, True
        for event in actions:
            at = float(event["timestamp"]) - meta["workload_started_at"]
            if not _has_window(ref_rows, at, at + 5) or not _has_window(run_rows, at, at + 10):
                evaluable = False
                break
            would_congest = any(at <= ts <= at + 5 and util >= threshold for ts, util, _path in ref_rows)
            stayed_low = all(util < threshold for ts, util, _path in run_rows if at <= ts <= at + 10)
            false += int(not would_congest)
            relief += int(would_congest and stayed_low)
        if evaluable:
            result.update(Successful_Reroutes=relief, False_Reroutes=false,
                          False_Reroute_Rate=false / len(actions), Congestion_Relief_Rate=relief / len(actions))
        else:
            result["Reference_Status"] = "paired_static_proxy_incomplete_action_window"
    episodes, current = [], []
    for timestamp, utilization, _path in ref_rows:
        if utilization >= threshold:
            current.append(timestamp)
        elif current:
            episodes.append((current[0], current[-1] + 1))
            current = []
    if current:
        episodes.append((current[0], current[-1] + 1))
    if episodes and all(_has_window(run_rows, first, last) for first, last in episodes):
        avoided = sum(all(util < threshold for ts, util, _path in run_rows if first <= ts < last)
                      for first, last in episodes)
        result["Congestion_Avoidance_Rate"] = avoided / len(episodes)
    return result


def _transition_audit(run):
    rows = run["transitions"]
    first, last = run["metadata"]["workload_started_at"], run["metadata"]["workload_finished_at"]
    grouped = defaultdict(list)
    for row in rows:
        if row.get("transition_id"):
            grouped[row["transition_id"]].append(row)
    durations, complete, failed = [], 0, 0
    for _identity, audit in grouped.items():
        starts = [float(row["timestamp"]) for row in audit
                  if row.get("stage") == "transaction" and row.get("status") == "started"]
        if not starts or not first <= min(starts) <= last:
            continue
        started = min(starts)
        commits = [float(row["timestamp"]) for row in audit if row.get("stage") == "commit"
                   and row.get("status") == "committed"]
        if commits and min(commits) >= started:
            durations.append((min(commits) - started) * 1000)
        complete += int(bool(commits) and any(row.get("stage") == "transaction" and row.get("status") == "complete" for row in audit))
        failed += int(any(row.get("status") in ("failed", "aborted", "indeterminate", "degraded") for row in audit))
    return {"Acknowledged_Commit_ms": _mean(durations),
            "Completed_Safe_Transitions": complete if run["metadata"]["controller"].get("safe_reroute") else 0,
            "Failed_Transitions": failed}


def _prediction_latency(run):
    if not run["metadata"]["system"].startswith("predictive"):
        return None
    # The proposed confidence route currently uses measured fallback. Calling
    # that measurement-to-action delay a prediction delay would be misleading.
    if run["metadata"]["controller"].get("confidence_gate"):
        decisions = run["decisions"]
        if not any(row.get("source") == "learned_confidence" for row in decisions):
            return None
    else:
        decisions = run["predictions"]
    values = []
    threshold = float(run["metadata"].get("congestion_threshold", 85))
    for event in run["reroutes"]:
        if event["old_path"] != "A" or event["new_path"] != "B":
            continue
        at = float(event["timestamp"])
        signals = []
        for row in decisions:
            timestamp = _finite(row.get("timestamp"))
            if timestamp is None or not at - 15 <= timestamp <= at:
                continue
            if run["metadata"]["controller"].get("confidence_gate"):
                if row.get("active_path") == "A":
                    signal = _finite(row.get("signal_a")) if row.get("source") == "learned_confidence" else None
                    signals.append((timestamp, signal))
            elif row.get("path") == "A" and row.get("prediction_status") == "predicted":
                signals.append((timestamp, _finite(row.get("signal_utilization"))))
        streak = []
        for timestamp, signal in sorted(signals):
            if signal is not None and signal >= threshold:
                streak.append(timestamp)
            else:
                streak = []
        if streak:
            values.append((at - streak[0]) * 1000)
    return _mean(values)


def summarize_research(directory, reference=None):
    """Return baseline-compatible metrics and research measurements, with null NA."""
    run = load_research_run(directory)
    ref = load_research_run(reference) if reference is not None else None
    key = (str(run["run_dir"]), run["signature"],
           str(ref["run_dir"]) if ref else None, ref["signature"] if ref else None)
    with _CACHE_LOCK:
        if key in _SUMMARY_CACHE:
            _SUMMARY_CACHE.move_to_end(key)
            return dict(_SUMMARY_CACHE[key])
        meta = run["metadata"]
        seconds = meta["workload_finished_at"] - meta["workload_started_at"]
        receiver = run["receiver_results"]
        packet_count = sum(float(row["packets"]) for row in receiver)
        flow_seconds = sum(float(row["seconds"]) for row in receiver)
        rtt = [row["rtt_ms"] for row in run["pings"]]
        congestion_duration, congestion_events = _congestion_window(
            run["active_rows"], meta["workload_started_at"], meta["workload_finished_at"],
            float(meta.get("congestion_threshold", 85)), float(meta.get("sampling_interval_seconds", 1)))
        first, last = meta["workload_started_at"], meta["workload_finished_at"]
        predictions = [row for row in run["predictions"] if first <= float(row["timestamp"]) <= last]
        decisions = [row for row in run["decisions"] if first <= float(row["timestamp"]) <= last]
        inference = [row.get("inference_time_ms") for row in predictions if row.get("prediction_status") == "predicted"]
        inference = [value for value in (_finite(value) for value in inference) if value is not None]
        cpu = [row.get("cpu_percent") for row in run["resources"] if first <= float(row["timestamp"]) <= last]
        result = {key: None for key in SUMMARY_METRICS}
        result.update(
            System=meta["system"], Model=meta["model"], Scenario=meta["scenario"], Topology=meta["topology"],
            Repetition=meta["repetition"], Smoke=bool(meta.get("smoke", False)),
            Configuration_ID=configuration_id(meta), Workload_Wall_Seconds=seconds,
            Average_Latency_ms=_mean(rtt), P95_Latency_ms=percentile(rtt, 95),
            Average_Throughput_Mbps=sum(float(row["bytes"]) for row in receiver) * 8 / seconds / 1e6,
            Average_Packet_Loss_pct=100 * sum(float(row["lost_packets"]) for row in receiver) / packet_count if packet_count else None,
            Average_Jitter_ms=sum(float(row["jitter_ms"]) * float(row["seconds"]) for row in receiver) / flow_seconds if flow_seconds else None,
            Congestion_Duration_seconds=congestion_duration, Congestion_Events=congestion_events,
            Reroutes=len(run["reroutes"]),
            Recovery_Reroutes=sum(row.get("old_path") == "B" and row.get("new_path") == "A" for row in run["reroutes"]),
            Prediction_to_Reroute_ms=_prediction_latency(run),
            Mean_Inference_ms=_mean(inference) if meta["control_mode"] == "predictive" else None,
            P95_Inference_ms=percentile(inference, 95) if meta["control_mode"] == "predictive" else None,
            Mean_Controller_CPU_pct=_mean(cpu),
            Fallback_Decisions=sum(row.get("source") == "measured_fallback" for row in decisions),
            Blocked_Decisions=sum(row.get("source") == "blocked" for row in decisions),
        )
        result.update(_counterfactual(run, ref))
        result.update(_transition_audit(run))
        result.update(run["transition_summary"])
        _cache_put(_SUMMARY_CACHE, key, result)
        return dict(result)


def mean_confidence_interval(values):
    clean = [value for value in (_finite(value) for value in values) if value is not None]
    if not clean:
        return 0, None, None, None, None
    mean = statistics.mean(clean)
    if len(clean) < 2:
        return 1, mean, None, None, None
    sd = statistics.stdev(clean)
    degrees = len(clean) - 1
    critical = T_CRITICAL_95.get(degrees)
    if critical is None:
        # Cornish-Fisher expansion for df>30, retaining finite Student-t
        # corrections instead of silently switching to a normal interval.
        z = 1.959963984540054
        critical = (z + (z ** 3 + z) / (4 * degrees)
                    + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96 * degrees ** 2)
                    + (3 * z ** 7 + 19 * z ** 5 + 17 * z ** 3 - 15 * z) / (384 * degrees ** 3))
    half = critical * sd / math.sqrt(len(clean))
    return len(clean), mean, sd, mean - half, mean + half


def discover_research(sources):
    """Read only explicit source roots; baseline or incomplete captures are skipped."""
    found = set()
    for source in sources:
        source = Path(source).resolve()
        paths = [source / "metadata.json"] if (source / "metadata.json").exists() else sorted(source.rglob("metadata.json"))
        for path in paths:
            try:
                metadata = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if metadata.get("capture_schema") == "research1" and metadata.get("research_schema") == 1:
                found.add(path.parent)
    return sorted(found)


def _catalog(directories):
    loaded, issues, excluded = [], [], []
    for directory in directories:
        try:
            metadata = json.loads((directory / "metadata.json").read_text())
            if metadata.get("status") != "complete":
                excluded.append({"id": directory.parent.name, "status": metadata.get("status", "unknown")})
                continue
            loaded.append(load_research_run(directory))
        except (OSError, ValueError, KeyError, TypeError) as error:
            issues.append({"id": directory.parent.name, "error": str(error)})
    grouped = defaultdict(list)
    for run in loaded:
        grouped[(run["metadata"]["scenario"], run["metadata"]["topology"], configuration_id(run["metadata"]))].append(run)
    variant_counts = defaultdict(int)
    for scenario, topology, _config in grouped:
        variant_counts[(scenario, topology)] += 1
    groups = []
    for (scenario, topology, config), runs in sorted(grouped.items()):
        references = defaultdict(list)
        for run in runs:
            if run["metadata"]["system"] == "static":
                references[run["metadata"]["repetition"]].append(run)
        rows, entries, used = [], [], set()
        for run in sorted(runs, key=lambda row: (row["metadata"]["system"], row["metadata"]["repetition"], str(row["run_dir"]))):
            repetition = run["metadata"]["repetition"]
            system = run["metadata"]["system"]
            identity = (system, repetition)
            if identity in used:
                issues.append({"id": run["run_dir"].parent.name, "error": "Duplicate system/repetition in one controlled group; excluded from aggregation"})
                continue
            used.add(identity)
            matches = references.get(repetition, [])
            ref = matches[0]["run_dir"] if len(matches) == 1 and system != "static" else None
            summary = summarize_research(run["run_dir"], ref)
            rows.append(summary)
            entries.append({"id": run["run_dir"].parent.name, "system": system, "status": "verified",
                            "repetition": repetition, "summary": summary})
        systems = list(PRIMARY_SYSTEMS) + [system for system in ABLATION_SYSTEMS if any(row["System"] == system for row in rows)]
        stats = []
        for system in systems:
            subset = [row for row in rows if row["System"] == system]
            for metric, _label, _unit in METRICS:
                n, mean, sd, low, high = mean_confidence_interval([row[metric] for row in subset])
                stats.append({"system": system, "metric": metric, "n": n, "mean": mean, "sd": sd, "low": low, "high": high})
        identity = scenario + "/" + topology
        if variant_counts[(scenario, topology)] > 1:
            identity += "/" + config[:8]
        groups.append({"id": identity, "scenario": scenario, "topology": topology,
                       "configuration_id": config, "smoke": bool(runs[0]["metadata"].get("smoke", False)),
                       "runs": entries, "stats": stats, "metrics": METRICS, "labels": LABELS,
                       "systems": systems, "per_run": rows})
    return {"groups": groups, "issues": issues, "excluded": excluded,
            "verified": len(loaded), "definitions": DEFINITIONS,
            "scope": "validated research1 captures; original campaign is separate"}


def research_catalog():
    """Dashboard catalog: only research captures directly under local sessions."""
    directories = []
    for path in sorted((ROOT / "live-experiments").glob("*/capture/metadata.json")):
        try:
            metadata = json.loads(path.read_text())
            if metadata.get("capture_schema") == "research1" and metadata.get("research_schema") == 1:
                directories.append(path.parent)
        except (OSError, ValueError):
            continue
    return _catalog(directories)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True,
                        help="Explicit research capture or directory root; repeat for several roots")
    parser.add_argument("--output-dir", type=Path, help="New directory for JSON and CSV evidence")
    args = parser.parse_args()
    result = _catalog(discover_research(args.source))
    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=False)
        (args.output_dir / "research_results.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        rows = [row for group in result["groups"] for row in group["per_run"]]
        stats = [{"Group": group["id"], **row} for group in result["groups"] for row in group["stats"]]
        for name, values in (("research_metrics_per_run.csv", rows), ("research_statistics.csv", stats)):
            if values:
                with (args.output_dir / name).open("x", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(values[0]), lineterminator="\n")
                    writer.writeheader()
                    writer.writerows(values)
        print(json.dumps({"verified": result["verified"], "groups": len(result["groups"]),
                          "issues": result["issues"], "output_dir": str(args.output_dir)}, allow_nan=False))
    else:
        print(json.dumps(result, indent=2, allow_nan=False))
    if result["issues"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
