#!/usr/bin/env python3
"""Calibrate the frozen GB interval on reused validation; never read test labels.

The global radius is the ceil((n+1)*coverage)-th ordered absolute validation
residual. This familiar finite-sample rank does not establish conformal coverage:
validation selected model parameters, adjacent times/links are dependent, and
deployment may shift. Train rows supply feature support bounds only.
"""

import argparse
import csv
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml/src"))
from confidence import ConfidenceEstimator, METHOD, sha256_file
from portable_gbdt import PortableGBDT


def read_rows(path, features, target, horizon):
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    required = features + [target, "timestamp", "target_timestamp"]
    if not rows or any(name not in rows[0] for name in required):
        raise ValueError("Missing finite timestamp-aligned calibration data: " + str(path))
    previous = -float("inf")
    for row in rows:
        for name in required:
            row[name] = float(row[name])
            if not math.isfinite(row[name]):
                raise ValueError("Nonfinite calibration value: " + name)
        if row["timestamp"] < previous:
            raise ValueError("Calibration split is not chronological")
        if abs(row["target_timestamp"] - row["timestamp"] - horizon) > 1e-9:
            raise ValueError("Calibration target is not exactly t+horizon")
        if not 0 <= row[target] <= 100:
            raise ValueError("Calibration target is outside [0,100]")
        previous = row["timestamp"]
    return rows


def ordered_quantile(values, coverage):
    """Return the fixed rank statistic, avoiding an optimistic finite cap."""
    rank = int(math.ceil((len(values) + 1) * coverage))
    if not values or rank > len(values):
        raise ValueError("Too few calibration rows for the requested nominal coverage")
    return sorted(values)[rank - 1], rank


def build_artifact(root=ROOT, nominal_coverage=0.9):
    root = Path(root)
    if not 0 < nominal_coverage < 1:
        raise ValueError("Nominal coverage must lie strictly between zero and one")
    models = root / "cnt-ml/models"
    splits = root / "cnt-ml/data/real/calibration-02/prepared"
    schema_path = models / "feature_list.json"
    portable_path = models / "gbdt_portable.json"
    source_path = models / "xgb_link_predictor.joblib"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    portable = json.loads(portable_path.read_text(encoding="utf-8"))
    features = schema["features"]
    horizon = schema["horizon_seconds"]
    target = schema["target"]
    if schema.get("target_alignment") != "exact_timestamp" or schema.get("engine") != "sklearn_gbdt":
        raise ValueError("Expected the frozen timestamp-aligned GB feature schema")
    train = read_rows(splits / "train.csv", features, target, horizon)
    val = read_rows(splits / "val.csv", features, target, horizon)
    if max(row["target_timestamp"] for row in train) >= min(row["timestamp"] for row in val):
        raise ValueError("Training targets overlap validation observation time")
    model = PortableGBDT(portable_path, source_path, features)
    predictions = [min(100.0, max(0.0, float(value))) for value in
                   model.predict([[row[name] for name in features] for row in val])]
    residuals = [abs(row[target] - value) for row, value in zip(val, predictions)]
    radius, rank = ordered_quantile(residuals, nominal_coverage)
    used = {features[tree["feature"][node]] for tree in portable["trees"]
            for node in range(len(tree["left"])) if tree["left"][node] != -1}
    domain_features = [name for name in features if name in used]
    bounds = {name: {"min": min(row[name] for row in train),
                     "max": max(row[name] for row in train)} for name in domain_features}
    in_domain = [all(bounds[name]["min"] <= row[name] <= bounds[name]["max"]
                     for name in domain_features) for row in val]
    signed_overprediction = [value - row[target] for row, value in zip(val, predictions)]
    one_sided, one_sided_rank = ordered_quantile(signed_overprediction, nominal_coverage)

    def binding(path):
        return {"path": str(path.relative_to(root)), "sha256": sha256_file(path)}

    bindings = {"portable_model": binding(portable_path), "source_model": binding(source_path),
                "feature_schema": binding(schema_path)}
    # Test is hashed as an opaque file; no test rows or labels are read or used.
    bindings.update({name + "_split": binding(splits / (name + ".csv"))
                     for name in ("train", "val", "test")})
    covered = sum(error <= radius for error in residuals)
    gate_passes = sum(domain and max(0.0, value - radius) >= 85.0
                      for domain, value in zip(in_domain, predictions))
    return {
        "schema": 1,
        "method": METHOD,
        "features": features,
        "domain_features": domain_features,
        "ignored_unused_features": [name for name in features if name not in used],
        "target": target,
        "horizon_seconds": horizon,
        "nominal_coverage": nominal_coverage,
        "radius_percentage_points": radius,
        "quantile_rank": rank,
        "rank_convention": "ceil((n+1)*nominal_coverage), 1-based, no interpolation",
        "prediction_clipping": [0.0, 100.0],
        "interval_clipping": [0.0, 100.0],
        "train_rows": len(train),
        "calibration_rows": len(val),
        "calibration_unique_timestamps": len({row["timestamp"] for row in val}),
        "training_time_range": [min(row["timestamp"] for row in train), max(row["timestamp"] for row in train)],
        "training_target_max_timestamp": max(row["target_timestamp"] for row in train),
        "calibration_time_range": [min(row["timestamp"] for row in val), max(row["timestamp"] for row in val)],
        "training_feature_ranges": bounds,
        "empirical_validation_coverage": covered / len(val),
        "validation_in_domain_rows": sum(in_domain),
        "validation_point_predictions_at_or_above_85": sum(value >= 85.0 for value in predictions),
        "validation_interval_gate_passes_at_85": gate_passes,
        "validation_positive_targets_at_85": sum(row[target] >= 85.0 for row in val),
        "validation_reused_for_model_selection": True,
        "independent_calibration_available": False,
        "exploratory": True,
        "coverage_guarantee": False,
        "test_labels_used": False,
        "model_refit": False,
        "diagnostic_only": {
            "one_sided_overprediction_radius_percentage_points": max(0.0, one_sided),
            "one_sided_quantile_rank": one_sided_rank,
            "used_for_routing": False,
        },
        "limitations": [
            "Validation also selected model hyperparameters; this is exploratory residual calibration.",
            "Adjacent timestamps and simultaneous link observations are dependent.",
            "Nominal coverage is a setting, not a congestion probability or coverage guarantee.",
            "Coverage may shift with workload, RTT, topology, capacity, or telemetry semantics.",
            "Training feature ranges are a necessary support check, not proof of distributional similarity.",
            "No independent calibration or held-out evaluation of the interval has been performed.",
        ],
        "routing_gate": {
            "threshold_percent": 85.0,
            "rule": "in_domain and interval_lower >= congestion_threshold",
            "maximum_possible_lower_bound_percent": max(0.0, 100.0 - radius),
            "proactive_gate_can_pass_at_85": max(0.0, 100.0 - radius) >= 85.0,
            "measured_reactive_fallback_required": True,
        },
        "bindings": bindings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage", type=float, default=0.9)
    parser.add_argument("--output", type=Path, default=ROOT / "cnt-ml/models/gbdt_confidence.json")
    args = parser.parse_args()
    artifact = build_artifact(nominal_coverage=args.coverage)
    if args.output.exists():
        existing = json.loads(args.output.read_text(encoding="utf-8"))
        if existing != artifact:
            raise SystemExit("Refusing to overwrite a different confidence artifact; choose a new --output")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    estimator = ConfidenceEstimator(artifact_path=args.output)
    result = estimator.metadata()
    result["maximum_possible_lower_bound_percent"] = artifact["routing_gate"]["maximum_possible_lower_bound_percent"]
    result["validation_interval_gate_passes_at_85"] = artifact["validation_interval_gate_passes_at_85"]
    result["diagnostic_only"] = artifact["diagnostic_only"]
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
