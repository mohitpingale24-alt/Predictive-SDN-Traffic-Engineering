"""Local Mac experiment supervisor: isolated VM per run, immutable campaign data."""
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / 'live-experiments'
BASE = ROOT / 'work/final-evaluation-vm/sdn-final-evaluation.qcow2'
SYSTEMS = ('static', 'reactive', 'predictive-baseline', 'predictive-ml',
           'predictive-safe', 'predictive-confidence', 'predictive-safe-only', 'ecmp')
SCENARIOS = ('legacy', 'normal', 'ramp', 'burst', 'sustained', 'near-threshold',
             'multi-flow', 'alternative-stress', 'bursty-mixed', 'elephant-mice', 'multi-pair')
TOPOLOGIES = ('diamond4', 'access6')
TERMINAL = ('complete', 'failed', 'cancelled', 'interrupted')


def atomic_json(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload, indent=2) + '\n')
    temporary.replace(path)


def session_path(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'run-\d{8}T\d{6}-[0-9a-f]{8}', identifier):
        raise ValueError('Invalid experiment identifier')
    path = LIVE / identifier
    if path.is_symlink() or path.resolve().parent != LIVE.resolve():
        raise ValueError('Invalid experiment location')
    return path


def requirements():
    binaries = {name: shutil.which(name) for name in ('qemu-system-aarch64', 'qemu-img', 'hdiutil')}
    # Finder-launched shells may omit Homebrew from PATH.
    for name in ('qemu-system-aarch64', 'qemu-img'):
        if not binaries[name] and Path('/opt/homebrew/bin', name).is_file():
            binaries[name] = str(Path('/opt/homebrew/bin', name))
    firmware = Path(binaries['qemu-system-aarch64'] or '/missing/bin/tool').resolve().parents[1] / 'share/qemu/edk2-aarch64-code.fd'
    if not firmware.is_file(): firmware = Path('/opt/homebrew/share/qemu/edk2-aarch64-code.fd')
    missing = [name for name, path in binaries.items() if not path]
    if platform.system() != 'Darwin' or platform.machine() != 'arm64': missing.append('Apple Silicon macOS runtime')
    if not BASE.is_file(): missing.append('prepared Ubuntu VM disk')
    if not firmware.is_file(): missing.append('QEMU firmware')
    if ',' in str(ROOT): missing.append('workspace path without commas')
    return dict(available=not missing, missing=missing, binaries=binaries, firmware=str(firmware))


def is_busy():
    LIVE.mkdir(exist_ok=True)
    with (LIVE / '.lock').open('a') as handle:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return True
        return False


def sessions():
    busy = is_busy()
    try: owner = (LIVE / '.lock').read_text().strip()
    except OSError: owner = ''
    items = []
    for path in sorted(LIVE.glob('run-*/session.json'), reverse=True):
        try:
            session_path(path.parent.name)
            item = json.loads(path.read_text())
            if item['status'] not in TERMINAL and (not busy or item['id'] != owner):
                item.update(status='interrupted', message='Runner stopped. Start a new experiment; capture preserved.')
            items.append(item)
        except (ValueError, KeyError, OSError): continue
    return dict(available=requirements()['available'], missing=requirements()['missing'], busy=busy, sessions=items)


def next_repetition(system, scenario, topology, source_hashes):
    """Manual repeats get unique indices within the same source configuration."""
    indices = []
    for path in LIVE.glob('run-*/capture/metadata.json'):
        try:
            meta = json.loads(path.read_text())
            if (meta.get('system') == system and meta.get('scenario') == scenario
                    and meta.get('topology') == topology
                    and meta.get('repository', {}).get('source_sha256') == source_hashes):
                value = meta.get('repetition')
                if isinstance(value, int) and not isinstance(value, bool):
                    indices.append(value)
        except (OSError, ValueError):
            continue
    return max(indices or [0]) + 1


def start(system, scenario='legacy', topology='diamond4', repetition=None, campaign_id=None):
    if system not in SYSTEMS: raise ValueError('Unknown controller system')
    if scenario not in SCENARIOS or topology not in TOPOLOGIES:
        raise ValueError('Unknown traffic scenario or topology')
    if repetition is not None and (isinstance(repetition, bool) or not isinstance(repetition, int) or not 1 <= repetition <= 1000):
        raise ValueError('Repetition must be an integer between 1 and 1000')
    if campaign_id is not None and (not isinstance(campaign_id, str)
            or not re.fullmatch(r'campaign-\d{8}T\d{6}-[0-9a-f]{8}', campaign_id)):
        raise ValueError('Invalid campaign identifier')
    if system in ('predictive-safe', 'predictive-confidence', 'predictive-safe-only') and scenario == 'legacy':
        scenario = 'ramp'
    if scenario == 'multi-flow' and topology != 'access6':
        raise ValueError('Multi-flow traffic requires the six-switch network')
    if scenario == 'legacy' and topology != 'diamond4':
        raise ValueError('The original workload uses the four-switch network')
    ready = requirements()
    if not ready['available']: raise ValueError('Missing: ' + ', '.join(ready['missing']))
    LIVE.mkdir(exist_ok=True)
    handle = (LIVE / '.lock').open('a')
    try:
        try: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError('An experiment is already running')
        if repetition is None:
            if scenario == 'legacy':
                repetition = 1
            else:
                try:
                    from .run_research_experiment import repository_provenance
                except ImportError:
                    from run_research_experiment import repository_provenance
                repetition = next_repetition(system, scenario, topology,
                    repository_provenance(ROOT)['source_sha256'])
                if repetition > 1000: raise ValueError('Too many repetitions in this configuration')
        identifier = datetime.now(timezone.utc).strftime('run-%Y%m%dT%H%M%S-') + uuid.uuid4().hex[:8]
        handle.seek(0)
        handle.truncate()
        handle.write(identifier)
        handle.flush()
        directory = session_path(identifier)
        directory.mkdir()
        item = dict(id=identifier, system=system, scenario=scenario, topology=topology,
                    repetition=repetition, campaign_id=campaign_id,
                    status='starting', message='Starting isolated network', created_at=time.time())
        atomic_json(directory / 'session.json', item)
        with (directory / 'supervisor.log').open('w') as log:
            subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker', identifier,
                              '--lock-fd', str(handle.fileno())], pass_fds=(handle.fileno(),),
                             stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=ROOT)
        return item
    finally:
        # Close this copy; the worker retains the inherited lock until it exits.
        handle.close()


def cancel(identifier):
    directory = session_path(identifier)
    item = json.loads((directory / 'session.json').read_text())
    if item['status'] in TERMINAL or not is_busy() or (LIVE / '.lock').read_text().strip() != identifier:
        raise ValueError('Experiment is no longer running')
    (directory / 'cancel.request').touch()
    return dict(status='cancelling', id=identifier)


def worker(identifier, lock_fd):
    directory = session_path(identifier)
    item = json.loads((directory / 'session.json').read_text())
    process = None
    def update(status, message):
        item.update(status=status, message=message, updated_at=time.time())
        atomic_json(directory / 'session.json', item)
    try:
        ready = requirements()
        if not ready['available']: raise RuntimeError('Missing: ' + ', '.join(ready['missing']))
        source = directory / 'source'
        source.mkdir()
        for name in ('path_switch_controller.py', 'telemetry_runtime.py', 'topology.py',
                     'route_transition.py', 'openflow_routes.py', 'confidence_policy.py',
                     'topology_graph.py', 'failure_recovery.py', 'drift_detection.py',
                     'explainability.py', 'anomaly_mitigation.py', 'topologies_v2.py',
                     'conformal_runtime.py', 'drift_adaptation.py', 'link_map.py'):
            shutil.copy2(ROOT / name, source / name)
        for name in ('experiments', 'cnt-ml/src'):
            shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns('__pycache__'))
        (source / 'cnt-ml/models').mkdir()
        for name in ('feature_list.json', 'xgb_link_predictor.joblib', 'gbdt_portable.json',
                     'gbdt_parity_fixture.json', 'gbdt_confidence.json'):
            shutil.copy2(ROOT / 'cnt-ml/models' / name, source / 'cnt-ml/models' / name)
        prepared = source / 'cnt-ml/data/real/calibration-02/prepared'
        prepared.mkdir(parents=True)
        for name in ('train.csv', 'val.csv', 'test.csv'):
            shutil.copy2(ROOT / 'cnt-ml/data/real/calibration-02/prepared' / name, prepared / name)
        seed = directory / 'seed'
        seed.mkdir()
        (seed / 'meta-data').write_text(f'instance-id: {identifier}\nlocal-hostname: sdn-live\n')
        script = f'''#!/bin/bash
set -eu
mkdir -p /mnt/source /mnt/output
mount -t 9p -o trans=virtio,version=9p2000.L,ro source /mnt/source
mount -t 9p -o trans=virtio,version=9p2000.L output /mnt/output
export PATH=/home/mohit/sdn-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export PYTHONDONTWRITEBYTECODE=1
systemctl start openvswitch-switch
python3 /mnt/source/experiments/live_guest.py --system {item['system']} --scenario {item.get('scenario', 'legacy')} --topology {item.get('topology', 'diamond4')} --repetition {item.get('repetition', 1)} --output /mnt/output
sync
poweroff
'''
        # JSON is valid YAML for cloud-init, avoiding shell interpolation of paths.
        cloud = {'write_files': [{'path': '/usr/local/sbin/sdn-live', 'permissions': '0755', 'content': script}],
                 'runcmd': [['bash', '/usr/local/sbin/sdn-live']]}
        (seed / 'user-data').write_text('#cloud-config\n' + json.dumps(cloud))
        binaries = ready['binaries']
        subprocess.run([binaries['qemu-img'], 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(BASE), str(directory / 'runtime.qcow2')], check=True, timeout=30)
        subprocess.run([binaries['hdiutil'], 'makehybrid', '-o', str(directory / 'seed.iso'), str(seed),
                        '-iso', '-joliet', '-default-volume-name', 'cidata'], check=True, timeout=60)
        command = [binaries['qemu-system-aarch64'], '-machine', 'virt,accel=hvf,highmem=off', '-cpu', 'host',
                   '-smp', '4', '-m', '3072', '-bios', ready['firmware'],
                   '-drive', f'file={directory}/runtime.qcow2,if=virtio,format=qcow2',
                   '-drive', f'file={directory}/seed.iso,media=cdrom,if=virtio,readonly=on',
                   '-virtfs', f'local,path={source},mount_tag=source,security_model=none,readonly=on',
                   '-virtfs', f'local,path={directory},mount_tag=output,security_model=none',
                   '-netdev', 'user,id=net0,restrict=on', '-device', 'virtio-net-pci,netdev=net0',
                   '-display', 'none', '-serial', f'file:{directory}/boot.log', '-monitor', 'none']
        process = subprocess.Popen(command)
        deadline = time.monotonic() + 480
        update('starting', 'Booting Linux network')
        while process.poll() is None:
            if (directory / 'cancel.request').exists():
                process.terminate()
                update('cancelled', 'Experiment stopped; partial capture preserved')
                return
            if time.monotonic() > deadline: raise TimeoutError('Network startup or experiment exceeded eight minutes. Inspect boot.log.')
            guest = directory / 'guest.json'
            if guest.exists():
                status = json.loads(guest.read_text())
                if status['status'] == 'running' and item['status'] != 'running': update('running', 'Generating traffic and collecting live telemetry')
                elif status['status'] == 'failed': raise RuntimeError(status['message'])
            time.sleep(1)
        if process.returncode: raise RuntimeError(f'VM exited with code {process.returncode}. Inspect boot.log.')
        if item.get('scenario', 'legacy') == 'legacy':
            from analyze_results import load_run
            load_run(directory / 'capture')
        else:
            from analyze_research import load_research_run
            load_research_run(directory / 'capture')
        update('complete', 'Experiment verified; replay available')
    except Exception as error:
        update('failed', str(error))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        os.close(lock_fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--worker')
    action.add_argument('--start', choices=SYSTEMS)
    parser.add_argument('--lock-fd', type=int)
    parser.add_argument('--scenario', choices=SCENARIOS, default='legacy')
    parser.add_argument('--topology', choices=TOPOLOGIES, default='diamond4')
    parser.add_argument('--repetition', type=int)
    args = parser.parse_args()
    if args.start:
        print(json.dumps(start(args.start, args.scenario, args.topology, args.repetition)), flush=True)
    else:
        if args.lock_fd is None: parser.error('--worker requires --lock-fd')
        worker(args.worker, args.lock_fd)
