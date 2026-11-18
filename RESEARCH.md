# Research extension — paper framing

Added 2026-11-18: Tier 1 (conformal prediction, distributional
forecasting, forecast statistics) + GNN link forecasting + drift
adaptation. Everything is opt-in; the verified campaign-02 results and
the legacy control modes are untouched.

## The claim (one sentence)
Predictive SDN rerouting is only trustworthy when its forecasts carry a
**coverage guarantee** and the system **adapts when the traffic regime
shifts** — this work provides both, and evaluates them harder than the
point-forecast baseline it replaces.

## Contributions
1. **Conformal rerouting intervals** (`cnt-ml/src/conformal.py`,
   `conformal_runtime.py`, `experiments/calibrate_conformal.py`):
   split-conformal intervals with the finite-sample correction replace
   the exploratory residual band; CQR wraps the quantile forecaster;
   ACI (Gibbs & Candès) adapts the miscoverage level online. The
   controller consumes them through the *same* policy interface
   (`SDN_CONFORMAL=1`), so the only change is interval provenance.
2. **Distributional forecasting + tail-risk routing**
   (`cnt-ml/src/quantile_models.py`, `experiments/train_quantile.py`):
   quantile GBDT (and a NumPy linear-QR baseline) at P50/P90/P99;
   `SDN_TAIL_RISK_QUANTILE=0.9` routes on the tail estimate instead of
   the mean forecast.
3. **Forecast statistics** (`experiments/forecast_stats.py`):
   Diebold–Mariano tests (Newey–West HAC + Harvey–Leybourne–Newbold
   correction) for every model comparison, and interval calibration
   (PICP, mean width, coverage error) — no claim rests on two bare
   averages again.
4. **GNN link forecasting** (`cnt-ml/src/gnn_model.py`,
   `experiments/train_gnn.py`): a two-layer GCN over the link line-graph
   (adjacency derived from the recorded link identifiers) forecasting
   all links jointly, compared on a chronological split against
   persistence and a per-link ridge on identical features, with a DM
   test on the difference. On coupled synthetic telemetry it beats
   persistence significantly (DM −5.93, p < 0.001 in the smoke run) —
   the real-data verdict comes from running it on campaign telemetry.
5. **Drift adaptation, measured** (`drift_adaptation.py`): Page–Hinkley
   and ADWIN-style detectors on the forecast-error stream (logged as
   controller events), plus three radius strategies — none / periodic /
   online recalibration — evaluated head-to-head on the same stream.
   `SDN_DRIFT_ADAPTATION=online` closes the loop in the controller.

## Evaluation protocol for the paper
- Chronological splits everywhere; no shuffled forecasting evaluation.
- Every model comparison ships a DM statistic and p-value, not just
  MAE/RMSE deltas.
- Every interval ships empirical coverage vs nominal, on calibration
  *and* on post-shift segments separately.
- GNN ablation: GNN vs ridge on identical features isolates the value
  of topology information; if the GNN does not beat the ridge on real
  telemetry, that negative result is reported, not buried.
- Drift claims come from the shifted segments of the harder workloads
  (bursty-mixed, elephant-mice), with detectors compared on the same
  error stream.

## Threats to validity (state them before a reviewer does)
- Conformal coverage is *marginal* and assumes exchangeability within
  the calibration window; ACI mitigates but does not restore a strict
  guarantee under arbitrary shift.
- The GNN's advantage depends on real spatial coupling in the traffic;
  the synthetic smoke test has coupling by construction.
- Tail-risk routing trades congestion safety for potentially more
  reroutes; reroute counts must be reported alongside congestion.

## Verify
```
python3 -m pytest tests/test_research_grade.py -q   # 10 tests
python3 experiments/train_gnn.py <telemetry.csv>    # GNN vs baselines + DM
python3 experiments/calibrate_conformal.py <capture-dir>
python3 experiments/forecast_stats.py <capture-dir> [...]
```

## All-link telemetry (added after the first real-data GNN run)

The first GNN evaluation on campaign-02 telemetry was a **negative
result** (GNN MAE 25.8 vs persistence 14.9; DM p = 0.888), and the cause
was structural: captures recorded only the two s1 uplinks, so the line
graph had 2 nodes and 1 edge, and a run yielded only 91 aligned
snapshots. The controller now records every core link (link_map.py;
canonical endpoint per link; `telemetry_all_links.csv` beside the legacy
telemetry file, which is unchanged). `experiments/pool_telemetry.py`
pools captures across runs with per-run timestamp offsets, giving the
GNN a 4-link diamond line graph and hundreds of snapshots. The GNN claim
must be re-evaluated on that data; the two-link result stands in the
paper as the motivation, whatever the pooled verdict is.

## Campaign-02 forecast comparison (real data, 2026-11-18)

Pooled Diebold-Mariano, predictive-ml vs predictive-baseline
(persistence), squared loss, 8 matched runs, n = 1370 pairs:
mean loss diff -108.7 (favours ML), DM -1.18, **p = 0.239 — not
significant**. Every one of the 8 runs individually favours ML on
squared loss (mean diffs -92 to -126), so the *direction* is consistent
(sign test on 8/8 runs: p ~= 0.008), but a few workload-transition
spikes dominate the loss variance and the DM test cannot certify the
magnitude. On absolute loss the picture reverses (run-01 MAE:
persistence 10.83 vs ML 14.50): the ML forecaster helps at transitions
and is slightly worse in calm periods. Conformal calibration on
run-01 held its guarantee (empirical coverage 0.907 at nominal 0.90;
radius 38.5 points, mean width 59.9) — the intervals honestly reflect
this forecast difficulty. Paper claim accordingly: no certified
point-forecast superiority; the contribution is the uncertainty-aware
gating around an uncertified forecaster.
