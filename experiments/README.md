# Linux Mininet Experiment

All commands in this guide are Linux commands. First change into the repository
where it exists inside the Linux machine or VM. For example:

```bash
cd "/home/mohit/SDN CP"
```

If the repository is shared from macOS into a Linux VM, use the VM's mounted
path instead. Do not use a macOS `/Users/...` path unless that exact path is
actually mounted inside the VM. The experiment scripts resolve the repository
from their own location, so paths containing spaces are supported.

This experiment is the decision gate for final model selection. It runs the
same deterministic UDP workload under four systems:

| System | Controller behavior |
| --- | --- |
| `static` | Keeps Path A for the complete run. |
| `reactive` | Uses measured utilization as the routing signal. |
| `predictive-baseline` | Uses the persistence predictor. |
| `predictive-ml` | Uses the saved scikit-learn Gradient Boosting Regressor. |

The current development default in `path_switch_controller.py` is predictive
control with the persistence model. No production model is permanently selected.
GRU is the strongest current regression candidate, but it is not integrated into
the live controller. Native XGBoost has not been evaluated and its name is not
used for the verified Gradient Boosting result.

## Testbed

The runner creates a four-switch diamond. Path A has 10 Mbps capacity and 4 ms
one-way link delay across its two links. Path B has 20 Mbps capacity and 8 ms
one-way link delay. A repeatable iperf3 UDP workload progresses through 3, 7, 9,
13, and 5 Mbps stages. The 13 Mbps stage overloads Path A but fits Path B.

The controller uses:

- three consecutive threshold signals;
- an 85% congestion threshold;
- a 70% recovery threshold;
- at least five percentage points of predicted improvement;
- a 15-second cooldown.

## Linux prerequisites

Use a Linux VM or host with Mininet, Open vSwitch, iperf3, and Python 3.9. From
the repository root:

```bash
python3.9 -m venv .venv-controller
source .venv-controller/bin/activate
python -m pip install "pip<24" "setuptools<60" wheel
python -m pip install -r requirements-controller.txt
sudo --preserve-env=PATH .venv-controller/bin/python -m pip --version
```

Mininet, Open vSwitch, and iperf3 should be installed from the Linux system
package manager. Verify `mn`, `ovs-vsctl`, `iperf3`, and `ryu-manager` are on the
preserved PATH.

## Run the complete comparison

First collect a calibration dataset using eight workload repetitions. The
reactive controller moves traffic across both paths without depending on a
synthetic-trained ML model:

```bash
sudo --preserve-env=PATH ./experiments/collect_calibration.sh /tmp/sdn-calibration-01
```

This produces `telemetry_real_candidate.csv` plus a provenance JSON file. It
does not overwrite `cnt-ml/data/raw/telemetry_raw.csv`. Review the capture and
its placeholder fields, preserve the synthetic dataset under a separate name,
then deliberately make the real candidate the pipeline input and rerun:

```bash
cd cnt-ml
test ! -e data/raw/telemetry_synthetic_backup.csv
cp data/raw/telemetry_raw.csv data/raw/telemetry_synthetic_backup.csv
cp /tmp/sdn-calibration-01/telemetry_real_candidate.csv data/raw/telemetry_raw.csv
python src/validate_schema.py
python src/clean_data.py
python src/build_features.py
python src/split_data.py
python src/train_baselines.py
python src/train_xgboost.py
python src/evaluate.py
python src/train_gru.py
cd ..
```

Do not train on the four evaluation runs. Calibration and evaluation data must
remain separate so the network comparison is genuinely held out.

After real-data retraining, run the complete comparison. Choose a new output
path; the suite refuses to overwrite an existing path.

```bash
sudo --preserve-env=PATH ./experiments/run_suite.sh /tmp/sdn-experiment-01 8
```

Each run stores raw telemetry, prediction timing, control events, ping output,
iperf3 JSON, controller resource samples, controller logs, and immutable run
metadata. The final analysis is written to:

```text
/tmp/sdn-experiment-01/analysis/network_metrics_per_run.csv
/tmp/sdn-experiment-01/analysis/network_statistics.csv
/tmp/sdn-experiment-01/analysis/network_results.md
```

## Metrics and interpretation

The suite creates an independent Mininet/controller session for every repetition,
runs all Static repetitions first, then Reactive, Predictive Persistence, and
Predictive Gradient Boosting. The analyzer verifies matched controlled settings
and reports per-run results plus mean, sample standard deviation, and two-sided
95% Student t confidence intervals for average and P95 latency, throughput, packet loss, jitter,
time above 85%, congestion events, reroutes, prediction-to-reroute latency,
inference time, and controller CPU utilization.

Congestion Avoidance Rate uses the static run as a paired counterfactual. A
reroute is successful when the static system becomes congested within five
seconds at the same workload-relative time while the rerouted system remains
below 85% during the following ten seconds.

False Reroute Rate is the fraction of reroutes for which the paired static run
does not become congested in that five-second window. This definition avoids
claiming a counterfactual from a path that was unloaded by the reroute itself.

Final model selection should be based on repeated Linux runs and network-level
outcomes. One smoke run is useful for debugging but is not sufficient research
evidence; repeat runs with recorded seeds and report uncertainty intervals.
