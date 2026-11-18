# Predictive SDN Traffic Engineering — Machine Learning Module (`cnt-ml`)

Short-horizon link utilization forecasting pipeline for proactive Software-Defined Networking (SDN) traffic engineering.

---

## 📌 Project Overview

This repository contains the complete Machine Learning pipeline designed to continuously predict link utilization **5 seconds into the future** based on per-link OpenFlow telemetry.

- **Live Default:** Persistence until held-out real-telemetry results justify another model
- **Candidate Models:** Linear Regression, tuned Gradient Boosting Regressor, and GRU
- **Unverified Optional Model:** Native XGBoost, only after its runtime is installed and tested separately
- **Baseline Models:** Persistence ($t+5\text{s} = t$), Linear Regression
- **Advanced Sequence Model:** GRU Recurrent Neural Network
- **Inference Horizon:** 5 seconds
- **Congestion Threshold:** $\ge 85\%$ predicted utilization triggers proactive rerouting

## Current validated state

Calibration-02 passed its quality gate, and the approved 1,470-row real dataset
has been retrained using the 13-feature schema. Gradient Boosting Regressor is
the candidate predictive ML model for the final four-system experiment. This is
not a final model selection or evidence of network improvement; repeated
network-level evaluation remains required. See
`results/real_data_retraining_report.md`.

---

## 📂 Repository Structure & File Map

```text
cnt-ml/
├── data/
	├── synthetic/                      # Preserved pre-retraining dataset snapshot
	├── real/calibration-02/            # Approved source, provenance, and prepared split
│   ├── raw/
│   │   └── telemetry_raw.csv         # Raw per-second telemetry (synthetic or Mininet)
│   ├── processed/
│   │   ├── telemetry_clean.csv       # Validated, sorted telemetry
│   │   └── telemetry_features.csv    # Engineered lag, MA, and slope features + target
│   └── splits/
│       ├── train.csv                 # 70% chronological train split
│       ├── val.csv                   # 15% chronological validation split
│       └── test.csv                  # 15% chronological test split
├── models/
│   ├── feature_list.json             # Model feature metadata & schema definition
│   ├── linear_link_predictor.joblib  # Saved Linear Regression model for live inference
│   ├── xgb_link_predictor.json       # Legacy native-XGBoost artifact; not the verified current result
│   ├── gru_link_predictor.h5         # Trained GRU model weights
│   └── gru_config.json               # GRU normalization constants & window parameters
├── results/
│   ├── metrics_comparison.csv        # MAE, RMSE, R2, and inference time per model
│   ├── report_ml_section.md          # Detailed technical/academic report section
│   └── plots/                        # Generated evaluation charts (01 to 06)
├── src/
│   ├── generate_synthetic_data.py    # Phase A: Realistic synthetic data generator
│   ├── validate_schema.py            # Phase B1: Telemetry schema & bounds validator
│   ├── clean_data.py                 # Phase B2: Timestamp sorting and bounds clipping
│   ├── build_features.py             # Phase C/D: exact timestamp t+5s targets and features
│   ├── split_data.py                 # Phase E: Temporal split (70/15/15) without leakage
│   ├── train_baselines.py            # Phase F: Trains Persistence & Linear Regression
│   ├── train_xgboost.py              # Phase G: Native XGBoost or verified Gradient Boosting fallback
│   ├── train_gru.py                  # Phase K: Trains GRU sequence model
│   ├── evaluate.py                   # Phase H: Evaluation & chart generation
│   └── predict.py                    # Phase I: Ryu Controller Python Integration API
├── phase_j_real_data_swap.md         # Guide for swapping real Mininet telemetry into raw CSV
├── run_all_phases.sh                 # Single-command Bash pipeline runner
└── README.md                         # Project documentation
```

---

## 🛠️ Step-by-Step Execution Workflow

To run the entire pipeline end-to-end locally:

### Option A: PowerShell / Command Prompt (Windows)
```powershell
python src/generate_synthetic_data.py
python src/validate_schema.py
python src/clean_data.py
python src/build_features.py
python src/split_data.py
python src/train_baselines.py
python src/train_xgboost.py
python src/evaluate.py
```

### Option B: Git Bash
```bash
bash run_all_phases.sh --yes
```

---

## 🔗 Ryu Controller Integration Contract

The Ryu SDN controller imports `predict_future_utilization` from `src/predict.py` to get live predictions:

```python
from src.predict import predict_future_utilization

# Telemetry dictionary from Ryu OpenFlow collector
telemetry_sample = {
    "timestamp": 1234567890,
    "link_id": "S1-S2",
    "utilization": 78.5,
    "throughput": 7.85,
    "delay": 12.3,
    "packet_loss": 0.0,
    "active_flows": 7
}

predicted_utilization = predict_future_utilization(telemetry_sample)

if predicted_utilization >= 85.0:
    # Trigger K-shortest path evaluation and proactive OpenFlow FlowMod reroute
    reroute_flow()
```

---

## 🔄 Swapping Real Mininet Telemetry

When Mininet + Ryu telemetry is ready:
1. Save the raw CSV to `data/raw/telemetry_raw.csv` with schema:
   `timestamp,link_id,utilization,throughput,delay,packet_loss,active_flows`
2. Run `python src/validate_schema.py`
3. Execute `bash run_all_phases.sh --yes` to retrain and evaluate the tabular models.
4. Run `python src/train_gru.py` separately to retrain and evaluate the optional GRU comparison.

The split phase purges observations whose five-second-ahead labels cross from
train into validation or from validation into test. This prevents boundary
leakage in time-series evaluation.

---

## Live controller integration

Run `path_switch_controller.py` from the repository root with Ryu. The
controller polls OpenFlow port and flow statistics every second for the two
candidate links, appends them to `data/raw/telemetry_live.csv`, constructs the
same lag/rolling features used during training, predicts utilization at t+5,
and applies the rerouting policy.

The policy requires three consecutive predictions by default. It changes to
the alternative path only when the active path is at least 85% and the
alternative is predicted to be at least five percentage points better. It uses
a 70% recovery threshold and a 15-second cooldown.

Configuration is controlled with environment variables:

```text
SDN_ACTIVE_MODEL=persistence|auto|linear_regression|xgboost|gradient_boosting
SDN_MODEL_SELECTION_METRIC=congestion_f1|rmse
SDN_CONTROL_MODE=static|reactive|predictive
SDN_LINK_CAPACITY_MBPS=10
SDN_PATH_A_CAPACITY_MBPS=10
SDN_PATH_B_CAPACITY_MBPS=20
SDN_CONGESTION_THRESHOLD=85
SDN_RECOVERY_THRESHOLD=70
SDN_MINIMUM_IMPROVEMENT=5
SDN_CONSECUTIVE_PREDICTIONS=3
SDN_REROUTE_COOLDOWN_SECONDS=15
SDN_TELEMETRY_CSV=/path/to/telemetry_live.csv
```

`persistence` is intentionally the default. Use `auto` only after retraining
and evaluating on telemetry from the actual topology. Automatic selection uses
held-out congestion F1 by default because rerouting is a threshold decision;
set `SDN_MODEL_SELECTION_METRIC=rmse` for regression-first selection.

The safe default remains `SDN_CONTROL_MODE=predictive` with
`SDN_ACTIVE_MODEL=persistence`. For the authorized final comparison, the
candidate ML condition uses `SDN_ACTIVE_MODEL=gradient_boosting`. `static`
disables automatic rerouting and `reactive` applies the same stability policy
to measured utilization. Final model selection remains deferred until the
repeated Linux experiments are complete.

Before comparing the alternative path, the controller projects how heavily it
would be loaded after transferring the active traffic. This prevents an idle
path's near-zero measurement from causing a misleading switch or premature
return. See `experiments/README.md` for the four-system Mininet comparison.

OpenFlow port statistics do not expose delay. The current live controller uses
the latest causal ping RTT from `SDN_PING_LOG`, at most 1.5 seconds old. Uncovered
RTT remains blank and learned inference skips that observation. Calibration
preparation used the nearest covered RTT within 1.5 seconds; this alignment
difference is documented in `../experiments/METRIC_DEFINITIONS.md`.
Queue occupancy is excluded from the active telemetry and ML feature schema.

For the controller environment, use Python 3.9 and install
`requirements-controller.txt`. The Ryu 4.34 import path was verified with the
pinned Eventlet version. Mininet and Open vSwitch still require the Linux test
environment; they are not installed by pip and were not executed on macOS.

```bash
python3.9 -m venv .venv-controller
source .venv-controller/bin/activate
python -m pip install "pip<24" "setuptools<60" wheel
python -m pip install -r requirements-controller.txt
ryu-manager path_switch_controller.py
```
