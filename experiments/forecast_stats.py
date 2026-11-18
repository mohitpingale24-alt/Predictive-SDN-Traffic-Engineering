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
        return dict(n=n, mean_loss_diff=0.0, dm_stat=0.0, p_value=1.0,
                    significant_5pct=False)
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


def compare_captures(capture_dirs, horizon_seconds=5):
    series = {}
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
    report = {}
    names = sorted(series)
    for name in names:
        pairs = series[name]
        errors = [p - a for p, a in pairs]
        report[name] = dict(pairs=len(pairs),
                            mae=round(sum(abs(e) for e in errors) / len(errors), 4),
                            rmse=round(math.sqrt(sum(e * e for e in errors) / len(errors)), 4))
    dm = {}
    for index, name in enumerate(names):
        for other in names[index + 1:]:
            shared = min(len(series[name]), len(series[other]))
            errors_a = [p - a for p, a in series[name][:shared]]
            errors_b = [p - a for p, a in series[other][:shared]]
            try:
                dm["%s vs %s" % (name, other)] = diebold_mariano(errors_a, errors_b, horizon_seconds)
            except ValueError as error:
                dm["%s vs %s" % (name, other)] = dict(error=str(error))
    return dict(systems=report, diebold_mariano=dm)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("captures", nargs="+")
    parser.add_argument("--horizon", type=int, default=5)
    args = parser.parse_args()
    print(json.dumps(compare_captures(args.captures, args.horizon), indent=2))


if __name__ == "__main__":
    main()
