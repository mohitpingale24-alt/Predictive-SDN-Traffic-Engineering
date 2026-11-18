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
   correction) for forecast comparisons that share identical targets —
   ML vs persistence scored against the same recorded actuals — and
   interval calibration (PICP, mean width, coverage error). Cross-system
   DM is deliberately not computed: different controllers induce
   different futures, so their errors cannot be paired.
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
- Every interval ships empirical coverage vs nominal measured on
  held-out data the radius never saw (chronological split, plus an
  untouched eval capture where available) — never on calibration rows.
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

## Campaign-02 forecast comparison (real data, 2026-11-18; corrected)

An earlier version of this section reported a pooled cross-system DM
(p = 0.239, mean loss diff -108.7). That test paired the ML system's
errors with the persistence *system's* errors by row position — but
the two controllers induce different futures, so the paired errors
were forecasts of different targets and the test was invalid. It is
retracted. The valid same-target comparison (ML forecasts vs
persistence, both scored against the identical recorded actuals of
the predictive-ml captures, pooled across 8 runs, n = 1378 aligned
pairs): mean loss diff -24.4 (favours ML), DM -1.41, **p = 0.157 —
not significant**. On absolute loss the picture reverses (run-01 MAE:
persistence 10.83 vs ML 14.50): the ML forecaster helps at transitions
and is slightly worse in calm periods. One caveat applies to this and
to every DM figure in the project: the pooled errors come from live
controller runs, and the routing decisions taken during those runs
changed the traffic that was subsequently measured and predicted. The
comparison is matched-workload evidence — both forecasters scored on
identical realized targets under the same intervention regime — not
a pure held-out forecast comparison on intervention-free data; a
forecaster whose predictions trigger reroutes partly authors its own
future targets, and no pairing of the recorded rows removes that.
Conformal calibration was also
re-measured honestly: the previously quoted 0.907 coverage was
computed on the calibration rows themselves and is retracted. With a
chronological split (103 calibration rows, radius 26.8 points), the
held-out coverage is **0.812 at nominal 0.90** (n = 69); ACI's
sequential held-out coverage is 0.855, and an untouched second
capture gives 0.862 (n = 174). The intervals undercover out of
sample — the exchangeability assumption does not survive the
workload shift, and ACI closes only part of the gap. Paper claim
accordingly: no certified point-forecast superiority and no
empirically held coverage guarantee on this data; the contribution
is the uncertainty-aware gating around an uncertified forecaster,
with its interval quality measured, not assumed.

One consequence must be stated plainly: with the honest radius of
26.8 points, the confidence gate is **arithmetically dormant**.
Predictions are clipped at 100, so the highest lower bound any
forecast can produce is 100 − 26.8 = 73.2 — below the 85 threshold
the gate requires. No live decision can pass the conformal gate with
this calibration; `decisions.csv` records the fallback source for
every affected decision, and the calibration artifact itself now
carries `gate_diagnostics.gate_can_activate = false` so the fact is
machine-visible, not buried in prose. The gate is not redesigned to
force activation (shrinking intervals or lowering the threshold to
manufacture passes would destroy the meaning of both); it activates
only if a future calibration on better forecasts yields a radius
under 15 points.

## GNN retrain after the review fixes (2026-11-18)

The GNN was retrained on the same input as the committed metrics
(campaign-02 predictive-ml run-01 telemetry) with the fixed code:
exact-timestamp windowing, boundary purge (5 snapshots), and residual
self-projections in both GCN layers (the original architecture
provably emitted identical predictions for both links on a 2-node
line graph). Result: GNN MAE 18.39 (was 25.81), RMSE 27.37;
persistence MAE 14.87, RMSE 26.11; DM p = 0.491, not significant. The
negative result stands, narrowed but not reversed; the full mapping
of review findings to fixes is in REVIEW_FIXES.md.
