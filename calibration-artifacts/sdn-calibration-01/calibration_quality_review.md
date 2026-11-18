# Calibration Quality Review

Decision: **CONDITIONAL PASS**

Retraining authorized: **NO**

Four-system evaluation authorized: **NO**

Reviewed capture: `/tmp/sdn-calibration-01`, copied to this directory after the Ubuntu run completed on 2026-09-02.

## Passing checks

- Ubuntu execution completed all 8 repetitions and prepared 1,480 real-candidate rows.
- Mininet connectivity passed with 0% loss in the initial two-host check.
- Ryu connected switches `s1`, `s2`, `s3`, and `s4`; the controller log contains no traceback, exception, fatal error, or disconnect.
- All required files are present and non-empty. All 42 JSON files parse successfully, and all reviewed CSV files have consistent row widths.
- `telemetry.csv` contains 1,480 rows: 740 each for `S1-S2` and `S1-S3`, with matching timestamp sets.
- Timestamps are strictly monotonic per link. Of 739 intervals per link, 734 are 1 second and 5 are 2 seconds.
- No negative values, utilization above 100%, NaN, or infinity were found.
- Path A reached 92.914% utilization, with 24 samples at or above the 85% threshold.
- OpenFlow-derived throughput follows the intended traffic and path changes: Path A carries warm-up/ramp traffic, Path B carries the 13 Mbps phase after threshold crossing, and the route returns to Path A for recovery.
- The controller recorded exactly 8 `A -> B` and 8 `B -> A` reroutes, matching the 8 workload repetitions, with no extra oscillation.
- All 40 iperf3 files are valid. Mean received rates closely match 3, 7, 9, 13, and 5 Mbps; mean jitter ranges from 0.398 to 0.616 ms; reported loss is 0%.
- Ping produced 739 samples. Mean delay increases from about 12.0 ms during warm-up/ramp to 17.9 ms near threshold and 19.9 ms during the 13 Mbps phase.
- Controller resource logging contains 708 rows. CPU mean is 6.284%, CPU P95 is 14.2%, memory mean is 2.395%, and memory maximum is 2.4%.
- The frozen repository was mounted read-only for the detached capture; the calibration wrote only to `/tmp/sdn-calibration-01` and the separate host output directory.

## Conditions that prevent training authorization

1. `telemetry.csv` and `telemetry_real_candidate.csv` contain only zero placeholders for `delay` and `queue_occupancy`; the provenance file explicitly records this limitation. Packet-loss telemetry is also zero for every row. Ping and iperf3 hold useful delay/loss evidence, but those measurements are not merged into the candidate dataset.
2. Provenance records the source run, system, model, row count, timestamp origin, and sampling interval, but omits the repository commit/tree identity, dirty-state fingerprint, Ubuntu/kernel versions, Mininet/Open vSwitch/Ryu/iperf3 versions, Python environment, topology hash, and command line.
3. Sampling is mostly consistent but has five 2-second gaps per link. The target-building step must account for actual timestamps rather than treating every row as exactly one second apart.
4. `predictions.csv` begins at the first telemetry timestamp and contains 1,480 rows. It therefore does not demonstrate a history warm-up before prediction. This may be acceptable for the persistence signal used in reactive mode, but it does not verify the required history behavior for sequence-based ML inference.
5. The 13 Mbps phase is rerouted to the 20 Mbps path, so it validates proactive/reactive avoidance but does not produce sustained utilization above 85% on the active path or non-zero packet loss. The 24 threshold samples are brief transition observations on Path A.
6. The host copy reported ownership-preservation warnings from `cp -a` over the 9p share. File contents were copied and passed structural validation, but a cryptographic source-versus-copy manifest was not produced inside the VM.

## Required next action

Do not replace `cnt-ml/data/raw/telemetry_raw.csv` and do not retrain. First run a corrected calibration that:

- merges synchronized ping and iperf3 delay/loss/jitter evidence into the real candidate or formally excludes unsupported features;
- records complete repository and environment provenance plus file checksums;
- enforces or explicitly documents prediction-history warm-up behavior;
- preserves timestamp gaps for time-aware target construction; and
- includes a controlled sustained-congestion interval if congestion/loss behavior must be represented in training.
