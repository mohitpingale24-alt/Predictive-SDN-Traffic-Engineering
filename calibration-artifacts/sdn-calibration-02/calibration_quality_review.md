# Calibration-02 Quality Review

Decision: **PASS**

Retraining authorized: **YES**

Four-system evaluation authorized: **NO**

Reviewed capture: `calibration-artifacts/sdn-calibration-02`, reconstructed without changing raw measurements from the preserved `sdn-calibration-02.failed` capture after repairing the dataset-preparation RTT coverage policy.

## Decision basis

- The Ubuntu/Mininet/Ryu experiment completed all 8 repetitions before the original wrapper failed in post-processing. The repaired preparation step excludes RTT-uncovered rows instead of fabricating delay measurements.
- Source telemetry contains 1,480 rows, 740 per link. The training candidate contains 1,470 rows, 735 per link; exactly 10 initial rows were excluded because no ping RTT existed within 1.5 seconds.
- The candidate has the seven-column active schema: `timestamp`, `link_id`, `utilization`, `throughput`, `delay`, `packet_loss`, and `active_flows`. `queue_occupancy` is absent.
- All 1,470 candidate rows contain measured ping RTT. Candidate RTT ranges from 10.2 ms to 52.6 ms. Delay is end-to-end RTT shared across the two link rows at a timestamp, not one-way per-link delay.
- All 40 iperf3 JSON files parse successfully. Measured loss is 0% in every workload segment, jitter ranges from 0.156 to 0.787 ms, and received rates span approximately 3.000 to 13.000 Mbps. Zero loss is therefore a measured result, not a placeholder claim.
- The candidate contains no malformed rows, duplicate `(timestamp, link_id)` pairs, non-finite values, negative measurements, or out-of-range percentages. Schema validation passes.
- Path A reaches 93.187% utilization and the capture contains 24 samples at or above the 85% congestion threshold.
- Prediction warm-up is auditable. Each link records `skipped_insufficient_history` for history counts 1 through 10, and the first usable reactive signal occurs at history count 11.
- The controller records exactly 8 `A -> B` and 8 `B -> A` reroutes. No extra reroutes or oscillations were observed.
- Controller logs contain no traceback, exception, error, fatal, or disconnect marker.
- Sampling is predominantly one second. Seven shared two-second gaps are enumerated in `sampling_gaps.json`; no gaps are silently interpolated. Downstream targets use exact timestamp matching at t+5 seconds.
- Provenance records the command, host and software environment, topology, controller settings, traffic workload, source-file hashes, preparation-script hash, artifact hashes, RTT exclusion policy, feature policy, and sampling gaps. All recorded artifact and preparation-script SHA-256 values were reverified.
- Controller resources contain 707 samples. Mean CPU use is 6.716%, CPU P95 is 15.31%, mean memory use is 2.395%, and maximum memory use is 2.4%.
- The historical `sdn-calibration-01` directory was not modified. The original calibration-02 failure is preserved separately as `sdn-calibration-02.failed`.

## Accepted limitations

1. The first five normalized seconds for both links are excluded because ping began after telemetry. The exclusion is explicit and does not remove any of the 24 high-utilization samples.
2. RTT is an end-to-end path measurement replicated across link rows, not a direct per-link delay measurement.
3. iperf3 loss is segment-level and repeated across telemetry rows within that segment. Fourteen candidate rows outside an iperf3 interval retain the OpenFlow-derived zero-drop observation.
4. Git commit and branch were unavailable inside the frozen VM source copy. Exact SHA-256 fingerprints for the controller, telemetry runtime, topology, experiment runner, and original preparation script are present; the repaired preparation script has its own verified fingerprint.

## Authorization boundary

This PASS authorizes preparation of a separate real-data training input and retraining of Persistence, Linear Regression, Gradient Boosting Regressor, and GRU. It does not authorize the final four-system network comparison until retraining finishes, held-out model metrics are reviewed, and a model is explicitly selected.
