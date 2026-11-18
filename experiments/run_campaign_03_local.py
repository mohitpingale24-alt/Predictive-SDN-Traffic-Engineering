"""Campaign-03 supervisor — run this ON THE MAC:

    cd "/Users/mohit/College/SDN CP"
    python3 experiments/run_campaign_03_local.py [--dry-run]

It boots the same QEMU guest machinery the live lab uses (overlay of the
prepared VM disk, cloud-init seed ISO, 9p shares), with the guest source
taken from campaign-02's preserved runtime-source.tar.gz — the frozen
source the original 32 runs used, so the pooled n=16 analysis passes
the project's configuration validation. Captures land directly in
final-evaluation-artifacts/campaign-03/ through the output share.

The guest is stateless (fresh overlay every launch); all progress lives
in the capture directory, and the guest driver skips runs whose
metadata already says complete. Stop this script or the VM at any
point and re-run it — the batch resumes where it left off.
"""
import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'final-evaluation-artifacts'
CAPTURE = ARTIFACTS / 'campaign-03'
TARBALL = ARTIFACTS / 'campaign-02' / 'runtime-source.tar.gz'
SESSION = ROOT / 'work' / 'campaign-03' / 'session'
STATUS_FILE = ARTIFACTS / 'campaign-03-status.txt'
SYSTEMS = ('static', 'reactive', 'predictive-baseline', 'predictive-ml')
KNOWN_CONTROLLER_SHA = {
    '5c188b217be411ad1f96f3aa7a3816d33e8c6353bb61080e72524b5858db807e',
    '3e3ab24d57f63a41d516389facf2ea9ed27ad17f2255e0a0b0e26942d1298377',
}
DEADLINE_SECONDS = 4 * 3600

GUEST_SCRIPT = '''#!/bin/bash
set -eu
mkdir -p /mnt/source /mnt/output
mount -t 9p -o trans=virtio,version=9p2000.L,ro source /mnt/source
mount -t 9p -o trans=virtio,version=9p2000.L output /mnt/output
exec > >(tee /mnt/output/campaign-03-vm.log) 2>&1
trap 'rc=$?; echo "FAIL campaign-03 rc=$rc $(date --iso-8601=seconds)" > /mnt/output/campaign-03-status.txt; sync; poweroff' ERR
export PATH=/home/mohit/sdn-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONDONTWRITEBYTECODE=1
systemctl start openvswitch-switch
echo "RUNNING campaign-03 $(date --iso-8601=seconds)" > /mnt/output/campaign-03-status.txt
mn -c
SDN_CAMPAIGN03_SOURCE=/mnt/source SDN_CAMPAIGN03_CAPTURE=/mnt/output/campaign-03 python3 /mnt/source/experiments/run_campaign_03.py
echo "COMPLETE campaign-03 32 runs $(date --iso-8601=seconds)" > /mnt/output/campaign-03-status.txt
sync
poweroff
'''


def load_live_runtime():
    spec = importlib.util.spec_from_file_location(
        'sdn_live_runtime', Path(__file__).with_name('live_runtime.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed_count():
    done = 0
    for system in SYSTEMS:
        for number in range(1, 9):
            metadata = CAPTURE / system / f'run-{number:02d}' / 'metadata.json'
            try:
                if json.loads(metadata.read_text()).get('status') == 'complete':
                    done += 1
            except (OSError, ValueError):
                pass
    return done


def prepare_source():
    source = SESSION / 'source'
    marker = source / 'path_switch_controller.py'
    if not marker.exists():
        source.mkdir(parents=True, exist_ok=True)
        with tarfile.open(TARBALL) as archive:
            archive.extractall(source, filter='data')
    # The batch driver is not part of the hashed provenance set; add the
    # current copy so the guest can invoke it from the source share.
    driver = Path(__file__).with_name('run_campaign_03.py')
    (source / 'experiments' / 'run_campaign_03.py').write_bytes(driver.read_bytes())
    sha = hashlib.sha256(marker.read_bytes()).hexdigest()
    if sha not in KNOWN_CONTROLLER_SHA:
        raise SystemExit(f'Prepared source has unexpected controller sha '
                         f'{sha}; refusing to run a non-identical batch. '
                         f'Remove {SESSION} and re-run.')
    print(f'frozen source ready (controller sha {sha[:12]}...)')
    return source


def build_seed():
    seed = SESSION / 'seed'
    seed.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    (seed / 'meta-data').write_text(
        f'instance-id: sdn-campaign03-{stamp}\nlocal-hostname: sdn-final\n')
    cloud = {'write_files': [{'path': '/usr/local/sbin/sdn-campaign03',
                              'permissions': '0755', 'content': GUEST_SCRIPT}],
             'runcmd': [['bash', '/usr/local/sbin/sdn-campaign03']]}
    (seed / 'user-data').write_text('#cloud-config\n' + json.dumps(cloud))
    return seed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    runtime = load_live_runtime()
    ready = runtime.requirements()
    if not ready['available']:
        raise SystemExit('Missing runtime pieces: ' + ', '.join(ready['missing']))
    if runtime.is_busy():
        raise SystemExit('Another live experiment is running. Wait for it to '
                         'finish before starting campaign-03.')
    if not TARBALL.is_file():
        raise SystemExit(f'Frozen source tarball not found: {TARBALL}')
    done = completed_count()
    print(f'campaign-03: {done}/32 runs already complete, {32 - done} remaining')
    if args.dry_run:
        prepare_source()
        print('dry run OK: runtime available, frozen source verified, '
              'no VM started.')
        return
    SESSION.mkdir(parents=True, exist_ok=True)
    source = prepare_source()
    seed = build_seed()
    binaries = ready['binaries']
    overlay = SESSION / 'runtime.qcow2'
    overlay.unlink(missing_ok=True)
    subprocess.run([binaries['qemu-img'], 'create', '-f', 'qcow2', '-F', 'qcow2',
                    '-b', str(runtime.BASE), str(overlay)], check=True, timeout=30)
    seed_iso = SESSION / 'seed.iso'
    seed_iso.unlink(missing_ok=True)
    subprocess.run([binaries['hdiutil'], 'makehybrid', '-o', str(seed_iso),
                    str(seed), '-iso', '-joliet', '-default-volume-name',
                    'cidata'], check=True, timeout=60)
    command = [binaries['qemu-system-aarch64'],
               '-machine', 'virt,accel=hvf,highmem=off', '-cpu', 'host',
               '-smp', '4', '-m', '3072', '-bios', ready['firmware'],
               '-drive', f'file={overlay},if=virtio,format=qcow2',
               '-drive', f'file={seed_iso},media=cdrom,if=virtio,readonly=on',
               '-virtfs', f'local,path={source},mount_tag=source,security_model=none,readonly=on',
               '-virtfs', f'local,path={ARTIFACTS},mount_tag=output,security_model=none',
               '-netdev', 'user,id=net0,restrict=on',
               '-device', 'virtio-net-pci,netdev=net0',
               '-display', 'none', '-serial', f'file:{SESSION}/boot.log',
               '-monitor', 'none']
    print('booting campaign VM (no window will open; progress below)...')
    process = subprocess.Popen(command)
    started = time.monotonic()
    try:
        while process.poll() is None:
            if time.monotonic() - started > DEADLINE_SECONDS:
                process.terminate()
                raise SystemExit('Campaign exceeded the 4-hour deadline and '
                                 'was stopped. Re-run to resume.')
            current = completed_count()
            status = ''
            try:
                status = STATUS_FILE.read_text().strip()
            except OSError:
                pass
            print(f'\r  {current}/32 complete · {status or "booting..."}   ',
                  end='', flush=True)
            time.sleep(10)
    except KeyboardInterrupt:
        process.terminate()
        print('\nStopped. Completed runs are preserved; re-run this command '
              'to resume the batch.')
        return
    print()
    final_status = ''
    try:
        final_status = STATUS_FILE.read_text().strip()
    except OSError:
        pass
    done = completed_count()
    if done == 32 and final_status.startswith('COMPLETE'):
        print('campaign-03 COMPLETE: 32/32 runs captured.')
        print('Next: python3 experiments/merge_campaigns.py')
    else:
        raise SystemExit(f'VM exited with {done}/32 runs complete. Status: '
                         f'{final_status or "unavailable"}. Inspect '
                         f'{ARTIFACTS}/campaign-03-vm.log and {SESSION}/boot.log, '
                         'then re-run this command to resume.')


if __name__ == '__main__':
    main()
