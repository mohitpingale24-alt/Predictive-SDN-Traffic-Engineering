"""Train the GNN link forecaster and compare it honestly against baselines.

Chronological split (no shuffling — forecasting evaluation must not leak
the future). Comparators on the identical test snapshots: persistence and
a per-link ridge on the same features (the tabular stand-in). A
Diebold-Mariano test of GNN vs persistence errors is included so the
comparison carries a significance statement, not just two numbers.

Usage: python3 -m experiments.train_gnn TELEMETRY.csv [--horizon 5]
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml" / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

import numpy as np
from gnn_model import (GCNForecaster, build_dataset, evaluate,
                       line_graph_adjacency, normalized_adjacency, ridge_forecast)
from forecast_stats import diebold_mariano


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("telemetry")
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--hidden", type=int, default=16)
    parser.add_argument("--output", default=str(ROOT / "cnt-ml" / "models" / "gnn_link_forecaster.npz"))
    parser.add_argument("--metrics", default=str(ROOT / "cnt-ml" / "results" / "gnn_metrics.json"))
    args = parser.parse_args()

    X, Y, link_ids = build_dataset(args.telemetry, args.horizon)
    graph = normalized_adjacency(line_graph_adjacency(link_ids))
    split = int(len(X) * 0.7)
    X_train, X_test = X[:split], X[split:]
    Y_train, Y_test = Y[:split], Y[split:]
    mean, std = X_train.mean(axis=(0, 1)), X_train.std(axis=(0, 1)) + 1e-9
    X_train_n = (X_train - mean) / std
    X_test_n = (X_test - mean) / std
    target_mean, target_std = float(Y_train.mean()), float(Y_train.std() + 1e-9)
    Y_train_n = (Y_train - target_mean) / target_std

    model = GCNForecaster(X.shape[2], hidden=args.hidden)
    losses = model.fit(graph, X_train_n, Y_train_n, epochs=args.epochs)
    prediction = model.predict(graph, X_test_n) * target_std + target_mean
    target = Y_test[:, :, 0]

    def metrics(values):
        error = values - target
        return dict(mae=round(float(np.mean(np.abs(error))), 4),
                    rmse=round(float(np.sqrt(np.mean(error ** 2))), 4))

    ridge = ridge_forecast(X_train_n, Y_train, X_test_n) \
        if Y_train.ndim == 3 else None
    report = dict(
        links=link_ids, horizon_steps=args.horizon,
        train_snapshots=int(len(X_train)), test_snapshots=int(len(X_test)),
        initial_loss=round(losses[0], 4), final_loss=round(losses[-1], 4),
        gnn=metrics(prediction),
        persistence=metrics(X_test[:, :, 0]),
        ridge_same_features=metrics(ridge) if ridge is not None else None,
        diebold_mariano_gnn_vs_persistence=diebold_mariano(
            (prediction - target).ravel().tolist(),
            (X_test[:, :, 0] - target).ravel().tolist(), horizon=args.horizon),
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    model.save(args.output)
    Path(args.metrics).parent.mkdir(parents=True, exist_ok=True)
    Path(args.metrics).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
