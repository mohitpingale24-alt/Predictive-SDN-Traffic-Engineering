"""Auto-generate a campaign report from captured run directories.

Reads only what the runs actually recorded (telemetry, control events,
decisions, failures, drift and explanation logs) and computes every number
in the report from those rows — means with 95% confidence intervals, paired
differences against a reference system, failure-recovery times, drift status
and the most common reroute explanations. Usage:

    python3 -m experiments.generate_campaign_report CAMPAIGN_DIR [-o OUT.md]

Also importable: build_report(campaign_dir) returns the report as a dict.
"""

import argparse
import csv
import json
import math
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from topology_graph import MultiObjectiveScorer


def _read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _float(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def mean_ci(values):
    values = [v for v in values if v is not None]
    if not values:
        return dict(n=0, mean=None, low=None, high=None)
    mean = sum(values) / len(values)
    if len(values) == 1:
        return dict(n=1, mean=round(mean, 3), low=round(mean, 3), high=round(mean, 3))
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    margin = 1.96 * math.sqrt(variance / len(values))
    return dict(n=len(values), mean=round(mean, 3),
                low=round(mean - margin, 3), high=round(mean + margin, 3))


def summarize_run(directory, congestion_threshold=85.0):
    telemetry = _read_csv(directory / "telemetry.csv")
    events = _read_csv(directory / "control_events.csv")
    failures = _read_csv(directory / "failures.csv")
    drift = _read_csv(directory / "drift.csv")
    explanations = _read_csv(directory / "explanations.csv")
    utils = [_float(row.get("utilization")) for row in telemetry]
    utils = [u for u in utils if u is not None]
    losses = [_float(row.get("packet_loss")) for row in telemetry]
    losses = [v for v in losses if v is not None]
    down = [row for row in failures if row.get("event") == "link_down"]
    reroutes = [row for row in events if row.get("event") == "reroute"]
    return dict(
        run=directory.name,
        samples=len(utils),
        congestion_seconds=sum(1 for u in utils if u >= congestion_threshold),
        mean_utilization=round(sum(utils) / len(utils), 3) if utils else None,
        max_utilization=max(utils) if utils else None,
        mean_packet_loss_pct=round(sum(losses) / len(losses), 3) if losses else None,
        reroutes=len(reroutes),
        link_failures=len(down),
        drift_detected=any(row.get("drift_detected") in ("True", "true", "1") for row in drift),
        top_explanation_reasons=[reason for reason, _ in
                                 Counter(row.get("reason", "") for row in explanations
                                         if row.get("candidate")).most_common(3)],
    )


def build_report(campaign_dir, reference_system=None, congestion_threshold=85.0):
    campaign_dir = Path(campaign_dir)
    systems = {}
    for system_dir in sorted(p for p in campaign_dir.iterdir() if p.is_dir()):
        runs = [summarize_run(run_dir, congestion_threshold)
                for run_dir in sorted(system_dir.glob("run-*"))
                if (run_dir / "telemetry.csv").exists()]
        if runs:
            systems[system_dir.name] = runs
    if not systems:
        raise ValueError("No run captures with telemetry.csv under " + str(campaign_dir))
    reference_system = reference_system or sorted(systems)[0]
    aggregate = {}
    for name, runs in systems.items():
        aggregate[name] = {metric: mean_ci([run[metric] for run in runs])
                           for metric in ("congestion_seconds", "mean_utilization",
                                          "mean_packet_loss_pct", "reroutes")}
    paired = {}
    reference = {run["run"]: run for run in systems.get(reference_system, [])}
    for name, runs in systems.items():
        if name == reference_system:
            continue
        diffs = [run["congestion_seconds"] - reference[run["run"]]["congestion_seconds"]
                 for run in runs if run["run"] in reference]
        if diffs:
            paired[name] = mean_ci(diffs)
    scorer = MultiObjectiveScorer({"utilization": 1.0, "loss_pct": 1.0})
    ranking = scorer.score_paths({
        name: {"utilization": agg["mean_utilization"]["mean"],
               "loss_pct": agg["mean_packet_loss_pct"]["mean"]}
        for name, agg in aggregate.items()
        if agg["mean_utilization"]["mean"] is not None})
    return dict(campaign=str(campaign_dir), reference_system=reference_system,
                congestion_threshold_pct=congestion_threshold,
                systems=systems, aggregate=aggregate,
                paired_congestion_seconds_vs_reference=paired,
                multi_objective_ranking=ranking)


def render_markdown(report):
    lines = ["# Campaign report (auto-generated)", "",
             "Reference system: **%s** · congestion threshold: %s%%" % (
                 report["reference_system"], report["congestion_threshold_pct"]), "",
             "| System | Runs | Congestion s (mean, 95% CI) | Mean util % | Mean loss % | Reroutes |",
             "|---|---|---|---|---|---|"]
    for name, agg in sorted(report["aggregate"].items()):
        c = agg["congestion_seconds"]
        lines.append("| %s | %d | %s (%s–%s) | %s | %s | %s |" % (
            name, c["n"], c["mean"], c["low"], c["high"],
            agg["mean_utilization"]["mean"], agg["mean_packet_loss_pct"]["mean"],
            agg["reroutes"]["mean"]))
    lines += ["", "## Paired congestion-seconds difference vs reference", ""]
    for name, diff in sorted(report["paired_congestion_seconds_vs_reference"].items()):
        lines.append("- %s: %s s (95%% CI %s–%s, n=%d)" % (name, diff["mean"], diff["low"], diff["high"], diff["n"]))
    lines += ["", "## Multi-objective ranking (utilization + loss, lower is better)", ""]
    for row in report["multi_objective_ranking"]:
        lines.append("- %s: score %s" % (row["path"], row["score"]))
    failures = sum(run["link_failures"] for runs in report["systems"].values() for run in runs)
    drift = [name for name, runs in report["systems"].items() if any(r["drift_detected"] for r in runs)]
    lines += ["", "Link failures recorded across campaign: %d" % failures,
              "Systems with detected prediction drift: %s" % (", ".join(drift) or "none"), ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign_dir")
    parser.add_argument("-o", "--output")
    parser.add_argument("--reference")
    parser.add_argument("--threshold", type=float, default=85.0)
    args = parser.parse_args()
    report = build_report(args.campaign_dir, args.reference, args.threshold)
    markdown = render_markdown(report)
    if args.output:
        Path(args.output).write_text(markdown, encoding="utf-8")
        Path(str(args.output) + ".json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Wrote", args.output)
    else:
        print(markdown)


if __name__ == "__main__":
    main()
