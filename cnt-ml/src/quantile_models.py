"""Distributional (quantile) utilization forecasting.

Two implementations with the same contract (fit / predict per quantile):

- ``LinearQuantileRegressor``: pinball-loss linear model trained by
  subgradient descent in pure NumPy — runs anywhere, serves as the
  dependency-free baseline.
- ``train_gbdt_quantiles``: sklearn GradientBoostingRegressor with
  loss="quantile", one model per level — the distributional counterpart
  of the project's point GBDT. Imported lazily so this module loads
  without sklearn.

Routing on a high quantile (e.g. P90) is tail-risk routing: the decision
uses the value the utilization will stay under with the stated
probability, instead of the mean forecast that hides spikes.
"""

import numpy as np


def pinball_loss(y_true, y_pred, quantile):
    error = np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float)
    return float(np.mean(np.maximum(quantile * error, (quantile - 1) * error)))


class LinearQuantileRegressor:
    def __init__(self, quantiles=(0.5, 0.9, 0.99), lr=0.05, epochs=300, seed=0):
        self.quantiles = tuple(float(q) for q in quantiles)
        self.lr = float(lr)
        self.epochs = int(epochs)
        self.seed = seed
        self.models = {}

    def fit(self, X, y):
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)
        self._mean = X.mean(axis=0)
        self._std = X.std(axis=0) + 1e-9
        Xn = (X - self._mean) / self._std
        rng = np.random.default_rng(self.seed)
        for quantile in self.quantiles:
            weights = np.zeros(X.shape[1])
            bias = float(np.quantile(y, quantile))
            for _ in range(self.epochs):
                order = rng.permutation(len(Xn))
                for i in order:
                    prediction = Xn[i] @ weights + bias
                    residual = y[i] - prediction
                    grad = -quantile if residual > 0 else (1 - quantile)
                    weights -= self.lr * grad * Xn[i] / math_sqrt(len(Xn))
                    bias -= self.lr * grad / math_sqrt(len(Xn))
            self.models[quantile] = (weights, bias)
        return self

    def predict(self, X, quantile):
        if quantile not in self.models:
            raise ValueError("Quantile %s was not fitted" % quantile)
        Xn = (np.asarray(X, dtype=float) - self._mean) / self._std
        weights, bias = self.models[quantile]
        return Xn @ weights + bias


def math_sqrt(value):
    return float(value) ** 0.5


def train_gbdt_quantiles(X, y, quantiles=(0.5, 0.9, 0.99), **kwargs):
    """One sklearn GBDT per quantile level. Returns {quantile: model}."""
    from sklearn.ensemble import GradientBoostingRegressor
    models = {}
    for quantile in quantiles:
        model = GradientBoostingRegressor(loss="quantile", alpha=float(quantile), **kwargs)
        model.fit(X, y)
        models[float(quantile)] = model
    return models


def quantile_coverage(y_true, predictions_by_quantile):
    """Empirical coverage per fitted level: fraction of actuals <= forecast."""
    y = np.asarray(y_true, dtype=float)
    return {float(q): round(float(np.mean(y <= np.asarray(p, dtype=float))), 4)
            for q, p in predictions_by_quantile.items()}
