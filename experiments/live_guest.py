"""Run the existing experiment in an isolated VM and publish durable progress."""
import argparse
import json
import os
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
    # Last line of defense: the legacy runner cannot execute 'ecmp'
    # (its system choices exclude it), so never dispatch ecmp there
    # even if a caller bypasses live_runtime's scenario coercion.
    if args.system == 'ecmp' and args.scenario == 'legacy':
        args.scenario = 'ramp'
    runner = 'run_experiment.py' if args.scenario == 'legacy' else 'run_research_experiment.py'
    command = [sys.executable, str(Path(__file__).with_name(runner)),
               '--system', args.system, '--output-dir', str(args.output / 'capture')]
    if args.scenario != 'legacy':
        command += ['--scenario', args.scenario, '--topology', args.topology,
                    '--repetition', str(args.repetition)]
    # Extension wiring: the runners copy os.environ into the controller
    # environment, so feature switches are set here, once. QoS meters only
    # bind DSCP-marked traffic and the ACL/slicing rules never match the
    # experiment flows (verified against experiments/scenarios.py ports
    # and host pairs), so enabling them does not change workload
    # semantics; conformal gating is enabled only for the systems whose
    # design already includes a confidence gate.
    source_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.setdefault('SDN_QOS', '1')
    environment.setdefault('SDN_ACL_PATH', str(source_root / 'acl_rules.example.json'))
    environment.setdefault('SDN_SLICES_PATH', str(source_root / 'slices.example.json'))
    environment['SDN_RECALIBRATION_PATH'] = str(args.output / 'capture' / 'recalibration.json')
    try:
        from scenarios import SYSTEM_CONFIG
        confidence_gated = bool(SYSTEM_CONFIG[args.system]['confidence_gate'])
    except Exception:
        confidence_gated = args.system in ('predictive-safe', 'predictive-confidence')
    if confidence_gated:
        environment.setdefault('SDN_CONFORMAL', '1')
    with (args.output / 'experiment.log').open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                env=environment)
    status('complete' if result.returncode == 0 else 'failed',
           'Capture ready' if result.returncode == 0 else 'Experiment failed. Inspect experiment.log and capture/controller.log.')


if __name__ == '__main__': main()
