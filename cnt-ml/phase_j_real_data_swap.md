# Phase J: Real Data Swap

Phase J replaces the synthetic CSV with telemetry collected by the Ryu/Mininet networking side. Keep a backup of the synthetic file before replacing it.

## Expected input

Save the real data as `data/raw/telemetry_raw.csv` with this exact schema:

```text
timestamp,link_id,utilization,throughput,delay,packet_loss,queue_occupancy,active_flows
```

Requirements:

- `timestamp`: Unix seconds or seconds from test start
- `link_id`: link identifier such as `S1-S2`
- `utilization`: 0-100 percent
- `throughput`: non-negative Mbps
- `delay`: non-negative milliseconds
- `packet_loss`: 0-100 percent
- `queue_occupancy`: 0-100 percent
- `active_flows`: non-negative count
- One row per link per sample, ideally every second
- At least 1,000 rows recommended
- Include utilization at or above 85% for meaningful congestion analysis

Example:

```csv
timestamp,link_id,utilization,throughput,delay,packet_loss,queue_occupancy,active_flows
1234567890,S1-S2,20.5,2.05,4.2,0.0,5.2,3
1234567891,S1-S2,21.3,2.13,4.1,0.0,5.8,3
```

## Validate and rerun

From the `cnt-ml` project root:

```powershell
python src/validate_schema.py
python src/clean_data.py
python src/build_features.py
python src/split_data.py
python src/train_baselines.py
python src/train_xgboost.py
python src/evaluate.py
```

With Git Bash, the same phases can be run together:

```bash
bash run_all_phases.sh
```

Use `bash run_all_phases.sh --yes` to skip the confirmation prompt.

The pipeline regenerates cleaned data, engineered features, chronological splits, model metrics, the XGBoost model, and evaluation plots. It never overwrites the raw CSV except when the networking team deliberately replaces it.

## Handoff to Ryu

The integration contract remains unchanged:

```python
from src.predict import predict_future_utilization

predicted = predict_future_utilization(telemetry)
if predicted >= 85:
    reroute_to_alternative_path()
```

Use `get_model_info()` to log the active model and metrics. Persistence remains the default until real-data evaluation shows that another model is better, especially at high utilization.

## Common issues

- Duplicate `(timestamp, link_id)` rows: Phase B removes duplicates.
- Missing timestamps: Phase B interpolates gaps within each link.
- Values outside valid ranges: Phase B clips or fixes supported numeric ranges.
- No high-load samples: collect traffic that pushes at least one link above 85%.
- XGBoost worse than Persistence: retain Persistence or tune and validate on richer real data.
- Timestamp ordering warnings: Phase B sorts each link before feature construction.

## Handoff checklist

- Real CSV saved at `data/raw/telemetry_raw.csv`
- `python src/validate_schema.py` passes
- Phases B-H complete
- Review `results/metrics_comparison.csv`
- Review `results/plots/04_mae_by_load_level.png`
- Confirm the selected model in `src/predict.py`
- Test `python src/predict.py`
- Share `src/predict.py`, `models/xgb_link_predictor.json`, and `models/feature_list.json` with the networking team
