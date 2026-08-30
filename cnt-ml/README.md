# Predictive SDN Traffic Engineering — Machine Learning Module (`cnt-ml`)

Short-horizon link utilization forecasting pipeline for proactive Software-Defined Networking (SDN) traffic engineering.

---

## 📌 Project Overview

This repository contains the complete Machine Learning pipeline designed to continuously predict link utilization **5 seconds into the future** based on per-link OpenFlow telemetry.

- **Primary Model:** XGBoost Regressor (tabular temporal features)
- **Baseline Models:** Persistence ($t+5\text{s} = t$), Linear Regression
- **Advanced Sequence Model:** GRU Recurrent Neural Network
- **Inference Horizon:** 5 seconds
- **Congestion Threshold:** $\ge 85\%$ predicted utilization triggers proactive rerouting

---

## 📂 Repository Structure & File Map

```text
cnt-ml/
├── data/
│   ├── raw/
│   │   └── telemetry_raw.csv         # Raw per-second telemetry (synthetic or Mininet)
│   ├── processed/
│   │   ├── telemetry_clean.csv       # Cleaned & interpolated data
│   │   └── telemetry_features.csv    # Engineered lag, MA, and slope features + target
│   └── splits/
│       ├── train.csv                 # 70% chronological train split
│       ├── val.csv                   # 15% chronological validation split
│       └── test.csv                  # 15% chronological test split
├── models/
│   ├── feature_list.json             # Model feature metadata & schema definition
│   ├── xgb_link_predictor.json       # Trained XGBoost regression model
│   ├── gru_link_predictor.h5         # Trained GRU model weights
│   └── gru_config.json               # GRU normalization constants & window parameters
├── results/
│   ├── metrics_comparison.csv        # MAE, RMSE, R2, and inference time per model
│   ├── report_ml_section.md          # Detailed technical/academic report section
│   └── plots/                        # Generated evaluation charts (01 to 06)
├── src/
│   ├── generate_synthetic_data.py    # Phase A: Realistic synthetic data generator
│   ├── validate_schema.py            # Phase B1: Telemetry schema & bounds validator
│   ├── clean_data.py                 # Phase B2: Timestamp sorting, interpolation, bounds clipping
│   ├── build_features.py             # Phase C/D: t+5s target shifting & feature engineering
│   ├── split_data.py                 # Phase E: Temporal split (70/15/15) without leakage
│   ├── train_baselines.py            # Phase F: Trains Persistence & Linear Regression
│   ├── train_xgboost.py              # Phase G: Trains XGBoost model & saves metadata
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
    "queue_occupancy": 65.0,
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
   `timestamp,link_id,utilization,throughput,delay,packet_loss,queue_occupancy,active_flows`
2. Run `python src/validate_schema.py`
3. Execute `bash run_all_phases.sh --yes` to retrain XGBoost and GRU on real network data.
