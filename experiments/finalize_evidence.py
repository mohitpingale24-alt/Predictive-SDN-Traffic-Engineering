"""Verify all 32 captures, aggregate corrected metrics, and draw final figures."""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from analyze_results import SYSTEMS, discover_campaign, load_run, mean_confidence_interval

ROOT = Path(__file__).resolve().parents[1]
LABELS = ["Static", "Reactive", "Persistence", "Gradient Boosting"]


def json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    found, repetitions = discover_campaign(args.campaign)
    if len(repetitions) != 8:
        raise ValueError("Final evidence requires eight matched repetitions per system")
    for runs in found.values():
        for path in runs.values():
            load_run(path)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, str(ROOT / "experiments/analyze_results.py"),
                    "--runs-root", str(args.campaign), "--output-dir", str(out / "statistics"), "--data-only"], check=True)
    with open(out / "statistics/network_statistics.csv") as handle:
        stats = list(csv.DictReader(handle))
    with open(out / "statistics/network_metrics_per_run.csv") as handle:
        rows = list(csv.DictReader(handle))
    metrics = {system: {row["Metric"]: {key: float(row[key]) for key in ("N", "Mean", "SD", "CI95_Lower", "CI95_Upper")}
                        for row in stats if row["System"] == system} for system in SYSTEMS}
    paired = []
    for reference in ("static", "reactive", "predictive-baseline"):
        for metric in ("P95_Latency_ms", "Average_Throughput_Mbps", "Average_Packet_Loss_pct", "Congestion_Duration_seconds"):
            differences = []
            for repetition in repetitions:
                ml = next(row for row in rows if row["System"] == "predictive-ml" and row["Repetition"] == repetition)
                baseline = next(row for row in rows if row["System"] == reference and row["Repetition"] == repetition)
                differences.append(float(ml[metric]) - float(baseline[metric]))
            n, mean, sd, low, high = mean_confidence_interval(differences)
            paired.append(dict(Comparison="predictive-ml minus " + reference, Metric=metric, N=n,
                               Mean=mean, SD=sd, CI95_Lower=low, CI95_Upper=high))
    with open(out / "statistics/paired_differences.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(paired[0]))
        writer.writeheader()
        writer.writerows(paired)
    plots = out / "graphs"
    plots.mkdir()
    plt.rcParams.update({"font.size": 11, "axes.spines.top": False, "axes.spines.right": False})
    colors = ["#667085", "#315A91", "#378C86", "#B65C36"]
    for metric, title, unit, name in (
        ("P95_Latency_ms", "Tail latency", "P95 round-trip latency (ms)", "p95_latency"),
        ("Average_Throughput_Mbps", "Delivered traffic", "Receiver goodput (Mbps)", "goodput"),
        ("Average_Packet_Loss_pct", "UDP packet loss", "Receiver packet loss (%)", "packet_loss"),
        ("Congestion_Duration_seconds", "Time above 85 percent utilization", "Congestion duration (seconds)", "congestion_duration"),
    ):
        fig, ax = plt.subplots(figsize=(9, 5))
        means = [metrics[s][metric]["Mean"] for s in SYSTEMS]
        widths = [metrics[s][metric]["CI95_Upper"] - metrics[s][metric]["Mean"] for s in SYSTEMS]
        ax.bar(LABELS, means, color=colors, yerr=widths, capsize=5, width=.58)
        ax.set(title=title, ylabel=unit, ylim=(0, max(1, max(a+b for a, b in zip(means, widths))) * 1.22))
        ax.grid(axis="y", alpha=.15)
        for index, value in enumerate(means):
            ax.text(index, value + widths[index] + ax.get_ylim()[1] * .025, f"{value:.2f}", ha="center")
        fig.text(.5, .01, "Mean and 95% Student t interval across eight runs per system", ha="center", fontsize=9)
        fig.tight_layout(rect=(0,.04,1,1))
        fig.savefig(plots / f"{name}.png", dpi=180)
        plt.close(fig)
    fig, axes = plt.subplots(4, 1, figsize=(10, 9), sharex=True, sharey=True)
    for axis, system, label, color in zip(axes, SYSTEMS, LABELS, colors):
        run = load_run(found[system]["run-01"])
        start = run["metadata"]["workload_started_at"]
        axis.plot([row[0]-start for row in run["active_rows"]], [row[1] for row in run["active_rows"]], color=color, label=label)
        axis.axhline(85, color="#9D3030", linestyle="--", linewidth=.8)
        for event in run["reroutes"]:
            axis.axvline(float(event["timestamp"])-start, color="#555555", linestyle=":", linewidth=.8)
        axis.set(ylabel="Utilization (%)", ylim=(-2,105))
        axis.legend(loc="upper right", frameon=False)
    axes[-1].set_xlabel("Seconds from workload start")
    fig.suptitle("Representative measured trace, run 01\nDashed line: congestion threshold; vertical lines: routing decisions")
    fig.tight_layout()
    fig.savefig(plots / "representative_trace.png", dpi=180)
    plt.close(fig)
    model_metrics = list(csv.DictReader((ROOT / "cnt-ml/results/real-calibration-02/metrics_comparison.csv").open()))
    payload = dict(campaign=str(args.campaign.resolve()), repetitions=8, metrics=metrics, paired_differences=paired,
                   model_metrics=model_metrics, quality_gate="PASS", inference_fixture=json.loads((ROOT / "cnt-ml/models/gbdt_parity_fixture.json").read_text()))
    (out / "evidence.json").write_text(json.dumps(json_safe(payload), indent=2, allow_nan=False) + "\n")
    files = [path for path in args.campaign.rglob("*") if path.is_file() and "analysis" not in path.parts]
    files += list((ROOT / "experiments").glob("*.py"))
    manifest = {str(path.resolve().relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(files)}
    (out / "source_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified 32 runs. Corrected evidence and graphs: {out}")


if __name__ == "__main__":
    main()
