"""Controller-side conformal intervals (opt-in, SDN_CONFORMAL=1).

Loads the artifact written by experiments/calibrate_conformal.py and
returns interval dictionaries with the same keys the confidence policy
already consumes, so the routing policy does not change — only the
interval's provenance does: a finite-sample coverage guarantee instead of
an exploratory residual band. ACI updates the radius online from paired
forecast/actual observations; when drift adaptation is enabled the radius
additionally tracks a rolling residual quantile (drift_adaptation.py).
"""

import hashlib
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "cnt-ml", "src"))
from conformal import AdaptiveConformal


class ConformalRuntime:
    def __init__(self, artifact_path, threshold=85.0, model_path=None):
        with open(artifact_path, encoding="utf-8") as handle:
            self.data = json.load(handle)
        if self.data.get("method") != "split_conformal_absolute_residual":
            raise ValueError("Unsupported conformal artifact method")
        # The intervals were calibrated against one specific forecaster.
        # If the caller supplies the model it will actually use, refuse
        # to serve intervals when it is not the calibrated one — a
        # swapped model silently voids the coverage evidence.
        binding = self.data.get("model_binding") or {}
        expected = binding.get("model_sha256")
        if expected and model_path is not None:
            with open(model_path, "rb") as handle:
                actual = hashlib.sha256(handle.read()).hexdigest()
            if actual != expected:
                raise ValueError(
                    "Conformal artifact was calibrated for a different "
                    "model (sha256 mismatch); recalibrate before use")
        ranges = self.data.get("calibration_ranges") or {}
        self.prediction_range = ranges.get("prediction")
        self.threshold = float(threshold)
        self.nominal_coverage = float(self.data["nominal_coverage"])
        aci_data = self.data.get("aci") or {}
        self.aci = AdaptiveConformal(alpha=float(self.data["alpha"]),
                                     gamma=float(aci_data.get("gamma", 0.01)))
        # Rebuild ACI state from the stored calibration scores.
        self.aci._scores = [float(s) for s in aci_data.get("scores", [])]
        if not self.aci._scores:
            raise ValueError("Conformal artifact carries no calibration scores")
        self.aci._refresh()
        self.base_radius = float(self.data["radius_percentage_points"])
        self.adapted_radius = None

    def set_adapted_radius(self, radius):
        self.adapted_radius = None if radius is None else float(radius)

    @property
    def radius(self):
        if self.adapted_radius is not None:
            return self.adapted_radius
        return self.aci.radius

    def interval(self, prediction, features=None):
        prediction = float(prediction)
        if not math.isfinite(prediction):
            raise ValueError("Prediction must be finite")
        prediction = min(100.0, max(0.0, prediction))
        radius = self.radius
        lower = max(0.0, prediction - radius)
        upper = min(100.0, prediction + radius)
        gate_pass = lower >= self.threshold
        # Domain is computed, not asserted: a prediction outside the
        # range seen during calibration is outside the evidence the
        # intervals rest on. Artifacts without recorded ranges (older
        # files) keep the historical behaviour.
        in_domain = True
        out_of_domain = []
        if self.prediction_range is not None:
            low, high = self.prediction_range
            if not (low <= prediction <= high):
                in_domain = False
                out_of_domain = ["prediction"]
        return dict(prediction=prediction, lower=lower, upper=upper,
                    width=upper - lower, radius=radius,
                    nominal_coverage=self.nominal_coverage,
                    method="split_conformal_aci",
                    in_domain=in_domain,
                    confidence_gate_pass=gate_pass,
                    gate_reason=("lower_bound_at_or_above_threshold" if gate_pass
                                 else "lower_bound_below_threshold"),
                    out_of_domain_features=out_of_domain,
                    missing_features=[], invalid_features=[],
                    exploratory=False)

    def update(self, prediction, actual):
        return self.aci.update(prediction, actual)
