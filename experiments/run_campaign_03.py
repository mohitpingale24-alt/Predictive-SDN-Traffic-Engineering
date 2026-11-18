"""Campaign-03: 32 additional controlled runs (8 per system) that extend
campaign-02 to 16 repetitions per system for the pooled analysis.

Normal entry point is the Mac-side supervisor, which boots the VM and
invokes this driver inside the guest with SDN_CAMPAIGN03_SOURCE /
SDN_CAMPAIGN03_CAPTURE set:
    python3 experiments/run_campaign_03_local.py
It can also run directly inside a campaign guest, like
resume_campaign_02.py did:
    python3 /mnt/repo/experiments/run_campaign_03.py [--dry-run]

The guest source snapshot is the frozen campaign-02 source (the audited
logging-only amendment is already applied there), so the new runs are
configuration-identical to campaign-02 and validate_controlled_campaign
accepts the pooled set. Campaign-02 captures are never touched; the new
batch lands in final-evaluation-artifacts/campaign-03/<system>/run-NN.
Existing complete runs are skipped, so an interrupted batch resumes by
simply running this script again. A failed run stops the batch.
"""
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import os

SOURCE = Path(os.environ.get('SDN_CAMPAIGN03_SOURCE',
                             '/home/mohit/campaign-02-v2-source'))
CAPTURE = Path(os.environ.get('SDN_CAMPAIGN03_CAPTURE',
                              '/mnt/repo/final-evaluation-artifacts/campaign-03'))
SYSTEMS = ('static', 'reactive', 'predictive-baseline', 'predictive-ml')
REPETITIONS = range(1, 9)
KNOWN_CONTROLLER_SHA = {
    '5c188b217be411ad1f96f3aa7a3816d33e8c6353bb61080e72524b5858db807e',
    '3e3ab24d57f63a41d516389facf2ea9ed27ad17f2255e0a0b0e26942d1298377',
}


def run_status(output_dir):
    metadata = output_dir / 'metadata.json'
    if not metadata.exists():
        return None
    try:
        return json.loads(metadata.read_text()).get('status')
    except (ValueError, OSError):
        return None


def main():
    dry_run = '--dry-run' in sys.argv
    controller = SOURCE / 'path_switch_controller.py'
    sha = hashlib.sha256(controller.read_bytes()).hexdigest()
    if sha not in KNOWN_CONTROLLER_SHA:
        raise SystemExit(f'Unexpected controller source sha256: {sha}. '
                         'Campaign-03 must use the frozen campaign-02 source.')
    plan = [(system, number) for system in SYSTEMS for number in REPETITIONS]
    pending = [(s, n) for s, n in plan
               if run_status(CAPTURE / s / f'run-{n:02d}') != 'complete']
    print(f'campaign-03: {len(plan) - len(pending)}/{len(plan)} already complete, '
          f'{len(pending)} to run (controller sha {sha[:12]}...)')
    if dry_run:
        for system, number in pending:
            print(f'  would run {system}/run-{number:02d}')
        return
    CAPTURE.mkdir(parents=True, exist_ok=True)
    manifest_path = CAPTURE / 'campaign-03.json'
    manifest = dict(campaign='campaign-03', extends='campaign-02',
                    controller_sha256=sha, systems=list(SYSTEMS),
                    repetitions_per_system=len(REPETITIONS), runs={})
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
    for system, number in pending:
        output_dir = CAPTURE / system / f'run-{number:02d}'
        started = time.time()
        print(f'[{system}/run-{number:02d}] starting...', flush=True)
        subprocess.run([sys.executable, 'experiments/run_experiment.py',
                        '--system', system, '--output-dir', str(output_dir)],
                       cwd=SOURCE, check=True)
        status = run_status(output_dir)
        if status != 'complete':
            raise SystemExit(f'{system}/run-{number:02d} finished with status '
                             f'{status!r}, not complete. Batch stopped; '
                             're-running this script resumes after it.')
        manifest['runs'][f'{system}/run-{number:02d}'] = dict(
            status=status, seconds=round(time.time() - started, 1))
        manifest_path.write_text(json.dumps(manifest, indent=2))
        print(f'[{system}/run-{number:02d}] complete in '
              f'{manifest["runs"][f"{system}/run-{number:02d}"]["seconds"]}s', flush=True)
    print('campaign-03 complete: 32/32 runs. '
          'Next on the Mac: python3 experiments/merge_campaigns.py')


if __name__ == '__main__':
    main()
