"""Build the conformal calibration artifact from a recorded capture.

Pairs each recorded forecast with the actual utilization one horizon
later (exact-timestamp alignment, the project's contract), then fits
split-conformal and ACI calibrators at the requested coverage. The
artifact records its provenance (capture, rows, horizon, pairing) so a
reviewer can reproduce the radius — nothing is tuned by hand.

Usage: python3 -m experiments.calibrate_conformal CAPTURE_DIR [-o OUT.json]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml" / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from conformal import AdaptiveConformal, SplitConformal, coverage_report
from forecast_stats import build_pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--coverage", type=float, default=0.9)
    parser.add_argument("--gamma", type=float, default=0.01)
    parser.add_argument("-o", "--output", default=str(ROOT / "cnt-ml" / "models" / "conformal_calibration.json"))
    args = parser.parse_args()

    pairs = build_pairs(args.capture, args.horizon)
    if len(pairs) < 20:
        raise SystemExit("Only %d forecast/actual pairs in capture; need >= 20" % len(pairs))
    predictions = [p for p, _ in pairs]
    actuals = [a for _, a in pairs]
    alpha = 1.0 - args.coverage
    split = SplitConformal(alpha).calibrate(predictions, actuals)
    aci = AdaptiveConformal(alpha, gamma=args.gamma).calibrate(predictions, actuals)
    intervals = [split.interval(p) for p in predictions]
    artifact = dict(
        method="split_conformal_absolute_residual",
        alpha=alpha, nominal_coverage=args.coverage,
        radius_percentage_points=round(split.radius, 4),
        aci=dict(gamma=args.gamma, initial_radius_percentage_points=round(aci.radius, 4),
                 scores=[round(s, 4) for s in aci._scores]),
        calibration_rows=split.calibration_rows,
        empirical_coverage_on_calibration=coverage_report(intervals, actuals),
        provenance=dict(capture=str(args.capture), horizon_seconds=args.horizon,
                        pairing="exact timestamp + horizon in telemetry.csv"),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: artifact[k] for k in
                      ("method", "nominal_coverage", "radius_percentage_points",
                       "calibration_rows", "empirical_coverage_on_calibration")}, indent=2))


if __name__ == "__main__":
    main()
