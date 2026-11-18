"""Fail closed on missing receiver results or silently broken predictions."""
import csv
import json
import math


def receiver_summary(payload):
    if payload.get("error"):
        raise ValueError(payload["error"])
    server = payload.get("server_output_json", {})
    summary = server.get("end", {}).get("sum_received") or server.get("end", {}).get("sum")
    if not summary:
        summary = payload.get("end", {}).get("sum_received")
    if not summary or summary.get("sender") is True:
        raise ValueError("Missing explicit receiver-side iperf summary")
    summary = dict(summary)
    # iperf 3.7 on this VM returns zero bytes in the UDP server end summary
    # even though its receiver interval counters contain all received bytes.
    intervals = [item.get("sum", {}) for item in server.get("intervals", [])]
    if intervals and all(row.get("sender") is False and not row.get("omitted", False) for row in intervals):
        interval_seconds = sum(float(row["seconds"]) for row in intervals)
        if abs(interval_seconds - float(summary["seconds"])) > .1:
            raise ValueError("Receiver interval duration does not cover the complete segment")
        summary["bytes"] = sum(int(row["bytes"]) for row in intervals)
        summary["seconds"] = interval_seconds
    for key in ("bytes", "seconds", "packets", "lost_packets", "jitter_ms"):
        if key not in summary or not math.isfinite(float(summary[key])) or float(summary[key]) < 0:
            raise ValueError("Invalid receiver metric: " + key)
    if not summary["seconds"] or not summary["packets"]:
        raise ValueError("Empty receiver measurement")
    if not summary["bytes"] and summary["lost_packets"] < summary["packets"]:
        raise ValueError("Zero receiver bytes contradict received packet count")
    return summary


def check_run(directory, metadata):
    log = (directory / "controller.log").read_text(errors="replace")
    if any(marker in log for marker in ("Traceback (most recent call last)", "Prediction failed", "Error sending")):
        raise ValueError("Controller error found in log")
    files = list(directory.glob("iperf_*.json"))
    expected = len(metadata["workload"]) * metadata.get("repetitions", 1)
    if len(files) != expected:
        raise ValueError("Incomplete workload capture")
    for path in files:
        receiver_summary(json.loads(path.read_text()))
    with open(directory / "predictions.csv", newline="") as handle:
        rows = list(csv.DictReader(handle))
    eligible = [row for row in rows if float(row["timestamp"]) >= metadata["workload_started_at"] + 11]
    if not eligible:
        raise ValueError("No post-warmup telemetry")
    expected_seconds = metadata["finished_at"] - metadata["workload_started_at"] - 11
    for link in ("S1-S2", "S1-S3"):
        times = sorted({float(row["timestamp"]) for row in eligible if row["link_id"] == link})
        if len(times) < max(10, .95 * expected_seconds):
            raise ValueError("Insufficient temporal telemetry coverage: " + link)
        if any(b - a > 3 for a, b in zip(times, times[1:])):
            raise ValueError("Telemetry gap exceeds three seconds: " + link)
    if metadata["control_mode"] == "predictive":
        for link in ("S1-S2", "S1-S3"):
            subset = [row for row in eligible if row["link_id"] == link]
            valid = [row for row in subset if row["prediction_status"] == "predicted"
                     and math.isfinite(float(row["predicted_utilization"]))]
            if len(valid) < 10 or len(valid) / max(1, len(subset)) < .95:
                raise ValueError("Insufficient valid prediction coverage: " + link)
    return dict(status="PASS", receiver_segments=len(files), post_warmup_rows=len(eligible))
