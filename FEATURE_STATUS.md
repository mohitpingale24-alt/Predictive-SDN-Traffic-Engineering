# Feature status — what is actually wired, tested and evaluated

Status levels, weakest to strongest:

- **library-only** — the module exists and is unit-tested, but the
  controller never calls it in a live run.
- **controller-wired** — the controller invokes it during live runs
  (possibly behind an opt-in environment switch).
- **live-tested** — it has run in the Mininet guest at least once with
  its effects visible in a capture.
- **repeatedly evaluated** — it is part of a frozen campaign with
  repeated runs and published statistics.

Anything not listed as repeatedly evaluated must not be described as
an evaluated research contribution. This file is the claims matrix:
if a slide, abstract or README sentence says more than the row here,
the sentence is wrong.

| Feature | Status | Evidence / limits |
|---|---|---|
| Predictive routing, frozen GBDT | repeatedly evaluated | campaign-02 (32 runs), statistics regenerate exactly |
| Static / Reactive / Persistence systems | repeatedly evaluated | campaign-02 |
| Static-fastest baseline (always path B) | controller-wired | added 2026-11-18 (`SDN_INITIAL_PATH`); no campaign runs yet |
| Safe reroute (barrier-acknowledged) | repeatedly evaluated | predictive-safe systems in campaign-02 |
| Exploratory confidence gate (gbdt_confidence) | repeatedly evaluated | predictive-confidence systems in campaign-02 |
| ECMP (SELECT group, capacity weights) | controller-wired | fake-datapath unit tests incl. return rules; live dispatch fixed 2026-11-18; no campaign |
| Conformal intervals + ACI | controller-wired (opt-in `SDN_CONFORMAL=1`) | calibrated offline; held-out coverage 0.81 at 0.90 nominal; gate arithmetically dormant at radius 26.8 (see RESEARCH.md) |
| Drift detection (monitor, Page–Hinkley, ADWIN-lite) | controller-wired | logs events/records in live runs; claims come from offline stream evaluation |
| Drift adaptation (online radius) | controller-wired (opt-in) | not in any frozen campaign |
| Failure recovery | controller-wired | port-status on s1 AND s4 (fixed 2026-11-18); fake-datapath tests; no failure-injection campaign |
| Anomaly detection | controller-wired | detection logged; mitigation reroute is opt-in (`SDN_ANOMALY_MITIGATION=1`) and unevaluated |
| Explainability records | controller-wired | explanations.csv in captures; dashboard page reads them |
| QoS / DiffServ meters | controller-wired (opt-in `SDN_QOS=1`) | meters installed on switch connect; DSCP classes from RFC 4594 shares; no separate QoS-effect evaluation |
| ACL firewall | controller-wired (opt-in `SDN_ACL_PATH`) | exact 5-tuple drop flows (fixed 2026-11-18); hit counters are in-process only |
| Network slicing | controller-wired (opt-in `SDN_SLICES_PATH`) | cross-slice IP traffic dropped; shares validated at load but NOT enforced in the datapath; no VLAN push/pop |
| Per-link latency | controller-wired | active-path RTT split equally across the path's links — an estimate (see METRIC_DEFINITIONS.md); inactive path reports unknown |
| All-link telemetry | controller-wired | telemetry_all_links.csv in captures; feeds offline GNN pooling |
| Quantile tail-risk forecaster | controller-wired (opt-in artifact) | offline training with horizon embargo (fixed 2026-11-18); not in frozen campaigns |
| GNN forecaster | library-only | offline evaluation only; NOT used by the controller for routing; negative result on campaign-02 two-link data |
| Topology graph / extra topologies | library-only | offline experiment modules; controller routing is written for the A/B diamond (and access6 variant) |
| General-topology routing | not implemented | no claim may be made |

Last updated: 2026-11-18, alongside the external-review fixes
(REVIEW_FIXES.md).
