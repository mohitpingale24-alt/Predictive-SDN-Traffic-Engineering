"""Tests for the research-grade extension (Tier 1 + GNN + drift adaptation).

Every claim is checked behaviourally on generated data: conformal coverage
meets its nominal level, ACI adapts under shift, the DM test detects a
real accuracy difference and not a fake one, detectors fire on shifted
error streams, adaptation beats no-adaptation after a shift, and the GNN
beats persistence when the data-generating process actually couples
neighbouring links.
"""

import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "cnt-ml" / "src"))
sys.path.insert(0, str(ROOT / "experiments"))

from conformal import (AdaptiveConformal, ConformalizedQuantile, SplitConformal,
                       conformal_quantile, coverage_report)
from forecast_stats import diebold_mariano, interval_calibration, build_pairs
from drift_adaptation import AdwinLite, PageHinkley, evaluate_strategies
from quantile_models import LinearQuantileRegressor, quantile_coverage


def test_split_conformal_hits_nominal_coverage():
    rng = np.random.default_rng(7)
    actuals = rng.uniform(10, 90, size=6000)
    predictions = actuals + rng.normal(0, 5, size=6000)
    calibrator = SplitConformal(alpha=0.1).calibrate(predictions[:1000], actuals[:1000])
    intervals = [calibrator.interval(p) for p in predictions[1000:]]
    report = coverage_report(intervals, actuals[1000:])
    assert report["coverage"] >= 0.885  # guarantee is >= 0.90 in expectation
    assert conformal_quantile([1, 2, 3, 4], 0.5) >= 2


def test_cqr_improves_raw_quantile_coverage():
    rng = np.random.default_rng(3)
    x = rng.uniform(0, 10, size=3000)
    y = 5 * x + rng.normal(0, 2 + x, size=3000)  # heteroscedastic
    model = LinearQuantileRegressor((0.05, 0.95), epochs=120).fit(x.reshape(-1, 1), y)
    lo = model.predict(x.reshape(-1, 1), 0.05)
    hi = model.predict(x.reshape(-1, 1), 0.95)
    cqr = ConformalizedQuantile(0.1).calibrate(lo[:1000], hi[:1000], y[:1000])
    raw = coverage_report(list(zip(lo[1000:], hi[1000:])), y[1000:])
    fixed = coverage_report([cqr.interval(a, b) for a, b in zip(lo[1000:], hi[1000:])], y[1000:])
    # CQR calibrates toward the nominal 0.90 in either direction: the raw
    # band over-covers here, so the corrected band must land closer to 0.90.
    assert abs(fixed["coverage"] - 0.90) < abs(raw["coverage"] - 0.90)


def test_aci_recovers_coverage_after_shift():
    rng = np.random.default_rng(11)
    calm_p = rng.normal(50, 1, 500)
    calm_a = calm_p + rng.normal(0, 2, 500)
    aci = AdaptiveConformal(0.1, gamma=0.02).calibrate(calm_p, calm_a)
    static = SplitConformal(0.1).calibrate(calm_p, calm_a)
    shifted_a = rng.normal(50, 12, 800)  # same centre, far wider spread
    shifted_p = np.full(800, 50.0)
    aci_hits = static_hits = 0
    for p, a in zip(shifted_p, shifted_a):
        lo, hi = aci.interval(p)
        aci_hits += lo <= a <= hi
        aci.update(p, a)
        lo, hi = static.interval(p)
        static_hits += lo <= a <= hi
    assert aci_hits / 800 > static_hits / 800


def test_diebold_mariano_detects_real_and_fake_differences():
    rng = np.random.default_rng(5)
    good = rng.normal(0, 1, 400)
    bad = rng.normal(0, 3, 400)
    result = diebold_mariano(list(good), list(bad))
    assert result["significant_5pct"] and result["dm_stat"] < 0
    same = diebold_mariano(list(good), list(good))
    assert same["p_value"] == 1.0 and not same["significant_5pct"]
    calibration = interval_calibration([(0.0, 100.0)] * 50, [50.0] * 50, 0.9)
    assert calibration["picp"] == 1.0 and calibration["mean_width"] == 100.0


def test_build_pairs_aligns_by_horizon(tmp_path):
    (tmp_path / "telemetry.csv").write_text(
        "timestamp,link_id,utilization,throughput,delay,packet_loss,active_flows\n"
        + "".join("%d,S1-S2,%d,1,1,0,1\n" % (t, t) for t in range(20)))
    (tmp_path / "predictions.csv").write_text(
        "timestamp,control_mode,active_path,path,link_id,history_count,prediction_status,"
        "current_utilization,signal_utilization,predicted_utilization,inference_time_ms\n"
        "0,predictive,A,A,S1-S2,11,predicted,0,42,42,1\n")
    pairs = build_pairs(tmp_path, horizon_seconds=5)
    assert pairs == [(42.0, 5.0)]


def test_detectors_fire_on_shifted_error_stream():
    rng = np.random.default_rng(2)
    ph = PageHinkley(delta=0.05, threshold=5.0)
    adwin = AdwinLite()
    fired_ph = fired_adwin = False
    stream = list(rng.normal(1.0, 0.2, 120)) + list(rng.normal(9.0, 0.5, 120))
    for value in stream:
        fired_ph = ph.observe(value) or fired_ph
        fired_adwin = adwin.observe(value) or fired_adwin
    assert fired_ph and fired_adwin


def test_online_adaptation_beats_none_after_shift():
    rng = np.random.default_rng(9)
    calm = [(50.0, 50.0 + e) for e in rng.normal(0, 1, 60)]
    shifted = [(50.0, 50.0 + e) for e in rng.normal(0, 10, 400)]
    report = evaluate_strategies(calm + shifted, calibration_fraction=0.13,
                                 coverage=0.9, window=60)
    assert report["online"]["coverage_error"] < report["none"]["coverage_error"]
    assert report["periodic"]["final_radius"] > report["none"]["final_radius"]


def test_linear_quantile_orders_and_covers():
    rng = np.random.default_rng(4)
    x = rng.uniform(0, 5, size=(1500, 1))
    y = (3 * x[:, 0] + rng.normal(0, 1, 1500)).clip(0, 100)
    model = LinearQuantileRegressor((0.5, 0.9), epochs=150).fit(x, y)
    coverage = quantile_coverage(y, {0.5: model.predict(x, 0.5), 0.9: model.predict(x, 0.9)})
    assert coverage[0.9] > coverage[0.5]
    assert 0.8 <= coverage[0.9] <= 0.99


def test_conformal_runtime_matches_controller_contract(tmp_path):
    rng = np.random.default_rng(6)
    predictions = rng.uniform(20, 80, 200)
    actuals = predictions + rng.normal(0, 3, 200)
    calibrator = SplitConformal(0.1).calibrate(predictions, actuals)
    aci = AdaptiveConformal(0.1, 0.01).calibrate(predictions, actuals)
    artifact = dict(method="split_conformal_absolute_residual", alpha=0.1,
                    nominal_coverage=0.9,
                    radius_percentage_points=calibrator.radius,
                    aci=dict(gamma=0.01, scores=[round(s, 4) for s in aci._scores]))
    path = tmp_path / "conformal_calibration.json"
    path.write_text(json.dumps(artifact))
    sys.path.insert(0, str(ROOT))
    from conformal_runtime import ConformalRuntime
    runtime = ConformalRuntime(str(path), threshold=85.0)
    interval = runtime.interval(95.0)
    for key in ("lower", "upper", "width", "nominal_coverage", "in_domain",
                "confidence_gate_pass", "gate_reason"):
        assert key in interval
    assert interval["confidence_gate_pass"] == (interval["lower"] >= 85.0)
    before = runtime.radius
    for _ in range(50):
        runtime.update(50.0, 100.0)  # repeated misses must widen the interval
    assert runtime.radius > before


def test_gnn_beats_persistence_on_coupled_links(tmp_path):
    from gnn_model import (GCNForecaster, build_dataset, line_graph_adjacency,
                           normalized_adjacency)
    rng = np.random.default_rng(13)
    links = ["S1-S2", "S2-S4", "S1-S3", "S3-S4"]
    adjacency = line_graph_adjacency(links)
    steps = 160
    series = np.zeros((steps, len(links)))
    series[0] = rng.uniform(20, 40, len(links))
    for t in range(1, steps):
        neighbour = (adjacency @ series[t - 1]) / np.maximum(adjacency.sum(axis=1), 1)
        series[t] = np.clip(0.45 * series[t - 1] + 0.45 * neighbour
                            + rng.normal(0, 0.7, len(links)), 0, 100)
    rows = ["timestamp,link_id,utilization,throughput,delay,packet_loss,active_flows"]
    for t in range(steps):
        for j, link in enumerate(links):
            rows.append("%d,%s,%.3f,%.3f,1.0,0.0,1" % (1000 + t, link, series[t, j], series[t, j] / 10))
    csv_path = tmp_path / "telemetry.csv"
    csv_path.write_text("\n".join(rows) + "\n")
    X, Y, link_ids = build_dataset(str(csv_path), horizon_steps=2)
    graph = normalized_adjacency(line_graph_adjacency(link_ids))
    split = int(len(X) * 0.7)
    mean, std = X[:split].mean(axis=(0, 1)), X[:split].std(axis=(0, 1)) + 1e-9
    Xn = (X - mean) / std
    target_mean, target_std = float(Y[:split].mean()), float(Y[:split].std() + 1e-9)
    model = GCNForecaster(X.shape[2], hidden=16, seed=1)
    losses = model.fit(graph, Xn[:split], (Y[:split] - target_mean) / target_std,
                       epochs=250, lr=0.01)
    prediction = model.predict(graph, Xn[split:]) * target_std + target_mean
    target = Y[split:, :, 0]
    gnn_rmse = float(np.sqrt(np.mean((prediction - target) ** 2)))
    persistence_rmse = float(np.sqrt(np.mean((X[split:, :, 0] - target) ** 2)))
    assert losses[-1] < losses[0]
    assert gnn_rmse < persistence_rmse
