"""Build the conformal calibration artifact from a recorded capture.

Pairs each recorded forecast with the actual utilization one horizon
later (exact-timestamp alignment, the project's contract), then fits
split-conformal and ACI calibrators at the requested coverage.

The capture's pairs are split chronologically: the first portion
calibrates the radius, the remaining portion is held out and used only
to MEASURE coverage. Reporting coverage on the calibration rows
themselves (the previous behaviour) is not evidence — the split-
conformal radius is by construction a quantile of those very
residuals, so in-sample coverage lands near nominal automatically.
An optional second capture (--eval-capture) is evaluated untouched as
an additional, fully independent check.

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


def split_pairs(pairs, calibration_fraction=0.6):
    cut = int(len(pairs) * calibration_fraction)
    return pairs[:cut], pairs[cut:]


def aci_heldout_coverage(aci, pairs):
    """Sequential ACI evaluation: score each interval before updating."""
    intervals, actuals = [], []
    for prediction, actual in pairs:
        intervals.append(aci.interval(prediction))
        actuals.append(actual)
        aci.update(prediction, actual)
    return coverage_report(intervals, actuals)


def build_artifact(capture, horizon=5, coverage=0.9, gamma=0.01,
                   calibration_fraction=0.6, eval_capture=None):
    pairs = build_pairs(capture, horizon)
    if len(pairs) < 40:
        raise SystemExit("Only %d forecast/actual pairs in capture; need >= 40 "
                         "for a calibration/held-out split" % len(pairs))
    calibration, heldout = split_pairs(pairs, calibration_fraction)
    alpha = 1.0 - coverage
    split = SplitConformal(alpha).calibrate(
        [p for p, _ in calibration], [a for _, a in calibration])
    aci = AdaptiveConformal(alpha, gamma=gamma).calibrate(
        [p for p, _ in calibration], [a for _, a in calibration])
    heldout_intervals = [split.interval(p) for p, _ in heldout]
    import hashlib

    def _sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    model_file = ROOT / "cnt-ml" / "models" / "gbdt_portable.json"
    schema_file = ROOT / "cnt-ml" / "models" / "feature_list.json"
    artifact = dict(
        method="split_conformal_absolute_residual",
        alpha=alpha, nominal_coverage=coverage,
        radius_percentage_points=round(split.radius, 4),
        aci=dict(gamma=gamma, initial_radius_percentage_points=round(aci.radius, 4),
                 scores=[round(s, 4) for s in aci._scores],
                 heldout_coverage=aci_heldout_coverage(aci, heldout)),
        calibration_rows=split.calibration_rows,
        heldout_rows=len(heldout),
        heldout_coverage=coverage_report(heldout_intervals,
                                         [a for _, a in heldout]),
        provenance=dict(capture=str(capture), horizon_seconds=horizon,
                        pairing="exact timestamp + horizon in telemetry.csv",
                        split="chronological: first %.0f%% calibrate, rest held out"
                              % (calibration_fraction * 100)),
        # Domain + gate diagnostics, computed from the calibration rows
        # and the radius — never asserted. in_domain at runtime means
        # the live prediction falls inside the calibrated prediction
        # range; the gate can only ever pass when a prediction's lower
        # bound (prediction - radius, predictions clipped at 100)
        # reaches the threshold, so record whether that is even
        # arithmetically possible with this radius.
        calibration_ranges=dict(
            prediction=[round(min(p for p, _ in calibration), 4),
                        round(max(p for p, _ in calibration), 4)],
            actual=[round(min(a for _, a in calibration), 4),
                    round(max(a for _, a in calibration), 4)]),
        gate_diagnostics=dict(
            threshold=85.0,
            max_lower_bound=round(100.0 - split.radius, 4),
            gate_can_activate=bool(100.0 - split.radius >= 85.0)),
        model_binding=dict(
            model_file="cnt-ml/models/gbdt_portable.json",
            model_sha256=_sha(model_file),
            feature_schema_file="cnt-ml/models/feature_list.json",
            feature_schema_sha256=_sha(schema_file)),
    )
    if eval_capture:
        eval_pairs = build_pairs(eval_capture, horizon)
        eval_intervals = [split.interval(p) for p, _ in eval_pairs]
        artifact["eval_capture_coverage"] = coverage_report(
            eval_intervals, [a for _, a in eval_pairs])
        artifact["provenance"]["eval_capture"] = str(eval_capture)
    return artifact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--coverage", type=float, default=0.9)
    parser.add_argument("--gamma", type=float, default=0.01)
    parser.add_argument("--calibration-fraction", type=float, default=0.6)
    parser.add_argument("--eval-capture")
    parser.add_argument("-o", "--output", default=str(ROOT / "cnt-ml" / "models" / "conformal_calibration.json"))
    args = parser.parse_args()

    artifact = build_artifact(args.capture, args.horizon, args.coverage,
                              args.gamma, args.calibration_fraction,
                              args.eval_capture)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: artifact[k] for k in
                      ("method", "nominal_coverage", "radius_percentage_points",
                       "calibration_rows", "heldout_rows", "heldout_coverage")},
                     indent=2))


if __name__ == "__main__":
    main()
