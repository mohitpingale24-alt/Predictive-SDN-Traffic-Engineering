#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_root="$(dirname "$script_dir")"
cd "$repository_root"

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: sudo ./experiments/run_suite.sh /absolute/path/to/new-runs-directory [repetitions]"
    exit 2
fi

runs_root="$1"
repetitions="${2:-8}"
if ! [[ "$repetitions" =~ ^[1-9][0-9]*$ ]]; then
    echo "Repetitions must be a positive integer: $repetitions"
    exit 2
fi
if [ -e "$runs_root" ]; then
    echo "Refusing to overwrite existing path: $runs_root"
    exit 2
fi
mkdir -p "$runs_root"

for system in static reactive predictive-baseline predictive-ml; do
    mkdir "$runs_root/$system"
    for repetition in $(seq 1 "$repetitions"); do
        run_name="$(printf 'run-%02d' "$repetition")"
        python3 experiments/run_experiment.py \
            --system "$system" \
            --output-dir "$runs_root/$system/$run_name"
    done
done

python3 experiments/analyze_results.py \
    --runs-root "$runs_root" \
    --output-dir "$runs_root/analysis"

echo "Experiment suite complete: $runs_root"
