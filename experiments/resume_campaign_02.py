"""Resume the preserved VM campaign after a logging-only correction."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

SOURCE = Path('/home/mohit/campaign-02-v2-source')
CAPTURE = Path('/mnt/repo/final-evaluation-artifacts/campaign-02')
OLD_SHA = '5c188b217be411ad1f96f3aa7a3816d33e8c6353bb61080e72524b5858db807e'
NEW_SHA = '3e3ab24d57f63a41d516389facf2ea9ed27ad17f2255e0a0b0e26942d1298377'

def main():
    controller = SOURCE / 'path_switch_controller.py'
    original = controller.read_text()
    assert hashlib.sha256(original.encode()).hexdigest() == OLD_SHA
    before = "                    'history_count': history_count, 'prediction_status': 'skipped_missing_rtt',\n                    'current_utilization': sample['utilization'],\n"
    after = before + "                    'signal_utilization': '', 'predicted_utilization': '',\n                    'inference_time_ms': '',\n"
    assert original.count(before) == 1
    amended = original.replace(before, after)
    assert hashlib.sha256(amended.encode()).hexdigest() == NEW_SHA
    controller.write_text(amended)
    (CAPTURE / 'source_amendment.json').write_text(json.dumps(dict(
        file='path_switch_controller.py', before_sha256=OLD_SHA, after_sha256=NEW_SHA,
        reason='Supply empty signal, prediction and timing fields in missing-RTT skip records. No model, feature, threshold, routing policy, topology or workload change.',
        affected_runs='predictive-ml/run-03 through run-08', excluded='excluded/predictive-ml-run-03'), indent=2))
    failed = CAPTURE / 'predictive-ml/run-03'
    assert json.loads((failed / 'metadata.json').read_text())['status'] == 'failed'
    (CAPTURE / 'excluded').mkdir(exist_ok=True)
    assert not (CAPTURE / 'excluded/predictive-ml-run-03').exists()
    failed.rename(CAPTURE / 'excluded/predictive-ml-run-03')
    subprocess.run(['tar', '-czf', str(CAPTURE / 'runtime-source.tar.gz'),
                    'path_switch_controller.py', 'telemetry_runtime.py', 'topology.py',
                    'experiments', 'cnt-ml/src', 'cnt-ml/models', 'cnt-ml/results/metrics_comparison.csv',
                    'cnt-ml/results/congestion_metrics.csv'], cwd=SOURCE, check=True)
    for number in range(3, 9):
        subprocess.run([sys.executable, 'experiments/run_experiment.py', '--system', 'predictive-ml',
                        '--output-dir', str(CAPTURE / f'predictive-ml/run-{number:02d}')], cwd=SOURCE, check=True)

if __name__ == '__main__':
    main()
