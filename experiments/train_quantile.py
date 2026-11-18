"""Train distributional (quantile) forecasters on a prepared dataset CSV.

The dataset must contain the model feature columns (from
cnt-ml/models/feature_list.json unless --features is given) and a target
column with the future utilization. Chronological holdout (last 30%):
reports pinball loss and empirical coverage per quantile for both the
sklearn GBDT quantile models and the NumPy linear quantile baseline, and
saves the GBDT models for tail-risk routing.

Usage: python3 -m experiments.train_quantile DATA.csv [--target y]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml" / "src"))

import numpy as np
import pandas as pd
from quantile_models import (LinearQuantileRegressor, pinball_loss,
                             quantile_coverage, train_gbdt_quantiles)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("data")
    parser.add_argument("--target", default="target")
    parser.add_argument("--features")
    parser.add_argument("--quantiles", default="0.5,0.9,0.99")
    parser.add_argument("--out", default=str(ROOT / "cnt-ml" / "models" / "quantile_gbdt.joblib"))
    parser.add_argument("--metrics", default=str(ROOT / "cnt-ml" / "results" / "quantile_metrics.json"))
    args = parser.parse_args()

    frame = pd.read_csv(args.data)
    if args.features:
        features = json.loads(Path(args.features).read_text())
        features = features["features"] if isinstance(features, dict) else features
    else:
        schema = json.loads((ROOT / "cnt-ml" / "models" / "feature_list.json").read_text())
        features = schema["features"]
    missing = [name for name in features + [args.target] if name not in frame]
    if missing:
        raise SystemExit("Dataset is missing columns: %s" % missing)
    quantiles = tuple(float(q) for q in args.quantiles.split(","))
    X = frame[features].to_numpy(dtype=float)
    y = frame[args.target].to_numpy(dtype=float)
    split = int(len(frame) * 0.7)
    X_train, X_test, y_train, y_test = X[:split], X[split:], y[:split], y[split:]

    gbdt = train_gbdt_quantiles(X_train, y_train, quantiles)
    linear = LinearQuantileRegressor(quantiles).fit(X_train, y_train)
    report = dict(features=features, target=args.target, quantiles=list(quantiles),
                  train_rows=int(split), test_rows=int(len(y_test)),
                  gbdt={str(q): dict(pinball_loss=round(pinball_loss(y_test, gbdt[q].predict(X_test), q), 4))
                        for q in quantiles},
                  linear={str(q): dict(pinball_loss=round(pinball_loss(y_test, linear.predict(X_test, q), q), 4))
                          for q in quantiles},
                  gbdt_coverage=quantile_coverage(y_test, {q: gbdt[q].predict(X_test) for q in quantiles}),
                  linear_coverage=quantile_coverage(y_test, {q: linear.predict(X_test, q) for q in quantiles}))
    try:
        import joblib
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(dict(models=gbdt, features=features, quantiles=list(quantiles)), args.out)
        report["saved_models"] = args.out
    except Exception as error:  # joblib optional; metrics remain the deliverable
        report["saved_models"] = None
        report["save_error"] = str(error)
    Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
    Path(args.metrics).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
