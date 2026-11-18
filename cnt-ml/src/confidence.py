"""Empirical prediction intervals for the frozen congestion forecaster.

Only the Python standard library is needed at runtime. These intervals reuse
the model-selection validation split and are exploratory: nominal coverage is
an interval setting, not a probability or a coverage guarantee.
"""

import hashlib
import json
import math
from pathlib import Path


METHOD = "validation_absolute_residual_exploratory"
ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class ConfidenceEstimator:
    """Load a bound calibration artifact and fail closed on unsupported inputs.

    The default constructor checks hashes of the frozen portable model, its
    original source weights, feature schema, and the chronological split files.
    A runtime copy without datasets may disable split checks explicitly; model
    and schema checks remain mandatory. ``interval`` never changes the model.
    """

    def __init__(self, artifact_path=None, model_path=None, schema_path=None,
                 source_model_path=None, split_directory=None,
                 verify_split_hashes=True):
        models = ROOT / "models"
        artifact_path = Path(artifact_path or models / "gbdt_confidence.json")
        with open(artifact_path, encoding="utf-8") as handle:
            self.data = json.load(handle)
        if self.data.get("schema") != 1 or self.data.get("method") != METHOD:
            raise ValueError("Unsupported confidence artifact schema or method")
        self.features = list(self.data["features"])
        self.used_features = list(self.data["domain_features"])
        self.ignored_features = list(self.data["ignored_unused_features"])
        if not self.features or len(set(self.features)) != len(self.features):
            raise ValueError("Invalid confidence feature contract")
        if (set(self.used_features) | set(self.ignored_features) != set(self.features)
                or set(self.used_features) & set(self.ignored_features)):
            raise ValueError("Confidence domain feature contract mismatch")
        if not self.used_features:
            raise ValueError("Confidence domain must include used model features")

        self.radius = float(self.data["radius_percentage_points"])
        self.nominal_coverage = float(self.data["nominal_coverage"])
        if (not math.isfinite(self.radius) or self.radius < 0
                or not 0 < self.nominal_coverage < 1):
            raise ValueError("Invalid confidence calibration values")
        if self.data.get("validation_reused_for_model_selection") is not True:
            raise ValueError("Confidence calibration provenance is missing")
        self.bounds = self.data["training_feature_ranges"]
        if set(self.bounds) != set(self.used_features):
            raise ValueError("Confidence feature range contract mismatch")
        for feature, bounds in self.bounds.items():
            low, high = float(bounds["min"]), float(bounds["max"])
            if not math.isfinite(low) or not math.isfinite(high) or low > high:
                raise ValueError("Invalid training range for " + feature)

        bindings = self.data["bindings"]
        paths = {
            "portable_model": Path(model_path or models / "gbdt_portable.json"),
            "source_model": Path(source_model_path or models / "xgb_link_predictor.joblib"),
            "feature_schema": Path(schema_path or models / "feature_list.json"),
        }
        for key, path in paths.items():
            if sha256_file(path) != bindings[key]["sha256"]:
                raise ValueError("Stale confidence artifact: " + key + " hash mismatch")
        with open(paths["feature_schema"], encoding="utf-8") as handle:
            schema = json.load(handle)
        with open(paths["portable_model"], encoding="utf-8") as handle:
            portable = json.load(handle)
        if (schema.get("features") != self.features
                or portable.get("features") != self.features
                or schema.get("horizon_seconds") != self.data["horizon_seconds"]
                or schema.get("target_alignment") != "exact_timestamp"):
            raise ValueError("Confidence model/schema contract mismatch")
        used = {self.features[tree["feature"][node]]
                for tree in portable["trees"]
                for node in range(len(tree["left"])) if tree["left"][node] != -1}
        if used != set(self.used_features):
            raise ValueError("Confidence used-feature contract mismatch")
        if portable.get("source_sha256") != bindings["source_model"]["sha256"]:
            raise ValueError("Confidence source weights contract mismatch")

        self.split_hashes_verified = bool(verify_split_hashes)
        if verify_split_hashes:
            splits = Path(split_directory or ROOT / "data/real/calibration-02/prepared")
            for name in ("train", "val", "test"):
                if sha256_file(splits / (name + ".csv")) != bindings[name + "_split"]["sha256"]:
                    raise ValueError("Stale confidence artifact: " + name + " split hash mismatch")

    def metadata(self):
        """Return concise provenance for run metadata and decision logging."""
        return {
            "method": METHOD,
            "nominal_coverage": self.nominal_coverage,
            "radius_percentage_points": self.radius,
            "calibration_rows": self.data["calibration_rows"],
            "empirical_validation_coverage": self.data["empirical_validation_coverage"],
            "exploratory": True,
            "validation_reused_for_model_selection": True,
            "split_hashes_verified": self.split_hashes_verified,
            "domain_features": list(self.used_features),
            "ignored_unused_features": list(self.ignored_features),
            "bindings": self.data["bindings"],
        }

    def interval(self, prediction, features, threshold=85.0):
        """Return an interval and whether its lower bound permits a reroute.

        ``features`` is the same mapping used for frozen GB prediction. Inputs
        outside any split-used training feature range receive a logged interval
        but cannot pass the confidence gate. Missing/nonfinite features receive
        the fully uninformative [0,100] interval. The routing policy owns its
        consecutive-sample, alternative-path, hysteresis and cooldown checks.
        """
        prediction = float(prediction)
        threshold = float(threshold)
        if not math.isfinite(prediction) or not math.isfinite(threshold):
            raise ValueError("Prediction and congestion threshold must be finite")
        if not 0 <= threshold <= 100:
            raise ValueError("Congestion threshold must lie in [0,100]")
        prediction = min(100.0, max(0.0, prediction))
        features = features if isinstance(features, dict) else {}
        missing = [name for name in self.features if name not in features]
        invalid = []
        values = {}
        for name in self.features:
            if name in features:
                try:
                    value = float(features[name])
                    if not math.isfinite(value):
                        raise ValueError("nonfinite")
                    values[name] = value
                except (TypeError, ValueError, OverflowError):
                    invalid.append(name)
        outside = []
        if not missing and not invalid:
            for name in self.used_features:
                low, high = self.bounds[name]["min"], self.bounds[name]["max"]
                tolerance = 1e-9 * max(1.0, abs(low), abs(high))
                if not low - tolerance <= values[name] <= high + tolerance:
                    outside.append(name)
        in_domain = not (missing or invalid or outside)
        lower = max(0.0, prediction - self.radius)
        upper = min(100.0, prediction + self.radius)
        if missing or invalid:
            lower, upper = 0.0, 100.0
        gate_pass = in_domain and lower >= threshold
        if missing:
            reason = "missing_features"
        elif invalid:
            reason = "nonfinite_features"
        elif outside:
            reason = "outside_training_feature_ranges"
        elif gate_pass:
            reason = "lower_bound_at_or_above_threshold"
        else:
            reason = "lower_bound_below_threshold"
        return {
            "prediction": prediction,
            "lower": lower,
            "upper": upper,
            "width": upper - lower,
            "radius": self.radius,
            "nominal_coverage": self.nominal_coverage,
            "method": METHOD,
            "in_domain": in_domain,
            "confidence_gate_pass": gate_pass,
            "gate_reason": reason,
            "out_of_domain_features": outside,
            "missing_features": missing,
            "invalid_features": invalid,
            "exploratory": True,
        }
