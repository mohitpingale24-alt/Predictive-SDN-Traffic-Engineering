# Predictive SDN traffic engineering

This Computer Networks project implements a Ryu/OpenFlow controller that predicts link utilization five seconds ahead and evaluates its routing behavior against Static, Reactive and Predictive Persistence systems in Mininet.

## Authoritative evidence

- `calibration-artifacts/sdn-calibration-02`: accepted real-data preparation and provenance.
- `cnt-ml/results/real-calibration-02`: frozen offline comparison, including GRU.
- `final-evaluation-artifacts/campaign-02`: repaired repeated network captures. Check every run, not just the top-level status marker.
- `experiments/METRIC_DEFINITIONS.md`: metric semantics and research limitations.
- `DEMO.md`: recorded and live implementation demonstrations.

Campaign 01 is excluded from final conclusions. Its eight learned-controller runs failed model deserialization. See `final-evaluation-artifacts/CAMPAIGN_01_AUDIT.md`. The first campaign 02 smoke attempt is also excluded. The subsequent smoke passed before the repeated campaign began.

Campaign 02 is complete: **32 quality-checked sessions, eight per system**, with matched workload, topology, capacities and duration. Final per-run values, means, sample standard deviations, 95% confidence intervals and paired differences are in `final-evaluation-artifacts/campaign-02/final-analysis/statistics`.

**Gradient Boosting is selected for the evaluated congestion-avoidance objective**, with zero sampled congestion seconds in all eight runs, versus three seconds for Reactive and Persistence and a 38.625-second Static mean. Reactive had slightly lower mean P95 RTT; GB is not established as better on latency, goodput or loss versus Reactive. This is a controlled Mininet project, not a production validation or universal model ranking. The exact decision and limitations are in `final-evaluation-artifacts/campaign-02/model_selection.json`.

One failed GB repetition was preserved under `campaign-02/excluded` and rerun after a logging-only correction. `source_amendment.json` and `runtime-source.tar.gz` preserve that change and the frozen runtime. No failed capture is included in the final 32.

## Local visual frontend

Double-click `start_project.command`, or run from this folder:

```bash
.venv-ml/bin/python frontend/server.py
```

Open **http://127.0.0.1:8765/**. The launcher opens this page automatically and reuses an already-running dashboard.

- **Live lab:** choose a controller, traffic scenario and network, then press **Start experiment**. The prepared Apple Silicon Mac boots an isolated Linux VM, starts Ryu and Mininet, and runs the real 90-second offered workload (connection overhead adds some wall time). Live cards show utilization, forecast, confidence interval and route changes. After quality checks pass, the cards switch to final network metrics and the session becomes replayable. **Stop** cancels only that isolated experiment and preserves partial files. Startup plus capture normally takes about 2–3 minutes.
- **Replay:** select any of four systems and eight verified campaign repetitions. Cards show full-run metrics; link meters follow the replay playhead.
- **Compare:** choose the original frozen campaign or a separate new scenario/topology group. New groups include only quality-checked captures with identical controlled configurations. A single repetition has no SD or confidence interval. Original CSV export remains separate.

New sessions, source snapshots, logs and copy-on-write VM disks are stored in `live-experiments/`. The prepared base VM and historical campaign captures are not modified. One experiment runs at a time, survives dashboard restarts, and is automatically shut down after capture; an interrupted runner is labelled rather than treated as verified. The local control API validates Origin and a session token, accepts only known system/scenario/topology choices, and exposes no arbitrary shell command or host network control.

## Confidence and safe-routing extension

The original model and campaign remain frozen. The new fifth system is **GB + confidence + safe**, with two additional ablations (confidence-only and safe-only).

- Exact timestamp `t+5s` training targets are retained. New runs reset feature history across missing/duplicate integer-second observations; they never silently bridge a sampling gap.
- Confidence is a fixed, exploratory validation-residual interval, hash-bound to the frozen weights/schema/splits. Validation was already used for model selection, so this is **not guaranteed coverage or a congestion probability**. Its measured 90% radius is **25.173 percentage points**: no clipped prediction can satisfy an 85% lower-bound gate. The proposed controller therefore explicitly uses **measured-congestion fallback**, not an artificially narrowed interval. See `experiments/CONFIDENCE_METHOD.md`.
- Make-before-break prepares the target transit rules, waits for matching OpenFlow barriers, adds higher-priority ingress selectors, waits for both edges, drains for 0.5 seconds and retires only obsolete selectors. Errors/timeouts roll back before commit; unconfirmed rollback or post-commit degradation blocks further route changes. This is **not cross-switch atomicity or a zero-loss guarantee**. [Ryu barrier API](https://ryu.readthedocs.io/en/latest/ofproto_v1_3_ref.html).
- New safety controllers require projected alternative utilization below 85%, three consecutive decisions, hysteresis and cooldown. Safe cooldown starts after acknowledgement.
- Seven scenarios are available: normal, ramp, burst, sustained, near-threshold, multi-flow and alternative-path stress. The six-switch network has three simultaneous host pairs. **All flows share and move as one A/B bundle**; independent per-flow optimization is not implemented.
- Sequence-aware 50 Hz UDP probes and 10 Hz ping per pair measure ±2-second windows. Logs separate probe datagram loss, successful RTT, RTT variation and receiver UDP jitter. Offered load excludes the additional probe overhead. Loss before rerouting may be congestion loss; it must not be attributed entirely to the route update.

On Linux, run one extension experiment into a new directory:

```bash
sudo --preserve-env=PATH python3 experiments/run_research_experiment.py \
  --system predictive-safe --scenario multi-flow --topology access6 \
  --output-dir /tmp/sdn-research-new --repetition 1
```

The dashboard uses `experiments/live_runtime.py` to run this inside the isolated Mac VM. New captures are validated by `check_research_run`, not the legacy fixed-workload gate. `experiments/analyze_research.py` produces metrics, SD and Student-t intervals; counterfactual avoidance/false-reroute metrics remain unavailable without a matched static reference. Scaled smoke runs are never mixed with full repetitions.

Plan a matched five-system comparison without starting anything:

```bash
.venv-ml/bin/python experiments/run_local_campaign.py --plan \
  --scenarios ramp multi-flow --repetitions 3
```

Replace `--plan` with `--run` to execute all 30 experiments sequentially (about an hour on the prepared Mac). The default, without `--scenarios`, covers all seven workloads: 105 experiments, taking several hours. The campaign journal binds code/model hashes, stops on failure, preserves partial attempts and permits explicit `--run --resume CAMPAIGN_ID`. Do not edit controller/model/protocol files during a campaign. These new repeated results are **not claimed complete merely because the runner exists**.

Live lab currently requires this prepared Apple Silicon macOS workspace, QEMU, `hdiutil`, its Ubuntu base disk, and about 3 GB available RAM. Missing requirements are shown in the panel. On other Linux hosts use the experiment commands below. No production network is controlled, no website is published, and no presentation or formal report is needed.

**Reproducibility status (honest version).** The dashboard, the analysis pipeline and the full unit-test suite reproduce from this repository alone (`pip install -r requirements-dashboard.txt`, or the Docker image, which serves the dashboard on the host's loopback). The LIVE experiments do not yet reproduce from the repository alone: they need a prepared Linux guest image (Ubuntu with Mininet 2.3.0, Open vSwitch, Ryu 4.34 on Python 3.8) whose qcow2 is not distributed with the repo, and provisioning that image is currently a manual process on the author's machine. Treat live runs as author-environment results until a provisioning script and image checksums are published; everything statistical in the paper can be re-derived from the committed campaign captures without the VM.

## Verify locally on macOS

Use the existing project environment from this directory:

```bash
.venv-ml/bin/python -m unittest discover -s tests -v
SDN_ACTIVE_MODEL=gradient_boosting .venv-ml/bin/python experiments/preflight_model.py
.venv-ml/bin/python experiments/campaign_progress.py final-evaluation-artifacts/campaign-02
```

These commands do not retrain models or overwrite calibration data. Avoid the legacy synthetic-data quick start while reproducing the frozen real-data experiment.

## Run the network experiment on Linux

Mininet and Open vSwitch require Linux. The prepared VM uses Python 3.8, Ryu 4.34, Eventlet 0.30.2 and Mininet 2.3.0. Host model training uses a separate Python 3.12 environment. Portable JSON inference preserves the frozen Gradient Boosting weights across these environments without unpickling them in the VM.

With the controller environment active and its executables preserved through sudo:

```bash
sudo --preserve-env=PATH bash experiments/run_suite.sh /tmp/sdn-new-campaign 8
```

Choose a new output directory. The runner never overwrites old runs. It starts the controller, checks actual model inference, creates the diamond topology, generates five UDP load stages, captures receiver measurements and checks prediction coverage. Run systems sequentially to avoid overlapping Mininet environments.

After 32 valid sessions, generate corrected final evidence into a new directory:

```bash
.venv-ml/bin/python experiments/finalize_evidence.py --campaign final-evaluation-artifacts/campaign-02 --output-dir /tmp/sdn-evidence-new
```

## Code map

| File | Role |
| --- | --- |
| `path_switch_controller.py` | Ryu statistics collection, forecasting and OpenFlow path changes |
| `telemetry_runtime.py` | Counter rates, recent RTT, feature history and routing policy |
| `route_transition.py` / `openflow_routes.py` | Barrier state machine and scoped OpenFlow installation/rollback |
| `confidence_policy.py` / `cnt-ml/src/confidence.py` | Bound uncertainty intervals and explicit measured fallback |
| `cnt-ml/src/build_features.py` | Exact timestamp t+5 labels |
| `cnt-ml/src/split_data.py` | Chronological splitting and label-boundary purging |
| `cnt-ml/src/predict.py` | Prediction API and model resolution |
| `cnt-ml/src/portable_gbdt.py` | Version-independent frozen-tree inference |
| `experiments/run_experiment.py` | Canonical evaluated topology and traffic workload |
| `experiments/quality_gate.py` | Receiver-data and controller-inference checks |
| `experiments/analyze_results.py` | Corrected network metrics and confidence intervals |
| `experiments/run_research_experiment.py` / `experiments/scenarios.py` | Separate seven-scenario, multi-flow protocol |
| `experiments/transition_probe.py` / `experiments/transition_metrics.py` | Sequence probes and transition-window measurements |
| `experiments/analyze_research.py` | Separate five-system and ablation statistics |
| `frontend/server.py` | Loopback dashboard and constrained local experiment API |
| `frontend/dist/` | Local topology, replay and comparison interface |
| `experiments/live_runtime.py` | Isolated VM startup, single-run locking, cancellation and final verification |
| `experiments/live_guest.py` | Linux guest entry point for the existing controller experiment |

Legacy standalone topology examples are retained, but the final comparison uses the topology in `experiments/run_experiment.py`. Historical filenames containing xgb refer to the scikit-learn Gradient Boosting artifact, not a verified native XGBoost deployment. GRU remains an offline comparison and is not integrated into live routing.
