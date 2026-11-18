# Top-notch extension — what was added

Everything below is additive and opt-in where it changes behaviour. The
original four-system campaign, its artifacts, and the legacy A/B control modes
are untouched, so campaign-02 stays comparable. No result, score, path, or
threshold is hardcoded: paths come from the discovered graph, weights from
configured capacities, drift baselines from the saved model metrics, feature
importance from the portable model artifact, and every reported number is
computed from recorded events.

## Tier 1
- **Link-failure recovery** (`failure_recovery.py`, controller-wired): an
  OpenFlow port-status handler maps a down port to its link, records the
  failure, reroutes to the surviving path when the active one dies, and
  measures time-to-reroute, time-to-recover, and packet loss during the
  outage from the events themselves (`failures.csv`).
- **Explainability** (`explainability.py`, dashboard panel "Why did it
  reroute?"): every reroute evaluation is logged to `explanations.csv` with
  the signals, the forecast interval, projected improvement, and top
  contributing features (portable-GBDT split importance × deviation from the
  calibration training ranges). Served at `/api/topnotch`.
- **Drift detection + recalibration** (`drift_detection.py`): forecasts are
  paired with the actual utilization one horizon later; a sliding window's
  RMSE is compared with the saved model's RMSE (× `SDN_DRIFT_FACTOR`).
  After consecutive drifting windows the controller logs `drift_detected`
  and writes a recalibration artifact (empirical residual radius from the
  observed pairs) to `cnt-ml/results/recalibration.json`. The live model is
  never silently swapped.
- **ECMP baseline** (system `ecmp`): the controller installs an OpenFlow
  SELECT group on the ingress switches, hashing flows over both paths with
  bucket weights proportional to the configured path capacities. Runnable
  from the dashboard's Live lab like every other system.

## Tier 2
- **Topology generalization** (`topology_graph.py`, `topologies_v2.py`):
  a graph built only from observed links, k-shortest paths by
  inverse-capacity/delay/hops, equal-cost path sets, and parameterized
  fat-tree / spine-leaf Mininet builders whose generated spec feeds both
  Mininet and the graph — the A/B wiring is no longer assumed by the new code.
- **Multi-objective routing** (`MultiObjectiveScorer`): weighted
  utilization + RTT + loss scoring with scales derived from the candidates,
  plus per-traffic-class weight profiles and a rule-based `TrafficClassifier`
  (rules are data: DSCP, ports, flow size).
- **Harder workloads** (`EXTENSION_SCENARIOS` in `experiments/scenarios.py`):
  `bursty-mixed` (generated on/off bursts), `elephant-mice`, and `multi-pair`,
  each a full 90-second protocol. The original 7 scenarios and the default
  campaign plan are unchanged.

## Tier 3
- **DDoS / elephant mitigation** (`anomaly_mitigation.py`): per-source
  rolling z-score surge detection and elephant-share detection (share of
  concurrent traffic, needs ≥2 sources). The controller logs
  `anomaly_detected`; set `SDN_ANOMALY_MITIGATION=1` to also reroute off the
  affected path. Mitigations expire instead of lingering.
- **One-command reproducibility**: `Dockerfile`, `docker-compose.yml`
  (`make docker-demo`), GitHub Actions CI running the full test suite, and
  `make demo` / `make test` / `make report`.
- **Auto campaign report** (`experiments/generate_campaign_report.py`,
  dashboard `/api/report`): per-system means with 95% CIs, paired
  congestion-seconds differences vs a reference system, multi-objective
  ranking, failure and drift summaries — all computed from the captures.

## Verify
```
python3 -m pytest tests/test_topnotch.py -q   # 11 tests, all new modules
python3 -m pytest tests/ -q                   # full suite (on the Mac/VM guest)
```
Live verification still owed: one `ecmp` live run, one link-failure run
(pull a link mid-run in Mininet), and a visual check of the new dashboard
panel — those need the Mininet guest, which only runs on your Mac.
