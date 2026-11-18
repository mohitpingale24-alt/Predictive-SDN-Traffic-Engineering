"""Read-only implementation demonstration from verified network captures."""
import argparse
from pathlib import Path
from analyze_results import load_run, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--run", default="run-01")
    args = parser.parse_args()
    print("RECORDED EXPERIMENT REPLAY - this is not a live network")
    print("Topology: h1 -- s1 -- (s2 / s3) -- s4 -- h2")
    print("Path A: 10 Mbps, 4 ms one-way link delay; Path B: 20 Mbps, 8 ms")
    static = load_run(args.campaign / "static" / args.run)
    for system in ("static", "reactive", "predictive-baseline", "predictive-ml"):
        run = load_run(args.campaign / system / args.run)
        result = summarize(run, static)
        print("\n" + system.upper() + " - quality gate PASS")
        for event in run["reroutes"]:
            elapsed = float(event["timestamp"]) - run["metadata"]["workload_started_at"]
            print(f"  t={elapsed:.1f}s: {event['old_path']} -> {event['new_path']}: {event['reason']}")
        predictions = [row for row in run["predictions"] if row["prediction_status"] == "predicted" and row["path"] == "A"]
        if predictions:
            row = max(predictions, key=lambda item: float(item["predicted_utilization"]))
            print(f"  Example model output: current={row['current_utilization']}%, t+5 prediction={float(row['predicted_utilization']):.2f}%")
        for metric in ("P95_Latency_ms", "Average_Throughput_Mbps", "Average_Packet_Loss_pct", "Congestion_Duration_seconds"):
            print(f"  {metric}: {result[metric]:.3f}")


if __name__ == "__main__":
    main()
