"""Run the existing experiment in an isolated VM and publish durable progress."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--system', choices=['static', 'reactive', 'predictive-baseline', 'predictive-ml',
                        'predictive-safe', 'predictive-confidence', 'predictive-safe-only', 'ecmp'], required=True)
    parser.add_argument('--scenario', default='legacy')
    parser.add_argument('--topology', choices=['diamond4', 'access6'], default='diamond4')
    parser.add_argument('--repetition', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    def status(value, message):
        temporary = args.output / 'guest.tmp'
        temporary.write_text(json.dumps(dict(status=value, message=message)))
        temporary.replace(args.output / 'guest.json')
    status('running', 'Experiment running')
    runner = 'run_experiment.py' if args.scenario == 'legacy' else 'run_research_experiment.py'
    command = [sys.executable, str(Path(__file__).with_name(runner)),
               '--system', args.system, '--output-dir', str(args.output / 'capture')]
    if args.scenario != 'legacy':
        command += ['--scenario', args.scenario, '--topology', args.topology,
                    '--repetition', str(args.repetition)]
    with (args.output / 'experiment.log').open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    status('complete' if result.returncode == 0 else 'failed',
           'Capture ready' if result.returncode == 0 else 'Experiment failed. Inspect experiment.log and capture/controller.log.')


if __name__ == '__main__': main()
