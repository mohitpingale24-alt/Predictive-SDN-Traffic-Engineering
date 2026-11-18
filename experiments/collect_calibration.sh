#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(dirname "$script_dir")"
cd "$repository_root"

if [ "$#" -ne 1 ]; then
    echo "Usage: sudo ./experiments/collect_calibration.sh /absolute/path/to/new-calibration-directory"
    exit 2
fi

output_dir="$1"
python3 experiments/run_experiment.py \
    --system reactive \
    --repetitions 8 \
    --output-dir "$output_dir"

python3 experiments/prepare_real_dataset.py \
    --run-dir "$output_dir" \
    --output "$output_dir/telemetry_real_candidate.csv"

echo "Calibration capture complete: $output_dir"
echo "Review telemetry_real_candidate.csv before replacing any training data."
