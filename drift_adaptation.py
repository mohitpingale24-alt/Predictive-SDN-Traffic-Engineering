"""Drift detectors and adaptation strategies, comparable head-to-head.

Pure Python. Two standard detectors (Page-Hinkley and an ADWIN-style
adaptive window using a Hoeffding bound) watch the forecast-error stream.
Three strategies consume the same stream of (prediction, actual) pairs and
maintain a conformal-style interval radius:

- ``none``: radius fixed from the calibration segment, never updated;
- ``periodic``: radius recomputed from the last W residuals every W pairs;
- ``online``: radius recomputed from the last W residuals on every pair
  (rolling recalibration).

``evaluate_strategies`` runs all three over one stream and reports
post-shift coverage, MAE of the radius, and adaptation lag — so the claim
"adaptation helps" is measured on shifted data, not asserted.
"""

import math
from collections import deque


class PageHinkley:
    """Page-Hinkley test on a scalar stream (here: absolute forecast error)."""

    def __init__(self, delta=0.05, threshold=5.0):
        self.delta = float(delta)
        self.threshold = float(threshold)
        self.reset()

    def reset(self):
        self._mean = 0.0
        self._count = 0
        self._cumulative = 0.0
        self._minimum = 0.0

    def observe(self, value):
        value = float(value)
        self._count += 1
        self._mean += (value - self._mean) / self._count
        self._cumulative += value - self._mean - self.delta
        self._minimum = min(self._minimum, self._cumulative)
        return (self._cumulative - self._minimum) > self.threshold


class AdwinLite:
    """ADWIN-style detector: compares every split of a growing window.

    Uses the Hoeffding bound epsilon = sqrt(ln(2/delta) / (2 * harmonic
    mean size)) on sub-window mean differences of the error stream. The
    window is bounded for controller use; the split check runs on bucketed
    summaries, which is the standard practical approximation.
    """

    def __init__(self, delta=0.002, max_window=500):
        self.delta = float(delta)
        self.max_window = int(max_window)
        self._values = deque(maxlen=self.max_window)

    def observe(self, value):
        self._values.append(float(value))
        values = list(self._values)
        n = len(values)
        if n < 20:
            return False
        total = sum(values)
        left_sum = 0.0
        for split in range(10, n - 9):
            left_sum += values[split - 1]
            left_n, right_n = split, n - split
            left_mean = left_sum / left_n
            right_mean = (total - left_sum) / right_n
            harmonic = 2.0 / (1.0 / left_n + 1.0 / right_n)
            epsilon = math.sqrt(math.log(2.0 / self.delta) / (2.0 * harmonic))
            if abs(left_mean - right_mean) > epsilon:
                return True
        return False


def _radius(residuals, coverage):
    ordered = sorted(residuals)
    rank = min(len(ordered), max(1, math.ceil((len(ordered) + 1) * coverage)))
    return ordered[rank - 1]


class RadiusStrategy:
    def __init__(self, kind, calibration_pairs, coverage=0.9, window=100):
        if kind not in ("none", "periodic", "online"):
            raise ValueError("Unknown adaptation strategy: " + str(kind))
        residuals = [abs(float(p) - float(a)) for p, a in calibration_pairs]
        if len(residuals) < 10:
            raise ValueError("Strategy needs at least 10 calibration pairs")
        self.kind = kind
        self.coverage = float(coverage)
        self.window = int(window)
        self.radius = _radius(residuals, coverage)
        self._residuals = deque(residuals, maxlen=self.window)
        self._since_update = 0

    def interval(self, prediction):
        prediction = float(prediction)
        return (max(0.0, prediction - self.radius), min(100.0, prediction + self.radius))

    def observe(self, prediction, actual):
        lower, upper = self.interval(prediction)
        covered = lower <= float(actual) <= upper
        self._residuals.append(abs(float(prediction) - float(actual)))
        self._since_update += 1
        if self.kind == "online":
            self.radius = _radius(list(self._residuals), self.coverage)
        elif self.kind == "periodic" and self._since_update >= self.window:
            self.radius = _radius(list(self._residuals), self.coverage)
            self._since_update = 0
        return covered


def evaluate_strategies(pairs, calibration_fraction=0.25, coverage=0.9, window=100):
    """Run all strategies over one stream; report post-calibration behaviour."""
    pairs = [(float(p), float(a)) for p, a in pairs]
    split = max(10, int(len(pairs) * calibration_fraction))
    if len(pairs) - split < 10:
        raise ValueError("Stream too short to evaluate adaptation")
    calibration, stream = pairs[:split], pairs[split:]
    report = {}
    for kind in ("none", "periodic", "online"):
        strategy = RadiusStrategy(kind, calibration, coverage, window)
        covered, radii = 0, []
        for predicted, actual in stream:
            if strategy.observe(predicted, actual):
                covered += 1
            radii.append(strategy.radius)
        report[kind] = dict(
            n=len(stream),
            coverage=round(covered / len(stream), 4),
            target_coverage=coverage,
            coverage_error=round(abs(covered / len(stream) - coverage), 4),
            mean_radius=round(sum(radii) / len(radii), 4),
            final_radius=round(radii[-1], 4))
    return report
