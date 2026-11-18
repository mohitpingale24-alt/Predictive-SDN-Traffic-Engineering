"""'Why did it reroute?' explanations built from the decision's own inputs.

Pure Python. An explanation is assembled only from values the caller actually
measured or predicted at decision time: the signals for every candidate path,
the forecast interval when one exists, and — when a portable GBDT artifact
path is supplied — feature importance computed from that artifact's trees
(split counts) combined with how far this sample's features sit from the
training ranges the caller provides. No feature, weight, or reason is
hardcoded; a missing input is reported as missing, never invented.
"""

import json
import math


def portable_gbdt_importance(portable_path):
    """Split-count importance per feature, read from the portable artifact."""
    with open(portable_path, encoding="utf-8") as handle:
        data = json.load(handle)
    features = list(data["features"])
    counts = {name: 0 for name in features}
    for tree in data["trees"]:
        for node, left in enumerate(tree["left"]):
            if left != -1:
                counts[features[tree["feature"][node]]] += 1
    total = sum(counts.values())
    if total == 0:
        return {name: 0.0 for name in features}
    return {name: round(count / total, 6) for name, count in counts.items()}


def top_contributing_features(features, feature_ranges, importance, limit=5):
    """Rank features by importance x distance outside/inside their range.

    A feature contributes when it is both important to the model and unusual
    in this sample: deviation is the distance from the range midpoint,
    normalized by the range width, for features the caller supplied ranges for.
    """
    scored = []
    for name, value in (features or {}).items():
        if name not in (feature_ranges or {}) or name not in (importance or {}):
            continue
        try:
            value = float(value)
            low = float(feature_ranges[name]["min"])
            high = float(feature_ranges[name]["max"])
        except (TypeError, ValueError, KeyError):
            continue
        if not math.isfinite(value) or high <= low:
            continue
        midpoint = (low + high) / 2.0
        deviation = abs(value - midpoint) / (high - low)
        scored.append(dict(feature=name, value=value, range=[low, high],
                           importance=importance[name],
                           contribution=round(importance[name] * deviation, 6)))
    return sorted(scored, key=lambda row: -row["contribution"])[:limit]


def build_explanation(*, timestamp, active_path, candidate, source, reason,
                      signals, predictions=None, intervals=None,
                      features=None, feature_ranges=None, importance=None,
                      threshold=None):
    """One JSON-serializable explanation record for a single decision."""
    signals = {str(k): (None if v is None else float(v)) for k, v in (signals or {}).items()}
    explanation = dict(
        timestamp=timestamp,
        active_path=active_path,
        candidate=candidate,
        source=source,
        reason=reason,
        threshold=threshold,
        signals=signals,
        predictions={str(k): (None if v is None else float(v))
                     for k, v in (predictions or {}).items()},
        interval=None,
        top_features=top_contributing_features(features, feature_ranges, importance),
    )
    interval = (intervals or {}).get(active_path)
    if isinstance(interval, dict):
        explanation["interval"] = {key: interval.get(key) for key in
                                   ("lower", "upper", "width", "nominal_coverage",
                                    "in_domain", "confidence_gate_pass", "gate_reason")}
    if candidate and candidate in signals and active_path in signals:
        explanation["projected_improvement_points"] = round(
            signals[active_path] - signals[candidate], 3)
    return explanation


EXPLANATION_FIELDS = ["timestamp", "active_path", "candidate", "source", "reason",
                      "signal_a", "signal_b", "prediction", "lower", "upper",
                      "top_features", "projected_improvement_points"]


def explanation_csv_row(explanation):
    signals = explanation.get("signals") or {}
    interval = explanation.get("interval") or {}
    return dict(
        timestamp=explanation.get("timestamp"),
        active_path=explanation.get("active_path"),
        candidate=explanation.get("candidate") or "",
        source=explanation.get("source"),
        reason=explanation.get("reason"),
        signal_a=signals.get("A", ""),
        signal_b=signals.get("B", ""),
        prediction=(explanation.get("predictions") or {}).get(explanation.get("active_path"), ""),
        lower=interval.get("lower", ""),
        upper=interval.get("upper", ""),
        top_features=";".join("%s:%s" % (row["feature"], row["contribution"])
                              for row in explanation.get("top_features", [])),
        projected_improvement_points=explanation.get("projected_improvement_points", ""),
    )
