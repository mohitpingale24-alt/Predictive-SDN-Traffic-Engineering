"""Pool campaign-02 (8/system) + campaign-03 (8/system) into a combined
n=16-per-system analysis, without modifying either campaign.

Builds final-evaluation-artifacts/combined-16/ as a symlink tree
(campaign-02 runs keep numbers 01-08, campaign-03 runs become 09-16),
runs the project's own analyze_results.py over it (which re-validates
that every run is configuration-identical), then pools the predictive-ml
forecast pairs across all 16 runs and reports MAE/RMSE, a Diebold-Mariano
test on squared loss (Newey-West HAC, HLN correction) and a per-run sign
test. Writes combined_summary.json next to the statistics.

Usage (from the project root):
    python3 experiments/merge_campaigns.py [--forecast-only]
"""
import argparse
import csv
import json
import math
import os
import subprocess
import sys
from pathlib import Path

SYSTEMS = ('static', 'reactive', 'predictive-baseline', 'predictive-ml')
KEY_METRICS = ('Congestion_Duration_seconds', 'Congestion_Events',
               'P95_Latency_ms', 'Average_Latency_ms', 'Average_Jitter_ms',
               'Average_Packet_Loss_pct', 'Congestion_Avoidance_Rate')


def complete_runs(campaign_root, system, count):
    runs = []
    for number in range(1, count + 1):
        run_dir = campaign_root / system / f'run-{number:02d}'
        metadata_path = run_dir / 'metadata.json'
        if not metadata_path.exists():
            raise SystemExit(f'Missing run: {run_dir}')
        metadata = json.loads(metadata_path.read_text())
        if metadata.get('status') != 'complete':
            raise SystemExit(f'Run not complete: {run_dir} '
                             f'(status={metadata.get("status")!r})')
        if metadata.get('system') != system:
            raise SystemExit(f'System mismatch in {run_dir}: '
                             f'{metadata.get("system")!r} != {system!r}')
        runs.append(run_dir)
    return runs


def build_combined(base, extra, combined):
    mapping = {}
    for system in SYSTEMS:
        base_runs = complete_runs(base, system, 8)
        extra_runs = complete_runs(extra, system, 8)
        for index, source in enumerate(base_runs + extra_runs, start=1):
            target = combined / system / f'run-{index:02d}'
            mapping[target] = source.resolve()
    if combined.exists():
        existing = {p: Path(os.readlink(p)) for p in combined.rglob('run-*') if p.is_symlink()}
        if existing != mapping:
            raise SystemExit(f'{combined} already exists and does not match '
                             'the expected symlink tree. Remove it and re-run.')
        return
    for target, source in mapping.items():
        target.mkdir(parents=True, exist_ok=True)
        # run dirs must be real directories for the analyzer's glob; link
        # the directory itself by replacing the placeholder dir.
        target.rmdir()
        os.symlink(source, target)
    print(f'combined tree: {len(mapping)} run symlinks under {combined}')


def fnum(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pooled_forecast(run_dirs):
    pairs = []
    per_run = {}
    for run_dir in run_dirs:
        telemetry = {}
        with open(run_dir / 'telemetry.csv') as handle:
            for row in csv.DictReader(handle):
                stamp = fnum(row['timestamp'])
                if stamp is not None:
                    telemetry[(int(round(stamp)), row['link_id'])] = fnum(row['utilization'])
        run_pairs = []
        with open(run_dir / 'predictions.csv') as handle:
            for row in csv.DictReader(handle):
                if row.get('prediction_status') != 'predicted':
                    continue
                stamp = fnum(row['timestamp'])
                cur = fnum(row['current_utilization'])
                gb = fnum(row['predicted_utilization'])
                if stamp is None or cur is None or gb is None:
                    continue
                actual = telemetry.get((int(round(stamp)) + 5, row['link_id']))
                if actual is not None:
                    run_pairs.append((cur, gb, actual))
        per_run[run_dir.name] = run_pairs
        pairs.extend(run_pairs)
    return pairs, per_run


def dm_test(diffs, lag=5):
    """Diebold-Mariano on loss differentials with Newey-West HAC variance
    and the Harvey-Leybourne-Newbold small-sample correction (h=1)."""
    n = len(diffs)
    mean = sum(diffs) / n
    gamma = []
    for k in range(lag + 1):
        gamma.append(sum((diffs[t] - mean) * (diffs[t - k] - mean)
                         for t in range(k, n)) / n)
    var = gamma[0] + 2 * sum((1 - k / (lag + 1)) * gamma[k]
                             for k in range(1, lag + 1))
    dm = mean / math.sqrt(var / n)
    hln = math.sqrt((n + 1 - 2 + 1 * 0 / n) / n)
    dm_star = dm * hln
    p_value = math.erfc(abs(dm_star) / math.sqrt(2))
    return mean, dm_star, p_value


def forecast_report(run_dirs, label):
    pairs, per_run = pooled_forecast(run_dirs)
    def mae(fn):
        return sum(abs(fn(c, g) - a) for c, g, a in pairs) / len(pairs)
    def rmse(fn):
        return math.sqrt(sum((fn(c, g) - a) ** 2 for c, g, a in pairs) / len(pairs))
    diffs = [(c - a) ** 2 - (g - a) ** 2 for c, g, a in pairs]
    mean_diff, dm_star, p_value = dm_test(diffs)
    run_signs = []
    for name, run_pairs in sorted(per_run.items()):
        if run_pairs:
            d = sum((c - a) ** 2 - (g - a) ** 2 for c, g, a in run_pairs) / len(run_pairs)
            run_signs.append(d > 0)
    wins = sum(run_signs)
    total = len(run_signs)
    tail = sum(math.comb(total, k) for k in range(wins, total + 1)) / 2 ** total
    sign_p = min(1.0, 2 * min(tail, 1 - tail + math.comb(total, wins) / 2 ** total))
    report = dict(label=label, samples=len(pairs),
                  persistence_mae=round(mae(lambda c, g: c), 3),
                  gb_mae=round(mae(lambda c, g: g), 3),
                  persistence_rmse=round(rmse(lambda c, g: c), 3),
                  gb_rmse=round(rmse(lambda c, g: g), 3),
                  dm_mean_squared_loss_diff=round(mean_diff, 2),
                  dm_statistic=round(dm_star, 3), dm_p_value=round(p_value, 4),
                  gb_squared_loss_wins=f'{wins}/{total}', sign_test_p=round(sign_p, 4))
    print(f"forecast [{label}]: n={report['samples']}  "
          f"MAE persistence {report['persistence_mae']} vs GB {report['gb_mae']}  "
          f"RMSE persistence {report['persistence_rmse']} vs GB {report['gb_rmse']}  "
          f"DM {report['dm_statistic']} (p={report['dm_p_value']})  "
          f"GB wins squared loss in {report['gb_squared_loss_wins']} runs (sign p={report['sign_test_p']})")
    return report


def read_statistics(path):
    stats = {}
    if path and Path(path).exists():
        with open(path) as handle:
            for row in csv.DictReader(handle):
                stats[(row['System'], row['Metric'])] = row
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default='final-evaluation-artifacts/campaign-02')
    parser.add_argument('--extra', default='final-evaluation-artifacts/campaign-03')
    parser.add_argument('--combined-root', default='final-evaluation-artifacts/combined-16')
    parser.add_argument('--output-dir', default='final-evaluation-artifacts/combined-16/final-analysis')
    parser.add_argument('--forecast-only', action='store_true')
    args = parser.parse_args()
    base, extra = Path(args.base), Path(args.extra)
    combined, output = Path(args.combined_root), Path(args.output_dir)

    ml_runs = []
    for root, count in ((base, 8), (extra, 8)):
        system_dir = root / 'predictive-ml'
        found = [system_dir / name for name in sorted(os.listdir(system_dir))
                 if name.startswith('run-')] if system_dir.exists() else []
        ml_runs.extend(found[:count])
    summary = dict(forecast=forecast_report(ml_runs, 'campaign-02 + campaign-03 predictive-ml'))
    if args.forecast_only:
        return

    build_combined(base, extra, combined)
    analyzer = Path(__file__).parent / 'analyze_results.py'
    subprocess.run([sys.executable, str(analyzer), '--runs-root', str(combined),
                    '--output-dir', str(output), '--data-only'], check=True)
    new_stats = read_statistics(output / 'network_statistics.csv')
    old_stats = read_statistics(base / 'final-analysis' / 'statistics' / 'network_statistics.csv')
    comparison = {}
    for system in ('reactive', 'predictive-ml'):
        for metric in KEY_METRICS:
            new = new_stats.get((system, metric))
            if not new:
                continue
            entry = dict(n=int(float(new['N'])), mean=float(new['Mean']),
                         ci95=[float(new['CI95_Lower']), float(new['CI95_Upper'])])
            old = old_stats.get((system, metric))
            if old:
                entry['campaign02_mean'] = float(old['Mean'])
                entry['campaign02_ci95'] = [float(old['CI95_Lower']), float(old['CI95_Upper'])]
            comparison[f'{system}/{metric}'] = entry
            print(f"{system:20s} {metric:32s} n=16 mean {entry['mean']:9.3f} "
                  f"CI [{entry['ci95'][0]:.3f}, {entry['ci95'][1]:.3f}]"
                  + (f"  (was n=8: {entry['campaign02_mean']:.3f} "
                     f"[{entry['campaign02_ci95'][0]:.3f}, {entry['campaign02_ci95'][1]:.3f}])" if old else ''))
    summary['system_metrics_n16'] = comparison
    (output / 'combined_summary.json').write_text(json.dumps(summary, indent=2))
    print(f'wrote {output / "combined_summary.json"}')


if __name__ == '__main__':
    main()
