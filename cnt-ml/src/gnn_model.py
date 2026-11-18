"""Graph neural network link forecaster (two-layer GCN, NumPy only).

The graph is the *line graph of the links*: each telemetry link is a node,
and two link-nodes are adjacent when the links share a switch — derived by
parsing the link identifiers recorded in the telemetry itself (e.g.
"S1-S2" and "S2-S4" share S2), never from a hardcoded wiring list. The GCN
forecasts every link jointly, so a link's prediction uses its neighbours'
state; the per-link baselines it is compared against cannot.

No deep-learning framework is required: forward/backward passes and Adam
are implemented directly, which keeps the model runnable in the controller
VM and fully inspectable for review.
"""

import csv
import math
from pathlib import Path

import numpy as np


def line_graph_adjacency(link_ids):
    """Adjacency (with self loops added later) from link identifier overlap."""
    endpoints = []
    for link_id in link_ids:
        parts = [part for part in str(link_id).replace("_", "-").split("-") if part]
        endpoints.append(set(parts))
    n = len(link_ids)
    adjacency = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(n):
            if i != j and endpoints[i] & endpoints[j]:
                adjacency[i, j] = 1.0
    return adjacency


def normalized_adjacency(adjacency):
    with_self = adjacency + np.eye(len(adjacency))
    degree = with_self.sum(axis=1)
    inverse_sqrt = np.diag(1.0 / np.sqrt(np.maximum(degree, 1e-12)))
    return inverse_sqrt @ with_self @ inverse_sqrt


def build_dataset(telemetry_csv, horizon_steps=5, return_stamps=False):
    """Align per-link telemetry into graph snapshots.

    Features per link-node: utilization, throughput, packet_loss,
    utilization lag-1, and a 5-sample utilization mean — all computed from
    the recorded series. The target is the link's utilization at exactly
    ``horizon_steps`` seconds later. Returns (X, Y, link_ids) with X of
    shape (snapshots, links, features); with return_stamps=True it also
    returns each snapshot's timestamp, so callers can purge training
    snapshots whose targets fall inside the test period.
    """
    series = {}
    with open(telemetry_csv, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                stamp = int(float(row["timestamp"]))
                values = (float(row["utilization"]), float(row["throughput"]),
                          float(row["packet_loss"]))
            except (ValueError, KeyError, TypeError):
                continue
            series.setdefault(row["link_id"], {})[stamp] = values
    link_ids = sorted(series)
    common = None
    for link_id in link_ids:
        stamps = set(series[link_id])
        common = stamps if common is None else (common & stamps)
    stamps = sorted(common or [])
    if len(stamps) < 20:
        raise ValueError("Not enough aligned telemetry snapshots for a GNN dataset")
    # Windows are looked up by timestamp, never by row position: the
    # 5-sample history must be stamps s-4..s and the target the sample at
    # exactly s + horizon_steps. A sampling gap — or the seam where
    # pooled telemetry joins two runs — makes the snapshot incomplete
    # instead of silently borrowing rows from the wrong time or run.
    features, targets, kept_stamps = [], [], []
    for stamp in stamps:
        snapshot = []
        complete = True
        for link_id in link_ids:
            rows = series[link_id]
            if any((stamp - k) not in rows for k in range(5)) \
                    or (stamp + horizon_steps) not in rows:
                complete = False
                break
            history = [rows[stamp - k][0] for k in range(5)]
            util, throughput, loss = rows[stamp]
            snapshot.append([util, throughput, loss, history[1],
                             sum(history) / len(history)])
        if not complete:
            continue
        target = [[series[link_id][stamp + horizon_steps][0]]
                  for link_id in link_ids]
        features.append(snapshot)
        targets.append(target)
        kept_stamps.append(stamp)
    if len(features) < 10:
        raise ValueError("Not enough complete snapshots after alignment")
    X = np.asarray(features, dtype=float)
    Y = np.asarray(targets, dtype=float)
    if return_stamps:
        return X, Y, link_ids, kept_stamps
    return X, Y, link_ids


class GCNForecaster:
    def __init__(self, n_features, hidden=16, seed=0):
        rng = np.random.default_rng(seed)
        scale1 = math.sqrt(2.0 / n_features)
        scale2 = math.sqrt(2.0 / hidden)
        self.W1 = rng.normal(0, scale1, size=(n_features, hidden))
        # Residual self-projection: each node's own features reach the
        # hidden layer unmixed. Without it, a fully-connected 2-node
        # line graph has normalized adjacency [[.5,.5],[.5,.5]], the
        # first convolution averages the two links into one vector, and
        # the model emits IDENTICAL predictions for both links no
        # matter how different their inputs are.
        self.W0 = rng.normal(0, scale1, size=(n_features, hidden))
        self.b1 = np.zeros(hidden)
        self.W2 = rng.normal(0, scale2, size=(hidden, 1))
        # Output-layer self projection: the second convolution mixes
        # hidden states across nodes exactly like the first, so node
        # identity needs an unmixed path at this layer too.
        self.W2s = rng.normal(0, scale2, size=(hidden, 1))
        self.b2 = np.zeros(1)
        self.meta = {}
        self._adam = {}

    def _forward(self, graph, X):
        GX = np.einsum("nm,tmf->tnf", graph, X)
        Z1 = (np.einsum("tnf,fh->tnh", GX, self.W1)
              + np.einsum("tnf,fh->tnh", X, self.W0) + self.b1)
        H = np.maximum(Z1, 0.0)
        GH = np.einsum("nm,tmh->tnh", graph, H)
        Y = (np.einsum("tnh,ho->tno", GH, self.W2)
             + np.einsum("tnh,ho->tno", H, self.W2s) + self.b2)
        return Y, (GX, Z1, H, GH)

    def predict(self, graph, X):
        return self._forward(graph, X)[0][:, :, 0]

    def _adam_step(self, name, param, grad, lr, step):
        state = self._adam.setdefault(name, (np.zeros_like(param), np.zeros_like(param)))
        m, v = state
        m = 0.9 * m + 0.1 * grad
        v = 0.999 * v + 0.001 * grad * grad
        self._adam[name] = (m, v)
        m_hat = m / (1 - 0.9 ** step)
        v_hat = v / (1 - 0.999 ** step)
        return param - lr * m_hat / (np.sqrt(v_hat) + 1e-8)

    def fit(self, graph, X, Y, epochs=200, lr=0.01):
        graph = np.asarray(graph, dtype=float)
        losses = []
        for step in range(1, epochs + 1):
            prediction, (GX, Z1, H, GH) = self._forward(graph, X)
            error = prediction - Y
            losses.append(float(np.mean(error ** 2)))
            dY = 2.0 * error / error.size
            dW2 = np.einsum("tnh,tno->ho", GH, dY)
            dW2s = np.einsum("tnh,tno->ho", H, dY)
            db2 = dY.sum(axis=(0, 1))
            dGH = np.einsum("tno,ho->tnh", dY, self.W2)
            dH = (np.einsum("nm,tmh->tnh", graph, dGH)
                  + np.einsum("tno,ho->tnh", dY, self.W2s))
            dZ1 = dH * (Z1 > 0)
            dW1 = np.einsum("tnf,tnh->fh", GX, dZ1)
            dW0 = np.einsum("tnf,tnh->fh", X, dZ1)
            db1 = dZ1.sum(axis=(0, 1))
            self.W1 = self._adam_step("W1", self.W1, dW1, lr, step)
            self.W0 = self._adam_step("W0", self.W0, dW0, lr, step)
            self.b1 = self._adam_step("b1", self.b1, db1, lr, step)
            self.W2 = self._adam_step("W2", self.W2, dW2, lr, step)
            self.W2s = self._adam_step("W2s", self.W2s, dW2s, lr, step)
            self.b2 = self._adam_step("b2", self.b2, db2, lr, step)
        return losses

    def save(self, path, meta=None):
        # The artifact must be self-describing: the normalization the
        # model was trained under, the link order its node axis follows
        # and the forecast horizon are part of the model. Previously
        # they lived only in the training script's report, so a loaded
        # model could be fed raw, wrongly-ordered inputs with no way
        # to detect the mismatch.
        payload = dict(W1=self.W1, W0=self.W0, b1=self.b1,
                       W2=self.W2, W2s=self.W2s, b2=self.b2)
        combined = dict(self.meta)
        if meta:
            combined.update(meta)
        if combined:
            import json
            payload["meta_json"] = np.asarray(json.dumps(combined))
        np.savez(path, **payload)

    @classmethod
    def load(cls, path):
        data = np.load(path)
        model = cls(data["W1"].shape[0], hidden=data["W1"].shape[1])
        model.W1, model.b1, model.W2, model.b2 = data["W1"], data["b1"], data["W2"], data["b2"]
        # Artifacts saved before the residual projections existed get
        # ZERO projections, reproducing their original function exactly
        # rather than the random init from __init__.
        model.W0 = data["W0"] if "W0" in data else np.zeros_like(model.W0)
        model.W2s = data["W2s"] if "W2s" in data else np.zeros_like(model.W2s)
        if "meta_json" in data:
            import json
            model.meta = json.loads(str(data["meta_json"]))
        return model


def ridge_forecast(X_train, Y_train, X_test, ridge=1.0):
    """Per-node-independent ridge baseline on the same features."""
    snapshots, nodes, features = X_train.shape
    train = X_train.reshape(-1, features)
    target = Y_train.reshape(-1, 1)
    gram = train.T @ train + ridge * np.eye(features)
    weights = np.linalg.solve(gram, train.T @ target)
    test = X_test.reshape(-1, features)
    return (test @ weights).reshape(X_test.shape[0], nodes)


def evaluate(graph, X, Y, model):
    prediction = model.predict(graph, X)
    persistence = X[:, :, 0]
    target = Y[:, :, 0]
    def metrics(values):
        error = values - target
        return dict(mae=round(float(np.mean(np.abs(error))), 4),
                    rmse=round(float(np.sqrt(np.mean(error ** 2))), 4))
    return dict(gnn=metrics(prediction), persistence=metrics(persistence),
                gnn_errors=(prediction - target).ravel().tolist(),
                persistence_errors=(persistence - target).ravel().tolist())
