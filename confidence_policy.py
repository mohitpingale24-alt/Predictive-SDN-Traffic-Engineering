"""Choose explicit confidence or measured signals for the proposed SDN policy.

This pure module does not own sampling freshness, consecutive decisions,
cooldown, recovery hysteresis, or switch updates. The controller resets its
consecutive candidate whenever the returned signal source changes.
"""

import math

from telemetry_runtime import project_path_signals


def _finite_percent(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and 0 <= value <= 100 else None


def _result(source, signals, reason, alternative_safe):
    return {"source": source, "signals": signals, "reason": reason,
            "alternative_safe": bool(alternative_safe)}


def select_signals(active_path, observed, predictions, intervals, capacities,
                   threshold=85.0):
    """Select conservative signals, with measured fallback for weak forecasts.

    Learned congestion evidence is the active interval's lower endpoint. The
    alternative signal adds the active *upper* endpoint, converted through the
    capacity ratio, to the alternative upper endpoint. A confidently unsafe
    alternative blocks the decision without replacing it with a weaker point
    forecast. Recovery from B uses measured signals because the exploratory
    interval has not established confidence for recovery.
    """
    if active_path not in ("A", "B"):
        return _result("blocked", None, "invalid_active_path", False)
    threshold = _finite_percent(threshold)
    if threshold is None:
        return _result("blocked", None, "invalid_congestion_threshold", False)
    capacities = capacities if isinstance(capacities, dict) else {}
    path_capacities = {}
    for path in ("A", "B"):
        try:
            value = float(capacities[path])
        except (KeyError, TypeError, ValueError, OverflowError):
            return _result("blocked", None, "invalid_path_capacities", False)
        if not math.isfinite(value) or value <= 0:
            return _result("blocked", None, "invalid_path_capacities", False)
        path_capacities[path] = value
    alternative = "B" if active_path == "A" else "A"

    def measured(reason):
        values = observed if isinstance(observed, dict) else {}
        signal = {path: _finite_percent(values.get(path)) for path in ("A", "B")}
        if any(value is None for value in signal.values()):
            return _result("blocked", None, "missing_or_invalid_measured_signal", False)
        projected = project_path_signals(active_path, signal, path_capacities)
        return _result("measured_fallback", projected, reason,
                       projected[alternative] < threshold)

    if active_path == "B":
        return measured("measured_recovery")
    predictions = predictions if isinstance(predictions, dict) else {}
    if not {"A", "B"}.issubset(predictions):
        return measured("missing_prediction")
    points = {path: _finite_percent(predictions[path]) for path in ("A", "B")}
    if any(value is None for value in points.values()):
        return measured("invalid_prediction")
    intervals = intervals if isinstance(intervals, dict) else {}
    if not all(isinstance(intervals.get(path), dict) for path in ("A", "B")):
        return measured("missing_confidence_interval")
    endpoints = {}
    for path in ("A", "B"):
        interval = intervals[path]
        lower = _finite_percent(interval.get("lower"))
        upper = _finite_percent(interval.get("upper"))
        if (lower is None or upper is None or lower > upper
                or not lower <= points[path] <= upper
                or interval.get("in_domain") not in (True, False)):
            return measured("invalid_confidence_interval")
        if interval.get("in_domain") is not True:
            return measured("outside_training_feature_ranges")
        endpoints[path] = (lower, upper)
    if (intervals[active_path].get("confidence_gate_pass") is not True
            or endpoints[active_path][0] < threshold):
        return measured("confidence_gate_closed")

    transfer = (endpoints[active_path][1]
                * path_capacities[active_path] / path_capacities[alternative])
    alternative_upper = min(100.0, endpoints[alternative][1] + transfer)
    if alternative_upper >= threshold:
        return _result("blocked", None, "unsafe_alternative", False)
    signals = {active_path: endpoints[active_path][0], alternative: alternative_upper}
    return _result("learned_confidence", signals, "confident_congestion_safe_alternative", True)
