"""Conformal prediction with finite-sample coverage guarantees.

Pure standard library. Three methods, all distribution-free:

- Split conformal (absolute residuals): the radius is the
  ceil((n+1)(1-alpha)) / n empirical quantile of calibration residuals,
  which guarantees marginal coverage >= 1-alpha for exchangeable data —
  a guarantee, not the exploratory band the frozen artifact provides.
- Conformalized quantile regression (CQR): wraps any pair of lower/upper
  quantile forecasts and corrects them by the conformal score quantile.
- Adaptive conformal inference (ACI, Gibbs & Candes 2021): the miscoverage
  level alpha_t is updated online from observed coverage errors, so the
  interval adapts under distribution shift while retaining long-run
  coverage control.

Nothing is fitted to a specific dataset here; calibration data is supplied
by the caller.
"""

import math


def conformal_quantile(scores, alpha):
    """Finite-sample conformal quantile of a score list at level 1-alpha."""
    if not scores:
        raise ValueError("Conformal calibration needs at least one score")
    if not 0 < float(alpha) < 1:
        raise ValueError("alpha must lie in (0, 1)")
    ordered = sorted(float(s) for s in scores)
    rank = math.ceil((len(ordered) + 1) * (1 - float(alpha)))
    rank = min(max(rank, 1), len(ordered))
    return ordered[rank - 1]


class SplitConformal:
    def __init__(self, alpha=0.1):
        self.alpha = float(alpha)
        self.radius = None
        self.calibration_rows = 0

    def calibrate(self, predictions, actuals):
        pairs = [(float(p), float(a)) for p, a in zip(predictions, actuals)
                 if math.isfinite(float(p)) and math.isfinite(float(a))]
        if len(pairs) < 10:
            raise ValueError("Split conformal needs at least 10 calibration pairs")
        scores = [abs(p - a) for p, a in pairs]
        self.radius = conformal_quantile(scores, self.alpha)
        self.calibration_rows = len(pairs)
        return self

    def interval(self, prediction):
        if self.radius is None:
            raise ValueError("Calibrate before requesting an interval")
        prediction = float(prediction)
        return (max(0.0, prediction - self.radius), min(100.0, prediction + self.radius))


class ConformalizedQuantile:
    """CQR over caller-supplied (lower, upper) quantile forecasts."""

    def __init__(self, alpha=0.1):
        self.alpha = float(alpha)
        self.correction = None
        self.calibration_rows = 0

    def calibrate(self, lowers, uppers, actuals):
        rows = [(float(lo), float(hi), float(a)) for lo, hi, a in zip(lowers, uppers, actuals)
                if all(math.isfinite(v) for v in (float(lo), float(hi), float(a)))]
        if len(rows) < 10:
            raise ValueError("CQR needs at least 10 calibration rows")
        scores = [max(lo - a, a - hi) for lo, hi, a in rows]
        self.correction = conformal_quantile(scores, self.alpha)
        self.calibration_rows = len(rows)
        return self

    def interval(self, lower, upper):
        if self.correction is None:
            raise ValueError("Calibrate before requesting an interval")
        return (max(0.0, float(lower) - self.correction),
                min(100.0, float(upper) + self.correction))


class AdaptiveConformal:
    """ACI wrapper around a SplitConformal radius.

    alpha_t moves down after a miss (intervals widen) and up after a hit,
    with step gamma; the radius for the current alpha_t is recomputed from
    the retained calibration scores, so adaptation needs no refit.
    """

    def __init__(self, alpha=0.1, gamma=0.01):
        if not 0 < float(alpha) < 1 or float(gamma) <= 0:
            raise ValueError("Invalid ACI alpha or gamma")
        self.target_alpha = float(alpha)
        self.alpha_t = float(alpha)
        self.gamma = float(gamma)
        self._scores = []
        self.radius = None

    def calibrate(self, predictions, actuals):
        pairs = [(float(p), float(a)) for p, a in zip(predictions, actuals)
                 if math.isfinite(float(p)) and math.isfinite(float(a))]
        if len(pairs) < 10:
            raise ValueError("ACI needs at least 10 calibration pairs")
        self._scores = [abs(p - a) for p, a in pairs]
        self._refresh()
        return self

    def _refresh(self):
        self.radius = conformal_quantile(self._scores, min(max(self.alpha_t, 0.001), 0.999))

    def interval(self, prediction):
        if self.radius is None:
            raise ValueError("Calibrate before requesting an interval")
        prediction = float(prediction)
        return (max(0.0, prediction - self.radius), min(100.0, prediction + self.radius))

    def update(self, prediction, actual):
        """Observe one outcome; returns True when the interval covered it."""
        if self.radius is None:
            raise ValueError("Calibrate before updating")
        lower, upper = self.interval(prediction)
        covered = lower <= float(actual) <= upper
        error = 0.0 if covered else 1.0
        self.alpha_t = min(0.999, max(0.001,
                                      self.alpha_t + self.gamma * (self.target_alpha - error)))
        self._scores.append(abs(float(prediction) - float(actual)))
        self._refresh()
        return covered


def coverage_report(intervals, actuals):
    """Empirical coverage and mean width for a set of intervals."""
    rows = [(float(lo), float(hi), float(a)) for (lo, hi), a in zip(intervals, actuals)
            if all(math.isfinite(v) for v in (float(lo), float(hi), float(a)))]
    if not rows:
        raise ValueError("No finite interval/actual rows")
    covered = sum(1 for lo, hi, a in rows if lo <= a <= hi)
    return dict(n=len(rows), coverage=round(covered / len(rows), 4),
                mean_width=round(sum(hi - lo for lo, hi, _ in rows) / len(rows), 4))
