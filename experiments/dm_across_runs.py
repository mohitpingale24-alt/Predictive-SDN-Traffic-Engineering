"""Pooled Diebold-Mariano test across paired runs of two systems.

A single 90-second run is underpowered: workload-transition spikes
dominate the loss variance, so a per-run DM test rarely reaches
significance either way. This pools the per-pair loss differences from
every matched run pair (run-01 vs run-01, run-02 vs run-02, ...) —
pairs are matched by their order within each run, since the scenario
protocol is identical across runs — and runs one DM test on the pooled
differences, plus the per-run breakdown.

Usage: python3 experiments/dm_across_runs.py SYSTEM_DIR_A SYSTEM_DIR_B
where each system dir contains run-* capture subdirectories.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forecast_stats import build_pairs, diebold_mariano  # noqa: E402


def _run_dirs(system_dir):
    return {name: os.path.join(system_dir, name)
            for name in sorted(os.listdir(system_dir))
            if name.startswith("run-") and
            os.path.isdir(os.path.join(system_dir, name))}


def pooled_dm(dir_a, dir_b, horizon_seconds=5.0):
    runs_a, runs_b = _run_dirs(dir_a), _run_dirs(dir_b)
    common = sorted(set(runs_a) & set(runs_b))
    if not common:
        raise ValueError("No matched run-* directories between the systems")
    errors_a_all, errors_b_all, per_run = [], [], {}
    for run in common:
        pairs_a = build_pairs(runs_a[run], horizon_seconds)
        pairs_b = build_pairs(runs_b[run], horizon_seconds)
        # build_pairs returns (predicted, actual) in protocol order; the
        # scenario sequence is identical across runs, so index i is the
        # same forecast moment in both systems.
        errors_a = [actual - pred for pred, actual in pairs_a]
        errors_b = [actual - pred for pred, actual in pairs_b]
        count = min(len(errors_a), len(errors_b))
        if count >= 10:
            per_run[run] = diebold_mariano(errors_a[:count], errors_b[:count])
            errors_a_all.extend(errors_a[:count])
            errors_b_all.extend(errors_b[:count])
    return {
        "system_a": os.path.basename(os.path.normpath(dir_a)),
        "system_b": os.path.basename(os.path.normpath(dir_b)),
        "loss": "squared error, A minus B (negative favours A)",
        "runs_matched": common,
        "per_run": per_run,
        "pooled": (diebold_mariano(errors_a_all, errors_b_all)
                   if len(errors_a_all) >= 10 else {}),
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__)
        return 2
    print(json.dumps(pooled_dm(argv[0], argv[1]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
