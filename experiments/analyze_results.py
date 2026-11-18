#!/usr/bin/env python3
"""Aggregate the four controller experiments into network-level metrics."""

import argparse
import bisect
import csv
import json
import math
from pathlib import Path
import re
import statistics
try:
	from .quality_gate import check_run, receiver_summary
except ImportError:
	from quality_gate import check_run, receiver_summary


SYSTEMS = ("static", "reactive", "predictive-baseline", "predictive-ml")
PATH_LINK = {"A": "S1-S2", "B": "S1-S3"}
PING_PATTERN = re.compile(r"^\[(?P<timestamp>\d+(?:\.\d+)?)\].*time[=<](?P<latency>\d+(?:\.\d+)?)\s*ms")
SUMMARY_METRICS = (
	"Average_Latency_ms", "P95_Latency_ms", "Average_Throughput_Mbps",
	"Average_Packet_Loss_pct", "Average_Jitter_ms", "Congestion_Duration_seconds",
	"Congestion_Events", "Reroutes", "Successful_Reroutes", "False_Reroutes",
	"Congestion_Avoidance_Rate", "False_Reroute_Rate", "Prediction_to_Reroute_ms",
	"Mean_Inference_ms", "P95_Inference_ms", "Mean_Controller_CPU_pct",
	"Recovery_Reroutes", "Congestion_Relief_Rate",
)

# Two-sided 95% Student t critical values indexed by degrees of freedom. The
# normal approximation is sufficient after 30 independent repetitions.
T_CRITICAL_95 = {
	1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447,
	7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179,
	13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120, 17: 2.110,
	18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074,
	23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052,
	28: 2.048, 29: 2.045, 30: 2.042,
}


def percentile(values, percentage):
	if not values:
		return math.nan
	ordered = sorted(values)
	position = (len(ordered) - 1) * percentage / 100
	lower = math.floor(position)
	upper = math.ceil(position)
	if lower == upper:
		return ordered[lower]
	return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def read_csv(path):
	if not path.exists():
		return []
	with open(path, newline="", encoding="utf-8") as handle:
		return list(csv.DictReader(handle))


def read_ping(path):
	latencies = []
	if not path.exists():
		return latencies
	for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
		match = PING_PATTERN.search(line)
		if match:
			latencies.append(float(match.group("latency")))
	return latencies


def read_iperf(run_dir):
	summaries = []
	for path in sorted(run_dir.glob("iperf_*.json")):
		summaries.append(receiver_summary(json.loads(path.read_text(encoding="utf-8"))))
	if not summaries:
		raise ValueError("No receiver summaries")
	duration = sum(row["seconds"] for row in summaries)
	packets = sum(row["packets"] for row in summaries)
	return ([sum(row["bytes"] for row in summaries) * 8 / duration / 1e6],
	        [sum(row["jitter_ms"] * row["seconds"] for row in summaries) / duration],
	        [100 * sum(row["lost_packets"] for row in summaries) / packets])


def event_timeline(event_rows):
	events = []
	for row in event_rows:
		if row.get("event") in ("controller_start", "reroute") and row.get("new_path") in ("A", "B"):
			events.append((float(row["timestamp"]), row["new_path"]))
	return sorted(events)


def path_at(timestamp, timeline):
	if not timeline:
		return "A"
	times = [item[0] for item in timeline]
	index = bisect.bisect_right(times, timestamp) - 1
	return timeline[max(0, index)][1]


def active_utilization_rows(telemetry, timeline):
	active = []
	for row in telemetry:
		timestamp = float(row["timestamp"])
		active_path = path_at(timestamp, timeline)
		if row["link_id"] == PATH_LINK[active_path]:
			active.append((timestamp, float(row["utilization"]), active_path))
	return sorted(active)


def congestion_summary(active_rows, threshold=85.0):
	if not active_rows:
		return 0.0, 0
	intervals = [b[0] - a[0] for a, b in zip(active_rows, active_rows[1:]) if 0 < b[0] - a[0] <= 3]
	interval = statistics.median(intervals) if intervals else 1.0
	duration = sum(interval for _, utilization, _ in active_rows if utilization >= threshold)
	events = 0
	was_high = False
	for _, utilization, _ in active_rows:
		is_high = utilization >= threshold
		if is_high and not was_high:
			events += 1
		was_high = is_high
	return duration, events


def prediction_to_reroute_latencies(predictions, reroutes, threshold=85.0):
	latencies = []
	for event in reroutes:
		if event["old_path"] != "A" or event["new_path"] != "B":
			continue
		event_time = float(event["timestamp"])
		old_path = event["old_path"]
		prior = sorted(
			(
				(float(row["timestamp"]), float(row["signal_utilization"]))
				for row in predictions
				if row.get("signal_utilization") not in (None, "") and row.get("path") == old_path and event_time - 15 <= float(row["timestamp"]) <= event_time
			),
		)
		streak = []
		for timestamp, signal in prior:
			if signal >= threshold:
				streak.append(timestamp)
			else:
				streak = []
		if streak:
			latencies.append(max(0.0, event_time - streak[0]))
	return latencies


def nearest_window_has_congestion(static_rows, relative_start, horizon=5.0, threshold=85.0):
	return any(relative_start <= timestamp <= relative_start + horizon and util >= threshold for timestamp, util, _ in static_rows)


def post_reroute_stays_below(active_rows, relative_start, window=10.0, threshold=85.0):
	values = [util for timestamp, util, _ in active_rows if relative_start <= timestamp <= relative_start + window]
	return bool(values) and max(values) < threshold


def counterfactual_rates(run, static_run):
	reroutes = [event for event in run["reroutes"] if event["old_path"] == "A" and event["new_path"] == "B"]
	if not reroutes or static_run is None:
		return math.nan, math.nan, 0, 0
	run_start = float(run["metadata"]["workload_started_at"])
	static_start = float(static_run["metadata"]["workload_started_at"])
	static_relative = [(ts - static_start, util, path) for ts, util, path in static_run["active_rows"]]
	run_relative = [(ts - run_start, util, path) for ts, util, path in run["active_rows"]]
	prevented = 0
	false_reroutes = 0
	for event in reroutes:
		relative = float(event["timestamp"]) - run_start
		would_congest = nearest_window_has_congestion(static_relative, relative)
		if not would_congest:
			false_reroutes += 1
		elif post_reroute_stays_below(run_relative, relative):
			prevented += 1
	count = len(reroutes)
	return prevented / count, false_reroutes / count, prevented, false_reroutes


def mean_confidence_interval(values):
	"""Return n, mean, sample SD, and a two-sided 95% confidence interval."""
	clean = [float(value) for value in values if not math.isnan(float(value))]
	if not clean:
		return 0, math.nan, math.nan, math.nan, math.nan
	mean = statistics.mean(clean)
	if len(clean) == 1:
		return 1, mean, math.nan, math.nan, math.nan
	standard_deviation = statistics.stdev(clean)
	critical = T_CRITICAL_95.get(len(clean) - 1, 1.96)
	half_width = critical * standard_deviation / math.sqrt(len(clean))
	return len(clean), mean, standard_deviation, mean - half_width, mean + half_width


def episode_avoidance(run, static_run):
	"""Fraction of matched static high-load episodes with no observed congestion.

	This is a time-aligned experimental proxy, not a causal proof of prevention.
	"""
	if static_run is None:
		return math.nan
	start = static_run["metadata"]["workload_started_at"]
	episodes = []
	current = []
	for timestamp, utilization, _ in static_run["active_rows"]:
		if utilization >= 85:
			current.append(timestamp - start)
		elif current:
			episodes.append((current[0], current[-1] + 1))
			current = []
	if current:
		episodes.append((current[0], current[-1] + 1))
	if not episodes:
		return math.nan
	rows = [(timestamp - run["metadata"]["workload_started_at"], utilization) for timestamp, utilization, _ in run["active_rows"]]
	avoided = 0
	for first, last in episodes:
		values = [util for timestamp, util in rows if first <= timestamp < last]
		if values and max(values) < 85:
			avoided += 1
	return avoided / len(episodes)


def load_run(run_dir):
	metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
	if metadata.get("status") != "complete":
		raise ValueError(f"Run is not complete: {run_dir}")
	check_run(run_dir, metadata)
	telemetry = read_csv(run_dir / "telemetry.csv")
	if not telemetry:
		# A "complete" run with no telemetry rows has no measured
		# utilization at all; summarizing it would report a fabricated
		# zero congestion duration — a perfect score for a broken capture.
		raise ValueError(f"Run has no telemetry rows: {run_dir}")
	predictions = read_csv(run_dir / "predictions.csv")
	events = read_csv(run_dir / "control_events.csv")
	timeline = event_timeline(events)
	reroutes = [row for row in events if row.get("event") == "reroute"]
	return {
		"metadata": metadata,
		"telemetry": telemetry,
		"predictions": predictions,
		"events": events,
		"timeline": timeline,
		"reroutes": reroutes,
		"active_rows": active_utilization_rows(telemetry, timeline),
		"run_dir": run_dir,
	}


def summarize(run, static_run):
	latencies = read_ping(run["run_dir"] / "ping.log")
	throughputs, jitters, losses = read_iperf(run["run_dir"])
	duration, congestion_events = congestion_summary(run["active_rows"])
	resources = read_csv(run["run_dir"] / "controller_resources.csv")
	cpu_values = [float(row["cpu_percent"]) for row in resources if row.get("cpu_percent")]
	inference = [
		float(row["inference_time_ms"])
		for row in run["predictions"]
		if row.get("inference_time_ms") not in (None, "") and row.get("prediction_status") == "predicted"
	]
	reroute_latencies = prediction_to_reroute_latencies(run["predictions"], run["reroutes"]) if run["metadata"]["control_mode"] == "predictive" else []
	avoidance, false_rate, prevented, false_reroutes = counterfactual_rates(run, static_run)
	return {
		"System": run["metadata"]["system"],
		"Model": run["metadata"]["model"],
		"Average_Latency_ms": statistics.mean(latencies) if latencies else math.nan,
		"P95_Latency_ms": percentile(latencies, 95),
		"Average_Throughput_Mbps": statistics.mean(throughputs) if throughputs else math.nan,
		"Average_Packet_Loss_pct": statistics.mean(losses) if losses else math.nan,
		"Average_Jitter_ms": statistics.mean(jitters) if jitters else math.nan,
		"Congestion_Duration_seconds": duration,
		"Congestion_Events": congestion_events,
		"Reroutes": len(run["reroutes"]),
		"Congestion_Avoidance_Rate": episode_avoidance(run, static_run),
		"Congestion_Relief_Rate": avoidance,
		"Recovery_Reroutes": sum(event["old_path"] == "B" and event["new_path"] == "A" for event in run["reroutes"]),
		"Successful_Reroutes": prevented,
		"False_Reroute_Rate": false_rate,
		"False_Reroutes": false_reroutes,
		"Prediction_to_Reroute_ms": statistics.mean(reroute_latencies) * 1000 if reroute_latencies else math.nan,
		"Mean_Inference_ms": statistics.mean(inference) if inference else math.nan,
		"P95_Inference_ms": percentile(inference, 95),
		"Mean_Controller_CPU_pct": statistics.mean(cpu_values) if cpu_values else math.nan,
	}


def format_value(value):
	if isinstance(value, float):
		return "N/A" if math.isnan(value) else f"{value:.4f}"
	return str(value)


def discover_campaign(runs_root):
	"""Return matched repetition directories for each system."""
	discovered = {}
	for system in SYSTEMS:
		system_dir = runs_root / system
		# Retain compatibility with the original one-directory smoke suite.
		if (system_dir / "metadata.json").exists():
			discovered[system] = {"run-01": system_dir}
		else:
			discovered[system] = {
				path.name: path for path in sorted(system_dir.glob("run-*"))
				if (path / "metadata.json").exists()
			}
	if any(not runs for runs in discovered.values()):
		raise ValueError("Every system must contain at least one complete run")
	repetition_sets = {system: set(runs) for system, runs in discovered.items()}
	expected = repetition_sets["static"]
	if any(repetitions != expected for repetitions in repetition_sets.values()):
		raise ValueError(f"Systems do not have matched repetitions: {repetition_sets}")
	return discovered, sorted(expected)


def controlled_fields(metadata):
	sources = dict(metadata.get("repository", {}).get("source_sha256", {}))
	# Audited logging-only amendment: the missing-RTT branch now emits the
	# complete CSV record rather than raising. These two exact revisions share
	# the same model/feature/routing behavior. All other source hashes must match.
	if sources.get("path_switch_controller.py") == "3e3ab24d57f63a41d516389facf2ea9ed27ad17f2255e0a0b0e26942d1298377":
		sources["path_switch_controller.py"] = "5c188b217be411ad1f96f3aa7a3816d33e8c6353bb61080e72524b5858db807e"
	return {
		"path_a_capacity_mbps": metadata["path_a_capacity_mbps"],
		"path_b_capacity_mbps": metadata["path_b_capacity_mbps"],
		"topology": metadata["topology"],
		"workload": metadata["workload"],
		"congestion_threshold": metadata["congestion_threshold"],
		"recovery_threshold": metadata["recovery_threshold"],
		"source_sha256": sources,
		"environment": metadata.get("environment", {}),
	}


def validate_controlled_campaign(loaded):
	reference = controlled_fields(loaded["static"][0][1]["metadata"])
	for system, runs in loaded.items():
		for repetition, run in runs:
			if controlled_fields(run["metadata"]) != reference:
				raise ValueError(f"Uncontrolled experiment configuration: {system}/{repetition}")


def summarize_statistics(per_run_rows):
	statistics_rows = []
	for system in SYSTEMS:
		system_rows = [row for row in per_run_rows if row["System"] == system]
		for metric in SUMMARY_METRICS:
			n, mean, sd, lower, upper = mean_confidence_interval(
				[row[metric] for row in system_rows]
			)
			statistics_rows.append({
				"System": system,
				"Model": system_rows[0]["Model"],
				"Metric": metric,
				"N": n,
				"Mean": mean,
				"SD": sd,
				"CI95_Lower": lower,
				"CI95_Upper": upper,
			})
	return statistics_rows


def metric_cell(statistics_rows, system, metric):
	row = next(item for item in statistics_rows if item["System"] == system and item["Metric"] == metric)
	if math.isnan(row["Mean"]):
		return "N/A"
	if math.isnan(row["CI95_Lower"]):
		return f"{row['Mean']:.4f}"
	return f"{row['Mean']:.4f} [{row['CI95_Lower']:.4f}, {row['CI95_Upper']:.4f}]"


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--runs-root", required=True)
	parser.add_argument("--output-dir", required=True)
	parser.add_argument("--data-only", action="store_true", help="Write CSV data without a narrative report")
	args = parser.parse_args()
	runs_root = Path(args.runs_root).resolve()
	output_dir = Path(args.output_dir).resolve()
	output_dir.mkdir(parents=True, exist_ok=False)
	discovered, repetitions = discover_campaign(runs_root)
	loaded = {
		system: [(repetition, load_run(path)) for repetition, path in runs.items()]
		for system, runs in discovered.items()
	}
	validate_controlled_campaign(loaded)
	rows = []
	for repetition in repetitions:
		static_run = dict(loaded["static"])[repetition]
		for system in SYSTEMS:
			run = dict(loaded[system])[repetition]
			row = summarize(run, static_run if system != "static" else None)
			row = {"Repetition": repetition, **row}
			rows.append(row)
	statistics_rows = summarize_statistics(rows)
	fieldnames = list(rows[0])
	with open(output_dir / "network_metrics_per_run.csv", "w", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
		writer.writeheader()
		writer.writerows(rows)
	stat_fields = list(statistics_rows[0])
	with open(output_dir / "network_statistics.csv", "w", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=stat_fields, lineterminator="\n")
		writer.writeheader()
		writer.writerows(statistics_rows)
	if args.data_only:
		print(f"Saved {len(rows)} per-run observations and aggregate statistics -> {output_dir}")
		return

	lines = [
		"# Predictive SDN Network Experiment Results", "",
		f"Independent repetitions per system: **{len(repetitions)}**", "",
		"Values are mean [95% confidence interval].", "",
		"| Metric | Static | Reactive | Predictive Persistence | Predictive GB |",
		"| --- | ---: | ---: | ---: | ---: |",
	]
	labels = {
		"Average_Latency_ms": "Average latency (ms)",
		"P95_Latency_ms": "P95 latency (ms)",
		"Average_Throughput_Mbps": "Throughput (Mbps)",
		"Average_Packet_Loss_pct": "Packet loss (%)",
		"Average_Jitter_ms": "Jitter (ms)",
		"Congestion_Duration_seconds": "Congestion duration (s)",
		"Congestion_Events": "Congestion events",
		"Reroutes": "Reroutes",
		"Successful_Reroutes": "Successful reroutes",
		"Recovery_Reroutes": "Recovery reroutes",
		"Congestion_Relief_Rate": "Congestion relief rate",
		"False_Reroutes": "False reroutes",
		"Congestion_Avoidance_Rate": "Congestion avoidance rate",
		"Prediction_to_Reroute_ms": "Prediction to reroute (ms)",
		"Mean_Inference_ms": "Inference time (ms)",
		"Mean_Controller_CPU_pct": "Controller CPU (%)",
	}
	for metric, label in labels.items():
		values = [metric_cell(statistics_rows, system, metric) for system in SYSTEMS]
		lines.append("| " + " | ".join([label, *values]) + " |")
	lines.extend([
		"",
		"Full sample standard deviations and confidence bounds are in `network_statistics.csv`; individual observations are in `network_metrics_per_run.csv`.",
		"",
		"Congestion Avoidance Rate is the fraction of paired static congestion episodes with no high samples in the comparison run over the same workload-relative interval. Successful reroutes and Congestion Relief Rate measure A-to-B actions followed by ten seconds below threshold when the static reference is high within five seconds. Relief is not necessarily preemptive avoidance.",
		"",
		"False Reroute Rate considers A-to-B congestion actions only. B-to-A recovery actions are separate. Counterfactual metrics are time-aligned reference proxies, not causal estimates. Throughput uses receiver interval bytes when older iperf versions report zero server end bytes. Full semantics and limitations are in experiments/METRIC_DEFINITIONS.md.",
	])
	(output_dir / "network_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
	print(f"Saved {len(rows)} per-run observations and aggregate statistics -> {output_dir}")


if __name__ == "__main__":
	main()
