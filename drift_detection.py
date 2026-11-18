"""Prediction drift detection and recalibration.

Pure Python. The monitor pairs each forecast with the actual value observed
one horizon later (the caller does the pairing) and compares a sliding
window's error against a baseline error supplied by the caller — in the
controller that baseline is read from the saved model-metrics file, never
typed in here. Drift is declared only after consecutive drifting windows, so
one bad minute cannot trigger it.

Recalibration recomputes an empirical residual radius from the observed
pairs (the same method as the existing confidence artifact) and writes it as
a JSON artifact; it never swaps the live model by itself.
"""

import json
import math
import os
from collections import deque


def _stats(pairs):
    errors = [predicted - actual for predicted, actual in pairs]
    absolute = [abs(e) for e in errors]
    n = len(pairs)
    return dict(
        n=n,
        mae=round(sum(absolute) / n, 4) if n else None,
        rmse=round(math.sqrt(sum(e * e for e in errors) / n), 4) if n else None,
        bias=round(sum(errors) / n, 4) if n else None,
        max_abs_error=round(max(absolute), 4) if n else None,
    )


class DriftMonitor:
    def __init__(self, baseline_rmse, window=30, factor=1.5, consecutive_windows=3):
        baseline_rmse = float(baseline_rmse)
        if not math.isfinite(baseline_rmse) or baseline_rmse <= 0:
            raise ValueError("baseline_rmse must be finite and positive")
        if int(window) < 5 or float(factor) <= 1 or int(consecutive_windows) < 1:
            raise ValueError("Invalid drift window, factor, or consecutive_windows")
        self.baseline_rmse = baseline_rmse
        self.window = int(window)
        self.factor = float(factor)
        self.consecutive_windows = int(consecutive_windows)
        self._pairs = deque(maxlen=self.window)
        self._all_pairs = []
        self._drifting_windows = 0
        self.drift_detected = False
        self.drift_at = None

    def observe(self, predicted, actual, timestamp=None):
        predicted, actual = float(predicted), float(actual)
        if not (math.isfinite(predicted) and math.isfinite(actual)):
            return None
        pair = (predicted, actual)
        self._pairs.append(pair)
        self._all_pairs.append(pair)
        if len(self._pairs) < self.window:
            return None
        stats = _stats(list(self._pairs))
        drifting = stats["rmse"] > self.baseline_rmse * self.factor
        self._drifting_windows = self._drifting_windows + 1 if drifting else 0
        if self._drifting_windows >= self.consecutive_windows and not self.drift_detected:
            self.drift_detected = True
            self.drift_at = timestamp
        return dict(stats, drifting=drifting,
                    drifting_windows=self._drifting_windows,
                    drift_detected=self.drift_detected,
                    baseline_rmse=self.baseline_rmse,
                    threshold_rmse=round(self.baseline_rmse * self.factor, 4),
                    timestamp=timestamp)

    def overall_stats(self):
        return _stats(self._all_pairs)

    def pairs(self):
        return list(self._all_pairs)

    def reset_drift(self):
        self.drift_detected = False
        self.drift_at = None
        self._drifting_windows = 0


def recalibrate(pairs, coverage=0.9):
    """Empirical residual radius at the requested coverage, from real pairs."""
    pairs = [(float(p), float(a)) for p, a in pairs
             if math.isfinite(float(p)) and math.isfinite(float(a))]
    if len(pairs) < 10:
        raise ValueError("Recalibration needs at least 10 prediction/actual pairs")
    if not 0 < float(coverage) < 1:
        raise ValueError("coverage must lie in (0, 1)")
    residuals = sorted(abs(p - a) for p, a in pairs)
    index = min(len(residuals) - 1, math.ceil(float(coverage) * len(residuals)) - 1)
    radius = residuals[index]
    covered = sum(1 for r in residuals if r <= radius) / len(residuals)
    return dict(
        method="observed_residual_quantile_recalibration",
        rows=len(pairs),
        nominal_coverage=float(coverage),
        empirical_coverage=round(covered, 4),
        radius_percentage_points=round(radius, 4),
        stats=_stats(pairs),
    )


def write_recalibration_artifact(pairs, path, coverage=0.9):
    artifact = recalibrate(pairs, coverage=coverage)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, indent=2)
        handle.write("\n")
    os.replace(temporary, path)
    return artifact
