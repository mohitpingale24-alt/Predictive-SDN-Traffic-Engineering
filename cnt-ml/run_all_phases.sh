#!/usr/bin/env bash
# Phase J orchestration: re-run Phases B through H after a real-data swap.
set -e

if [ "${1:-}" != "--yes" ]; then
    read -r -p "Run Phases B-H using data/raw/telemetry_raw.csv? (y/n) " reply
    case "$reply" in
        [Yy]) ;;
        *) echo "Aborted."; exit 1 ;;
    esac
fi

python src/validate_schema.py
python src/clean_data.py
python src/build_features.py
python src/split_data.py
python src/train_baselines.py
python src/train_xgboost.py
python src/evaluate.py

echo "All Phases B-H completed successfully."
echo "Review results/metrics_comparison.csv and results/plots/."
