# Response to the external review of commit 6e80ba6

The review's verdict was accepted: the experimental core held up (175
tests, 38/38 preflight parity, campaign-02 statistics regenerating
exactly), but several newer feature claims exceeded what the code and
evidence supported. Every finding was re-verified against the code
before being fixed. All fixes carry regression tests in
`tests/test_review_fixes.py` (42 tests) unless noted as doc-only.

Verification after the fixes: **216 passed, 65 subtests** in the VM
replica (one pre-existing environmental failure:
`test_threshold_neighbors_match_sklearn`, whose frozen pickle was made
under numpy 2.x — it passes on the Mac). Campaign-02 statistics still
regenerate **byte-identical** after the analyzer/gate changes.

## P1 findings

### 1. Learned forwarding rules bypassed ACL, slicing and QoS checks — FIXED
Learned flows matched only MAC pairs, so once a flow was installed,
later traffic between the same hosts skipped every policy check, and
ACL drops blocked whole MAC pairs instead of the denied connection.
Learned forwarding now matches the full checked connection (in_port,
Ethernet pair, IPv4 pair, protocol, TCP/UDP ports, DSCP) via
`connection_match_fields()` in `openflow_routes.py`; non-IP traffic
(ARP) installs no flow at all; ACL drops match the exact denied
5-tuple; slicing drops match the IP pair (the isolation semantic).

### 2. ECMP omitted return-forwarding rules — FIXED
`_install_ecmp` now also installs in_port 2→1 and 3→1 return flows on
s1/s4. The related dispatch defect is fixed too: `live_runtime`
coerces `ecmp` + `legacy` to a research scenario (the legacy runner
has no ECMP system), with a second guard in `live_guest`.

### 3. Training features used future measurements — FIXED
Ping: `nearest_ping` (which could select a later ping for 812
calibration rows) replaced by `latest_ping` — most recent ping at or
before the row, 1.5 s tolerance; provenance strings updated.
Loss: rows previously carried the whole-segment iperf summary of the
segment they fell inside — a value that only exists at segment end.
Rows now carry the most recent COMPLETED segment's loss
(`iperf_completed_at`), falling back to OpenFlow counters before the
first segment completes. Two existing tests asserted the leaky
values; they were updated with the reasoning recorded in the test.
(The frozen GBDT has no loss splits, so frozen results are
unaffected, as the review itself noted.)

### 4. History features crossed sampling gaps — FIXED
Training lags/rolling means are now looked up by exact timestamp
(`attach_time_history` in `build_features.py`); gap-adjacent rows
become NaN and drop instead of silently spanning a gap. The live
`FeatureHistory` defaults to `require_contiguous=True` and the
controller's `SDN_CONTIGUOUS_HISTORY` default flipped to `'1'`, so
training and runtime semantics match.

### 5. Forecast significance comparisons paired different outcomes — FIXED, claim corrected
Cross-system Diebold–Mariano pairing was invalid: independent
controllers induce different futures, so row-position pairing compares
forecasts of different targets. Removed from `forecast_stats.py` and
`dm_across_runs.py`. The valid comparison scores ML and persistence
against identical (timestamp, link) actuals within the same captures.
Recomputed on campaign-02 predictive-ml (8 runs pooled, n = 1378):
mean loss diff −24.40 (favours ML), DM −1.41, **p = 0.157 — not
significant**. This replaces the previously quoted −108.7 / p = 0.239
everywhere (RESEARCH.md, SCOPUS_READINESS.md).

### 6. Conformal "certification" used calibration-set coverage — FIXED, claim corrected
`calibrate_conformal.py` now splits chronologically (60/40): the
radius comes from calibration rows only; coverage is measured on
held-out rows, plus ACI sequential held-out coverage and an optional
untouched eval capture. Regenerated artifact: radius 26.80 (was
38.51), **held-out coverage 0.812 at nominal 0.90** (n = 69), ACI
held-out 0.855, eval capture 0.862 (n = 174). The old "0.907
certified" claim was in-sample and is retracted in RESEARCH.md: the
intervals undercover out of sample. Dashboard relabelled to held-out
coverage throughout.
Runtime hardening: `in_domain` is now computed from the calibration
prediction range recorded in the artifact (was asserted `True`
unconditionally), and the runtime refuses to serve intervals when a
supplied model's sha256 differs from the calibrated model's.

### 7. The confidence gate could never activate — DIAGNOSED, recorded, deliberately not "fixed" by redesign
With predictions clipped at 100 and radius 26.8, the maximum possible
lower bound is 73.2 < 85: the gate is arithmetically dormant. The
artifact now carries `gate_diagnostics` (`max_lower_bound`,
`gate_can_activate: false`), the dashboard surfaces it, and
RESEARCH.md states the dormancy plainly. Shrinking intervals or
lowering the threshold to manufacture passes — which the reviewer
explicitly warned against — was not done.

### 8. GNN/quantile extensions lacked temporal boundary handling — FIXED, recomputed
GNN dataset windows/targets are built by exact timestamp (a sampling
gap or pooled-run seam makes a snapshot incomplete rather than
silently spanning it); training snapshots whose target lands in the
test period are purged (`boundary_purged_snapshots`). The quantile
trainer sorts by timestamp and applies a horizon embargo via
`target_timestamp` (`boundary_purged_rows` in its report).
Recompute on the committed data (below) confirms the fixes run
end-to-end on real inputs.

### 9. Failure recovery mistook zero traffic for recovery — FIXED
A sample from the failed link itself can no longer mark recovery
(`note_sample(..., link_id=...)`); attributed recovery requires a
reroute plus an under-threshold sample from a different link.
`switch_path` refuses to reroute onto a path with any down link.
Extended in the sweep: port-status handling now covers s4
(S2-S4/S3-S4, previously invisible), and the gate checks BOTH links
of the target path.

### 10. Strong simple baselines were missing — FIXED (headline), one part left open
Added **static-fastest**: static control pinned to path B (the
20 Mbps route) as a primary system (`SDN_INITIAL_PATH` in the
controller, `static-fastest` in `scenarios.py`, protocol bumped to
`safe-te-v2`). Whether ML beats "always use the fast route" is now a
runnable campaign question. Not added: a validation-tuned reactive
threshold / EWMA predictor baseline — recorded as remaining work
rather than bolted on untested.

### 11. Missing telemetry could become "zero congestion" — FIXED
`analyze_results.load_run` raises on a telemetry file with zero rows,
and `quality_gate.check_run` now requires telemetry.csv (≥10 finite
rows) and real ping samples — the gate previously never opened either
file. All 32 committed campaign-02 captures pass the strengthened
gate, and their statistics still regenerate byte-identical.

## P2 findings

### 12. Experiment fingerprints were incomplete — FIXED
Run provenance now hashes every behavior-changing input: the CN
policy modules and their data files, `conformal_runtime.py` + the
calibration artifact, `link_map.py`, `failure_recovery.py`, drift /
anomaly / explainability / latency / topology modules, and the
cnt-ml runtime sources; run metadata records the effective `SDN_*`
controller environment verbatim.

### 13. Public deployment was not reproducible end to end — FIXED for dashboard/tests, stated honestly for live runs
New `requirements-dashboard.txt` (the reproducible core; excludes the
ryu/eventlet pins that only install on the experiment VM's Python);
the dashboard server takes `--host` and the Docker image binds
0.0.0.0 inside the container while compose publishes host-loopback
only; CI installs the dashboard requirements on Python 3.12 and runs
the Node dashboard tests. README now states plainly that live
experiments still need a prepared VM image that is not distributed,
so live results are author-environment results until provisioning is
published. FEATURE_STATUS.md is the claims matrix the reviewer asked
for: per feature — library-only / controller-wired / live-tested /
repeatedly evaluated (GNN is library-only; slicing enforces isolation
drops but not bandwidth shares and has no VLAN push/pop; general
topology routing is not implemented).

### 14. Diebold–Mariano degenerated on constant loss differences — FIXED
Constant nonzero differences now keep their true mean with
`dm_stat`/`p_value` = None and `degenerate: true` (the statistic is
undefined at zero variance), instead of reporting mean 0 / p = 1.
Identical errors still return p = 1.0.

### 15. GNN architectural and serialization defects — FIXED
With a fully-connected 2-node line graph, the normalized adjacency
[[.5,.5],[.5,.5]] averaged both links into one vector and the model
emitted identical predictions for distinct inputs. Both layers now
have residual self-projections (W0, W2s), so node identity survives;
an asymmetric-input test guards it. `save()` now embeds the
normalization, link order and horizon as artifact meta; `load()`
restores them, and pre-fix artifacts load with zero projections,
reproducing their original function exactly.

### 16. Drift/ACI pairing in the controller — FIXED
The controller paired every aged forecast with the current actual, so
after a telemetry gap several target times shared one observation.
Forecasts are now keyed by their exact target time (issue + horizon,
±0.5 s); targets that pass unobserved expire unpaired and are
counted in `_unpaired_forecasts`.

### 17. Per-link latency attribution was misleading — FIXED
The single end-to-end ping RTT was attributed to BOTH paths' links.
It is now attributed only to the ACTIVE path's links; the inactive
path goes stale and reports unknown. The equal-split semantics are
documented in METRIC_DEFINITIONS.md.

### 18. Metric semantics were under-documented — DOCUMENTED
METRIC_DEFINITIONS.md now states: `active_flows` counts flow-table
entries on the port, not transport flows; utilization is clipped at
100 (overload magnitude is lost); path projection assumes the whole
bundle moves together; legacy-mode reroute timestamps are controller
intent, not barrier-confirmed forwarding (safe-reroute mode is the
acknowledged path).

### 19. Local security weaknesses — FIXED
`/tmp/active_path` is written with `O_NOFOLLOW`, owner-only 0600.
The VM provisioning template no longer mints a plaintext password
equal to the username: the account is locked, password auth is
disabled, and access is via console/9p only (nothing in the
automation ever logged in with it; historical copies remain inside
committed calibration artifacts as build records).

### 20. `SDN_ACTIVE_MODEL=auto` selected by test results — FIXED
`predict.resolve_active_model` ranked models by the committed TEST
comparison tables — test-set selection. `auto` now resolves to the
frozen validation choice (gradient_boosting) when its artifact
exists, else persistence. Explicit model choices are untouched; the
tables are still read for reporting, never for selection.

### 21. Overstated claims in the audit/readiness docs — FIXED
WEAK_METRIC_AUDIT.md: overlapping CIs no longer read as "no
significant difference"/equivalence (no equivalence test exists);
"eliminating congestion entirely" corrected to "no sampled
congestion seconds in these runs". SCOPUS_READINESS.md: the
"doubling runs gives a definitive/properly powered answer" promise
removed (no power calculation exists), and the invalid cross-system
DM reference replaced by the valid same-target result.

### 22. `campaign_plan` crashed on extension scenarios — FIXED
It indexed `SCENARIOS[name]["topology"]` directly while validation
accepted EXTENSION_SCENARIOS. It now uses `scenario_definition()`;
a parameterized test plans every exposed scenario.

### 23. Integration-test gaps — PARTIALLY CLOSED, rest stated openly
The new fake-datapath tests cover policy-scoped flow installation,
ECMP groups + return rules, the whole-path reroute gate and drift
pairing without Ryu. A real Linux/Mininet integration job in CI
remains open: it needs a prepared guest image in CI, which does not
exist yet (see README reproducibility status).

## Recomputed artifacts (fixed code, committed data)

- **Conformal artifact** regenerated (see finding 6).
- **GNN retrained** on campaign-02 predictive-ml run-01 telemetry
  (the input behind the committed metrics): 59 train snapshots
  (5 boundary-purged; gap-exact windowing), GNN MAE 18.39 (was 25.81),
  RMSE 27.37; persistence MAE 14.87 / RMSE 26.11; DM p = 0.491, not
  significant. **The negative GNN result stands** — the residual
  architecture improves it materially but does not beat persistence.
  The saved model is now self-describing (normalization, link order,
  horizon in the artifact).
- **Quantile trainer** smoke-run on the committed features file:
  chronological split with horizon embargo, 11 boundary rows purged,
  988 train / 429 test. (No quantile artifact was ever committed;
  nothing to refresh.)
- Note: the committed `telemetry_pooled.csv` is header-only —
  campaign-02 predates all-link recording, so pooled GNN training
  cannot be reproduced from committed captures. Recorded here rather
  than silently worked around.

## What remains open (not claimed as done)

1. Mac verification of this whole patch set (full suite on the Mac,
   dashboard click-through) — replica results are not Mac results.
2. Live Mininet verification of the controller fixes (policy flows,
   ECMP return rules, s4 failure handling) and a failure-injection
   run; a Mininet CI job (finding 23).
3. Campaign runs for static-fastest and the other new/changed
   systems; campaign-03 merge when that run completes.
4. Validation-tuned reactive / EWMA baselines (finding 10).
5. Published VM provisioning (script + checksums) to make live runs
   reproducible by others (finding 13).
