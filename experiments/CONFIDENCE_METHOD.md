# Prediction confidence

`cnt-ml/src/confidence.py` adds an empirical interval around the frozen Gradient
Boosting prediction. It leaves the point model unchanged. Regenerate the bound
artifact with `.venv-ml/bin/python experiments/calibrate_confidence.py`.

The interval radius is the fixed 90% ordered absolute residual on the 204-row
chronological validation split, using rank `ceil((n+1)*0.90)`. Predictions and
interval endpoints are clipped to the physical utilization range [0,100]. Train
data supply minimum/maximum bounds for features actually split on by the trees.
All schema features must be present and finite; unused features do not decide
domain eligibility. The artifact records hashes for the original weights, portable
weights, feature schema and all three split files. The test file is hashed without
parsing its rows or targets.

This is **exploratory calibration**: the same validation split selected the model
parameters, measurements at adjacent times and on simultaneous links are
dependent, and no independent calibration split exists. The nominal coverage is
an interval setting. It is not a congestion probability or a guarantee of coverage
for new workloads, topologies or the subset of routing decisions.

The runtime API is `ConfidenceEstimator().interval(prediction, features,
threshold=85.0)`. Its dictionary exposes `lower`, `upper`, `width`,
`nominal_coverage`, `method`, `in_domain`, `confidence_gate_pass`, `gate_reason`,
and unsupported/missing/invalid feature lists. Its routing gate is
`in_domain and lower >= threshold`. The controller still owns its consecutive
samples, alternative path capacity, cooldown and hysteresis checks. A failed
confidence gate should be logged and can fall back to the measured reactive
policy. Missing/nonfinite features return [0,100] and cannot pass. Values outside
the ranges of used training features cannot pass either.

`confidence_policy.select_signals(active_path, observed, predictions, intervals,
capacities, threshold=85)` makes the source of routing signals explicit. It
returns `source`, `signals`, `reason`, and `alternative_safe`. A learned decision
uses the active interval lower endpoint as congestion evidence. Its alternative
path upper endpoint is `alternative_upper + active_upper * active_capacity /
alternative_capacity`, capped at 100%, and must remain below 85%. If that bound
is unsafe, the decision is blocked with no replacement signal. Missing or weak
forecasts fall back to projected measured utilization. Recovery from B uses
measured utilization. The controller owns freshness checks and resets its
consecutive candidate when signal sources change, so measurements and forecasts
cannot be mixed into one three-sample trigger.

The calibrated radius is approximately **25.17 percentage points**. Even a
prediction of 100% has a lower endpoint of only 74.83%, so the present 90% gate
permits **zero proactive decisions at the 85% threshold**. This is evidence that
the current model/data do not support a narrow interval, not evidence that the
extension improves routing. Reactive fallback prevents the confidence layer from
silently disabling congestion handling. A one-sided signed overprediction
quantile is stored as a diagnostic only and does not change the routing rule.

To support a stronger result, collect a separate calibration capture with more
congestion episodes, fix the model and all interval rules before evaluating on
fresh runs, and compare network metrics including missed/false reroutes. Do not
narrow this radius or change the confidence level after inspecting test results.
