# Weak-Metric Audit — campaign-02 (2026-11-18)

Every number below is computed from the saved campaign-02 artifacts
(32 runs; 1,370 forecast samples paired prediction → telemetry at +5 s,
the same pairing as the conformal calibration). Gate/blend/recalibration
parameters were selected leave-one-run-out: fit on 7 runs, scored on the
held-out run, pooled. Scripts: `analyze_weak.py`, `analyze_threshold.py`,
`analyze_recal.py` (VM copies in `~/workspace/sdn_hybrid/`).

## 1. Latency — not actually weak (earlier mean-only claim corrected)

| metric | Reactive | Predictive-GB | verdict |
|---|---|---|---|
| P95 latency | 16.675 ms, CI95 [16.570, 16.780] | 16.908 ms, CI95 [16.685, 17.130] | CIs overlap — these data do not separate the systems on this metric |
| Avg jitter | 0.055 ms, CI95 [0.029, 0.080] | 0.309 ms, CI95 [−0.183, 0.800] | CIs overlap widely — consistent with one high-variance run; not a demonstrated systematic gap |
| Avg latency | 11.837 ms | 12.574 ms | sub-ms, same story |

Static, for contrast: P95 125.9 ms, packet loss 11.4%, congestion 38.6 s.
Defensible claim: *predictive control costs no measurable latency versus
reactive control, while recording no sampled congestion seconds in these
runs (0.000 s vs 3.000 s) with avoidance rate 1.000 vs 0.188.*
Overlapping CIs are NOT evidence of equivalence: no equivalence margin
was pre-specified and no equivalence test (e.g. TOST) was run, so the
honest statement is "not separated by these data", never "equal".

## 2. Forecast MAE — real, structural, and honestly unbeatable here

- Persistence MAE 10.94 / RMSE 23.01. GB MAE 14.80 / RMSE 22.17.
  GB loses MAE in **all 8 runs**; GB wins squared loss in all 8 runs.
- GB signed bias: **+4.2 pts overall, +9.6 pts when current ≥ 50** —
  it systematically overpredicts under load (anticipates ramps that
  plateau). In the calm half of samples its MAE is 13.1 vs persistence 7.4.
- Three honest attempts to fix it, all leave-one-run-out:
  - volatility gate → collapses to pure persistence (every fold picks
    the largest threshold);
  - move-size gate → collapses to pure persistence;
  - blend → LORO weight 0.0 (pure persistence) in every fold;
  - linear recalibration (a≈1.7, b≈0.81) → best RMSE of anything tested
    (21.25) but MAE worsens to 15.67; recal + damping → collapses to
    persistence again.
- Conclusion: on this data no deployable combination beats persistence
  on MAE. The oracle gate (MAE 8.43) shows headroom exists, but the
  signals available at prediction time do not identify when GB is right.

## 3. Why GB still earns its place — decision-level evidence

- Zero samples in the predictive-ml runs ever reached 85% at +5 s, and
  GB predicted ≥85 twenty-four times. Read naively: false alarms. Read
  correctly: **intervention censoring** — the controller rerouted on
  those forecasts, so the crossing never happened. The same workload
  congested for 3.0 s under persistence/reactive control and 38.6 s
  under static. The forecast's value is measured at the system level:
  no sampled congestion seconds, avoidance 1.000, loss 0.023%,
  throughput equal.
- Recalibrated GB has the best RMSE of any forecaster tested (21.25) —
  if a single point-forecast number must be quoted, quote RMSE with the
  MAE caveat attached, never MAE alone and never GB-superiority on MAE.

## What would actually close the MAE gap

Retrain GB against the bias: the +9.6 pt overprediction under load is a
training-distribution artifact. A retrain must use calibration data
only (never campaign-02 — that is the test set), with the campaign kept
untouched for evaluation. Until then, the claims above are the strong,
defensible position.
