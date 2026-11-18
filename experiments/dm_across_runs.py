"""ML-vs-persistence Diebold-Mariano results for two systems, side by side.

This tool used to pair the two systems' forecast errors by row position
(run-01 vs run-01, index i against index i) and run one pooled DM test
across systems. That pairing is invalid: each controller's actions
change its own future utilization, so index i in system A and index i
in system B are forecasts of different realized targets, however
identical the scenario protocol is. A paired test needs a shared
target, which only exists within one capture.

What remains valid, and what this reports: within each system, the
recorded ML forecasts and persistence are scored against the identical
(timestamp, link) actuals of the same captures, pooled across that
system's runs. Each system gets its own DM result; the two results can
be read side by side, but no cross-system paired statistic is computed.

Usage: python3 experiments/dm_across_runs.py SYSTEM_DIR_A SYSTEM_DIR_B
where each system dir contains run-* capture subdirectories.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from forecast_stats import build_aligned_errors, diebold_mariano  # noqa: E402


def _run_dirs(system_dir):
    return {name: os.path.join(system_dir, name)
            for name in sorted(os.listdir(system_dir))
            if name.startswith("run-") and
            os.path.isdir(os.path.join(system_dir, name))}


def _system_result(system_dir, horizon_seconds):
    aligned = []
    runs = _run_dirs(system_dir)
    for run in sorted(runs):
        aligned.extend(build_aligned_errors(runs[run], horizon_seconds))
    result = dict(runs=sorted(runs), aligned_pairs=len(aligned))
    if len(aligned) >= 10:
        result["ml_vs_persistence_dm"] = diebold_mariano(
            [ml for ml, _ in aligned], [pe for _, pe in aligned],
            horizon=int(horizon_seconds))
    return result


def compare_systems(dir_a, dir_b, horizon_seconds=5.0):
    return {
        "system_a": os.path.basename(os.path.normpath(dir_a)),
        "system_b": os.path.basename(os.path.normpath(dir_b)),
        "loss": "squared error, ML minus persistence within each system "
                "(negative favours ML)",
        "cross_system_dm": "not computed: the two controllers induce "
                           "different futures, so their errors share no "
                           "common target and cannot be paired",
        "per_system": {
            os.path.basename(os.path.normpath(dir_a)): _system_result(dir_a, horizon_seconds),
            os.path.basename(os.path.normpath(dir_b)): _system_result(dir_b, horizon_seconds),
        },
    }


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print(__doc__)
        return 2
    print(json.dumps(compare_systems(argv[0], argv[1]), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
