"""Forecast-comparison statistics: Diebold-Mariano and interval calibration.

Pure standard library (the normal CDF comes from math.erf, so there is no
SciPy dependency). The Diebold-Mariano test uses a Newey-West HAC variance
with the Harvey-Leybourne-Newbold small-sample correction — the standard
form reported in forecasting papers. Interval calibration reports PICP
(empirical coverage), mean width, and the coverage error against the
nominal level, per system, from recorded prediction/actual pairs.

CLI: python3 -m experiments.forecast_stats CAPTURE_DIR [CAPTURE_DIR ...]
Pairs are built from each capture's predictions.csv: a forecast made at t
is scored against the same link's measured utilization at t + horizon in
telemetry.csv (exact-timestamp alignment, the project's existing contract).

Diebold-Mariano comparisons are only ever made between forecasters that
predicted the SAME targets: the recorded ML forecasts and persistence,
both scored against the identical (timestamp, link) actuals of one
capture. Errors from different captures are never paired by row
position — different controllers change their own future utilization,
so position i in two runs is not a shared forecast target and a paired
test across runs is meaningless. And even the valid same-target test
carries a caveat (emitted as dm_caveat in the report): the errors come
from live runs whose routing decisions changed the traffic being
predicted, so the result is matched-workload evidence, not a pure
held-out forecast comparison on intervention-free data.
"""

import argparse
import csv
import json
import math
from pathlib import Path


def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def diebold_mariano(errors_a, errors_b, horizon=1, loss="squared"):
    """DM test of equal predictive accuracy. Negative stat favours A."""
    if len(errors_a) != len(errors_b) or len(errors_a) < 10:
        raise ValueError("DM needs two aligned error series of length >= 10")
    if loss == "squared":
        diffs = [a * a - b * b for a, b in zip(errors_a, errors_b)]
    elif loss == "absolute":
        diffs = [abs(a) - abs(b) for a, b in zip(errors_a, errors_b)]
    else:
        raise ValueError("loss must be 'squared' or 'absolute'")
    n = len(diffs)
    mean = sum(diffs) / n
    if all(abs(d - mean) < 1e-15 for d in diffs):
        if abs(mean) < 1e-15:
            return dict(n=n, mean_loss_diff=0.0, dm_stat=0.0, p_value=1.0,
                        significant_5pct=False)
        # A constant NONZERO loss difference is a real, uniform
        # advantage — not equality. Its sampling variance is zero, so
        # the DM statistic itself is undefined (division by zero), not
        # zero. Report the mean faithfully and mark inference degenerate
        # instead of fabricating p = 1.
        return dict(n=n, mean_loss_diff=round(mean, 4), dm_stat=None,
                    p_value=None, significant_5pct=False, degenerate=True,
                    note="zero-variance loss differences: every paired "
                         "observation favours the same forecaster, but "
                         "the DM statistic is undefined")
    max_lag = max(1, int(round(n ** (1 / 3)))) if horizon == 1 else horizon - 1
    variance = sum((d - mean) ** 2 for d in diffs) / n
    for lag in range(1, max_lag + 1):
        covariance = sum((diffs[t] - mean) * (diffs[t - lag] - mean)
                         for t in range(lag, n)) / n
        variance += 2 * (1 - lag / (max_lag + 1)) * covariance
    if variance <= 0:
        raise ValueError("Non-positive long-run variance in loss differences")
    dm = mean / math.sqrt(variance / n)
    # Harvey-Leybourne-Newbold correction for small samples / horizon h.
    h = max(1, int(horizon))
    factor = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    dm_hln = dm * factor
    p_value = 2 * (1 - _norm_cdf(abs(dm_hln)))
    return dict(n=n, mean_loss_diff=round(mean, 4), dm_stat=round(dm_hln, 4),
                p_value=round(p_value, 6), significant_5pct=p_value < 0.05)


def interval_calibration(intervals, actuals, nominal_coverage):
    rows = [(float(lo), float(hi), float(a)) for (lo, hi), a in zip(intervals, actuals)
            if all(math.isfinite(v) for v in (float(lo), float(hi), float(a)))]
    if not rows:
        raise ValueError("No finite interval rows")
    covered = sum(1 for lo, hi, a in rows if lo <= a <= hi)
    picp = covered / len(rows)
    return dict(n=len(rows), nominal_coverage=float(nominal_coverage),
                picp=round(picp, 4),
                coverage_error=round(abs(picp - float(nominal_coverage)), 4),
                mean_width=round(sum(hi - lo for lo, hi, _ in rows) / len(rows), 4))


def _read_csv(path):
    if not Path(path).exists():
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def build_pairs(capture_dir, horizon_seconds=5):
    """(prediction, actual_at_t+horizon) pairs from one recorded capture."""
    capture_dir = Path(capture_dir)
    telemetry = {}
    for row in _read_csv(capture_dir / "telemetry.csv"):
        try:
            telemetry[(int(float(row["timestamp"])), row["link_id"])] = float(row["utilization"])
        except (ValueError, KeyError, TypeError):
            continue
    pairs = []
    for row in _read_csv(capture_dir / "predictions.csv"):
        if row.get("prediction_status") != "predicted":
            continue
        try:
            stamp = int(float(row["timestamp"]))
            predicted = float(row["predicted_utilization"])
        except (ValueError, KeyError, TypeError):
            continue
        actual = telemetry.get((stamp + int(horizon_seconds), row["link_id"]))
        if actual is not None:
            pairs.append((predicted, actual))
    return pairs


def build_aligned_errors(capture_dir, horizon_seconds=5):
    """(ml_error, persistence_error) pairs sharing identical targets.

    Both forecasters are scored against the same (timestamp, link)
    actual: the recorded ML prediction made at t, and persistence
    (utilization measured at t), against utilization at t + horizon.
    Only rows where both forecasts and the actual exist are returned,
    which is what makes a paired Diebold-Mariano test valid here.
    """
    capture_dir = Path(capture_dir)
    telemetry = {}
    for row in _read_csv(capture_dir / "telemetry.csv"):
        try:
            telemetry[(int(float(row["timestamp"])), row["link_id"])] = float(row["utilization"])
        except (ValueError, KeyError, TypeError):
            continue
    aligned = []
    for row in _read_csv(capture_dir / "predictions.csv"):
        if row.get("prediction_status") != "predicted":
            continue
        try:
            stamp = int(float(row["timestamp"]))
            predicted = float(row["predicted_utilization"])
        except (ValueError, KeyError, TypeError):
            continue
        actual = telemetry.get((stamp + int(horizon_seconds), row["link_id"]))
        persistence = telemetry.get((stamp, row["link_id"]))
        if actual is not None and persistence is not None:
            aligned.append((predicted - actual, persistence - actual))
    return aligned


def compare_captures(capture_dirs, horizon_seconds=5):
    series = {}
    aligned_by_system = {}
    for directory in capture_dirs:
        pairs = build_pairs(directory, horizon_seconds)
        if pairs:
            path = Path(directory)
            # Captures are all named run-XX; key by system/run to avoid
            # silently overwriting one system with another.
            key = "%s/%s" % (path.parent.name, path.name)
            if key in series:
                key = str(path)
            series[key] = pairs
            aligned_by_system.setdefault(path.parent.name, []).extend(
                build_aligned_errors(directory, horizon_seconds))
    report = {}
    for name in sorted(series):
        pairs = series[name]
        errors = [p - a for p, a in pairs]
        report[name] = dict(pairs=len(pairs),
                            mae=round(sum(abs(e) for e in errors) / len(errors), 4),
                            rmse=round(math.sqrt(sum(e * e for e in errors) / len(errors)), 4))
    dm = {}
    for system in sorted(aligned_by_system):
        aligned = aligned_by_system[system]
        if len(aligned) >= 10:
            try:
                dm["%s: ml vs persistence (same targets)" % system] = diebold_mariano(
                    [ml for ml, _ in aligned], [pe for _, pe in aligned],
                    horizon_seconds)
            except ValueError as error:
                dm["%s: ml vs persistence (same targets)" % system] = dict(error=str(error))
    return dict(systems=report, diebold_mariano=dm,
                cross_system_dm="not reported: different controllers induce "
                                "different futures, so errors from different "
                                "systems share no common target and cannot be "
                                "paired for a Diebold-Mariano test",
                dm_caveat="these Diebold-Mariano figures pool errors from "
                          "live controller runs whose routing decisions "
                          "changed the traffic being predicted: they are "
                          "matched-workload evidence (identical realized "
                          "targets, same intervention regime), not a pure "
                          "held-out forecast comparison on "
                          "intervention-free data")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+")
    parser.add_argument("--horizon", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(compare_captures(args.captures, args.horizon), indent=2))


if __name__ == "__main__":
    main()
