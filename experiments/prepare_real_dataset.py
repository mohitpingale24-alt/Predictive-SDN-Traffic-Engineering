#!/usr/bin/env python3
"""Prepare an audited, one-second real-telemetry training candidate."""

import argparse
import bisect
import csv
import hashlib
import json
from pathlib import Path
import re
import statistics

CANDIDATE_FIELDS = [
	"timestamp", "link_id", "utilization", "throughput", "delay",
	"packet_loss", "active_flows",
]
PING_PATTERN = re.compile(
	r"^\[(?P<timestamp>\d+(?:\.\d+)?)\].*time[=<](?P<delay>\d+(?:\.\d+)?)\s*ms"
)


def numeric_mean(rows, field):
	return statistics.mean(float(row[field]) for row in rows)


def sha256_file(path):
	digest = hashlib.sha256()
	with open(path, "rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def read_ping(path):
	measurements = []
	for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
		match = PING_PATTERN.search(line)
		if match:
			measurements.append((float(match.group("timestamp")), float(match.group("delay"))))
	return sorted(measurements)


def nearest_ping(timestamp, measurements, tolerance_seconds=1.5):
	timestamps = [item[0] for item in measurements]
	position = bisect.bisect_left(timestamps, timestamp)
	candidates = measurements[max(0, position - 1):position + 1]
	if not candidates:
		return None
	nearest = min(candidates, key=lambda item: abs(item[0] - timestamp))
	return nearest[1] if abs(nearest[0] - timestamp) <= tolerance_seconds else None


def read_iperf_intervals(run_dir):
	intervals = []
	for path in sorted(run_dir.glob("iperf_r*.json")):
		payload = json.loads(path.read_text(encoding="utf-8"))
		start = float(payload["start"]["timestamp"]["timesecs"])
		summary = payload.get("end", {}).get("sum", {}) or payload.get("end", {}).get("sum_received", {})
		intervals.append({
			"start": start,
			"end": start + float(summary["seconds"]),
			"loss_percent": float(summary["lost_percent"]),
			"jitter_ms": float(summary["jitter_ms"]),
			"file": path.name,
			"segment": path.stem.split("_", 3)[-1],
		})
	return intervals


def iperf_at(timestamp, intervals):
	return next((item for item in intervals if item["start"] <= timestamp <= item["end"]), None)


def sampling_gap_report(rows, intervals, control_events, expected_interval=1.0):
	links = {}
	for row in rows:
		links.setdefault(row["link_id"], []).append(float(row["timestamp"]))
	grouped = {}
	interval_counts = {}
	for link_id, timestamps in sorted(links.items()):
		timestamps = sorted(set(timestamps))
		counts = {}
		for previous, current in zip(timestamps, timestamps[1:]):
			delta = round(current - previous, 6)
			counts[str(delta)] = counts.get(str(delta), 0) + 1
			if delta > expected_interval + 0.5:
				grouped.setdefault((previous, current), []).append(link_id)
		interval_counts[link_id] = counts

	gaps = []
	for (previous, current), affected_links in sorted(grouped.items()):
		traffic = iperf_at(current, intervals) or iperf_at(previous, intervals)
		gaps.append({
			"previous_timestamp": previous,
			"current_timestamp": current,
			"expected_interval_seconds": expected_interval,
			"actual_interval_seconds": current - previous,
			"missing_duration_seconds": current - previous - expected_interval,
			"links": sorted(affected_links),
			"workload_segment": traffic["segment"] if traffic else "outside_iperf_segment",
			"iperf_file": traffic["file"] if traffic else None,
			"nearby_control_events": [
				{
					"timestamp": float(event["timestamp"]), "event": event["event"],
					"old_path": event.get("old_path", ""), "new_path": event.get("new_path", ""),
				}
				for event in control_events
				if previous - 2 <= float(event["timestamp"]) <= current + 2
			],
		})
	return {
		"expected_interval_seconds": expected_interval,
		"link_interval_counts": interval_counts,
		"unique_gap_count": len(gaps),
		"gaps": gaps,
		"handling_policy": (
			"Preserve observed timestamps. Do not interpolate. Future t+5 targets must be "
			"joined by timestamp, never by row offset."
		),
	}


def main():
	parser = argparse.ArgumentParser()
	parser.add_argument("--run-dir", required=True)
	parser.add_argument("--output", required=True)
	args = parser.parse_args()
	run_dir = Path(args.run_dir).resolve()
	output = Path(args.output).resolve()
	if output.exists():
		raise SystemExit(f"Refusing to overwrite existing output: {output}")

	metadata_path = run_dir / "metadata.json"
	telemetry_path = run_dir / "telemetry.csv"
	ping_path = run_dir / "ping.log"
	events_path = run_dir / "control_events.csv"
	metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
	if metadata.get("status") != "complete":
		raise SystemExit("The source run is not marked complete")
	with open(telemetry_path, newline="", encoding="utf-8") as handle:
		rows = list(csv.DictReader(handle))
	if not rows:
		raise SystemExit("The source telemetry.csv is empty")
	ping_measurements = read_ping(ping_path)
	iperf_intervals = read_iperf_intervals(run_dir)
	if not ping_measurements or not iperf_intervals:
		raise SystemExit("Timestamped ping and valid iperf3 measurements are required")
	with open(events_path, newline="", encoding="utf-8") as handle:
		control_events = list(csv.DictReader(handle))

	start = min(float(row["timestamp"]) for row in rows)
	buckets = {}
	for row in rows:
		second = int(round(float(row["timestamp"]) - start))
		buckets.setdefault((second, row["link_id"]), []).append(row)

	normalized = []
	loss_from_iperf = 0
	excluded_no_ping = []
	for (timestamp, link_id), bucket in sorted(buckets.items()):
		absolute_timestamp = numeric_mean(bucket, "timestamp")
		delay = nearest_ping(absolute_timestamp, ping_measurements)
		if delay is None:
			excluded_no_ping.append({
				"normalized_timestamp": timestamp,
				"absolute_timestamp": absolute_timestamp,
				"link_id": link_id,
				"reason": "no ping RTT within 1.5 seconds",
			})
			continue
		traffic = iperf_at(absolute_timestamp, iperf_intervals)
		packet_loss = traffic["loss_percent"] if traffic else numeric_mean(bucket, "packet_loss")
		loss_from_iperf += int(traffic is not None)
		normalized.append({
			"timestamp": timestamp,
			"link_id": link_id,
			"utilization": round(numeric_mean(bucket, "utilization"), 3),
			"throughput": round(numeric_mean(bucket, "throughput"), 3),
			"delay": round(delay, 3),
			"packet_loss": round(packet_loss, 6),
			"active_flows": int(round(numeric_mean(bucket, "active_flows"))),
		})

	output.parent.mkdir(parents=True, exist_ok=True)
	with open(output, "x", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=CANDIDATE_FIELDS, lineterminator="\n")
		writer.writeheader()
		writer.writerows(normalized)
	gap_path = run_dir / "sampling_gaps.json"
	gap_path.write_text(
		json.dumps(sampling_gap_report(rows, iperf_intervals, control_events), indent=2) + "\n",
		encoding="utf-8",
	)
	provenance = {
		"experiment_id": metadata["experiment_id"],
		"source_run": str(run_dir),
		"source_system": metadata["system"],
		"source_model": metadata["model"],
		"source_rows": len(rows),
		"normalized_rows": len(normalized),
		"timestamp_origin": start,
		"sampling_interval_seconds": metadata["sampling_interval_seconds"],
		"prediction_horizon_seconds": metadata["prediction_horizon_seconds"],
		"measurement_sources": {
			"utilization": "OpenFlow 1.3 port byte-counter deltas",
			"throughput": "OpenFlow 1.3 port byte-counter deltas",
			"delay": "nearest timestamped end-to-end ICMP RTT from ping.log",
			"packet_loss": "iperf3 UDP lost_percent during traffic; OpenFlow drops outside traffic",
			"active_flows": "OpenFlow 1.3 flow statistics",
		},
		"measurement_coverage": {
			"delay_rows_from_ping": len(normalized),
			"packet_loss_rows_from_iperf": loss_from_iperf,
			"excluded_rows_without_nearby_ping": len(excluded_no_ping),
			"excluded_without_nearby_ping": excluded_no_ping,
			"ping_match_tolerance_seconds": 1.5,
		},
		"feature_policy": {
			"included": [field for field in CANDIDATE_FIELDS if field not in ("timestamp", "link_id")],
			"excluded": ["queue_occupancy"],
			"exclusion_reason": "OpenFlow provides no queue occupancy; placeholders cannot enter training.",
		},
		"sampling_gap_report": gap_path.name,
		"environment": metadata["environment"],
		"topology": metadata["topology"],
		"controller": metadata["controller"],
		"traffic": metadata["traffic"],
		"repository": metadata["repository"],
		"dataset_preparation": {
			"script": Path(__file__).name,
			"script_sha256": sha256_file(Path(__file__).resolve()),
			"rtt_policy": "exclude rows with no ping RTT within 1.5 seconds; never impute",
			"target_policy": "downstream labels must match exact timestamp t+5 seconds",
		},
		"sha256": {
			"metadata.json": sha256_file(metadata_path),
			"telemetry.csv": sha256_file(telemetry_path),
			"ping.log": sha256_file(ping_path),
			"control_events.csv": sha256_file(events_path),
			"sampling_gaps.json": sha256_file(gap_path),
			output.name: sha256_file(output),
		},
		"limitations": [
			"Delay is end-to-end RTT shared across link rows, not one-way per-link delay.",
			"iperf3 loss is a measured segment-level value repeated inside that segment.",
			"Telemetry rows without a ping RTT within 1.5 seconds are excluded, never imputed.",
			"Queue occupancy is intentionally excluded from the candidate and model features.",
		],
	}
	output.with_suffix(output.suffix + ".provenance.json").write_text(
		json.dumps(provenance, indent=2) + "\n", encoding="utf-8",
	)
	print(
		f"Prepared {len(normalized)} audited rows -> {output}; "
		f"excluded {len(excluded_no_ping)} row(s) without nearby ping RTT"
	)


if __name__ == "__main__":
	main()
