#!/usr/bin/env python3
"""Plan or execute matched research experiments sequentially on this Mac.

--plan reads source/protocol definitions and prints the matrix without writes.
--run creates a new campaign journal and starts isolated VMs one at a time.
Failed runs stop the campaign. --run --resume is an explicit retry/continuation,
bound to the original source fingerprint and preserving every prior attempt.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
from pathlib import Path
import re
import time
import uuid

try:
    from . import live_runtime
    from .scenarios import SCENARIOS, SYSTEM_CONFIG, TOPOLOGIES, campaign_plan
except ImportError:
    import live_runtime
    from scenarios import SCENARIOS, SYSTEM_CONFIG, TOPOLOGIES, campaign_plan


CAMPAIGN_PATTERN = re.compile(r"campaign-\d{8}T\d{6}-[0-9a-f]{8}")
CONFIG_KEYS = ("system", "scenario", "topology", "repetition")
TERMINAL = ("complete", "failed", "cancelled", "interrupted")
SOURCE_FILES = (
    "path_switch_controller.py", "telemetry_runtime.py", "route_transition.py",
    "openflow_routes.py", "confidence_policy.py", "experiments/live_runtime.py",
    "experiments/live_guest.py", "experiments/scenarios.py", "experiments/run_research_experiment.py",
    "experiments/transition_probe.py", "experiments/transition_metrics.py",
    "experiments/preflight_model.py", "experiments/quality_gate.py",
    "experiments/analyze_research.py", "experiments/run_local_campaign.py",
    "conformal_runtime.py", "link_map.py", "failure_recovery.py",
    "qos_policy.py", "acl_firewall.py", "slicing.py",
    "acl_rules.example.json", "slices.example.json",
    "drift_detection.py", "drift_adaptation.py",
    "anomaly_mitigation.py", "explainability.py", "link_latency.py",
    "topology_graph.py", "cnt-ml/src/predict.py", "cnt-ml/src/confidence.py",
    "cnt-ml/src/conformal.py", "cnt-ml/src/portable_gbdt.py",
    "cnt-ml/models/conformal_calibration.json",
    "cnt-ml/models/feature_list.json", "cnt-ml/models/xgb_link_predictor.joblib",
    "cnt-ml/models/gbdt_portable.json", "cnt-ml/models/gbdt_parity_fixture.json",
    "cnt-ml/models/gbdt_confidence.json", "cnt-ml/data/real/calibration-02/prepared/train.csv",
    "cnt-ml/data/real/calibration-02/prepared/val.csv", "cnt-ml/data/real/calibration-02/prepared/test.csv",
)
CAPTURE_SOURCE_FILES = (
    "path_switch_controller.py", "telemetry_runtime.py", "route_transition.py",
    "openflow_routes.py", "confidence_policy.py", "experiments/scenarios.py",
    "experiments/run_research_experiment.py", "experiments/transition_probe.py",
    "experiments/transition_metrics.py",
    "conformal_runtime.py", "link_map.py", "failure_recovery.py",
    "qos_policy.py", "acl_firewall.py", "slicing.py",
    "acl_rules.example.json", "slices.example.json",
    "cnt-ml/models/conformal_calibration.json",
    "cnt-ml/models/gbdt_portable.json",
    "cnt-ml/models/gbdt_confidence.json", "cnt-ml/models/feature_list.json",
)


class CampaignBlocked(RuntimeError):
    pass


def _sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(root):
    root = Path(root)
    files = set(SOURCE_FILES)
    files.update(str(path.relative_to(root)) for path in (root / "cnt-ml/src").glob("*.py"))
    missing = [name for name in sorted(files) if not (root / name).is_file()]
    if missing:
        raise CampaignBlocked("Missing campaign source/assets: " + ", ".join(missing))
    hashes = {name: _sha(root / name) for name in sorted(files)}
    base = root / "work/final-evaluation-vm/sdn-final-evaluation.qcow2"
    identity = None
    if base.exists():
        stat = base.stat()
        identity = dict(size=stat.st_size, modified_ns=stat.st_mtime_ns)
    encoded = json.dumps(dict(files=hashes, base_disk_identity=identity), sort_keys=True, separators=(",", ":")).encode()
    return dict(digest=hashlib.sha256(encoded).hexdigest(), files=hashes, base_disk_identity=identity)


def build_plan(repetitions=3, scenarios=None, topology=None, include_ablations=False):
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or not 1 <= repetitions <= 100:
        raise ValueError("Repetitions must be between 1 and 100")
    names = list(SCENARIOS) if scenarios is None else list(scenarios)
    if not names or len(set(names)) != len(names):
        raise ValueError("Choose at least one scenario, without duplicates")
    if topology is not None and topology not in TOPOLOGIES:
        raise ValueError("Unknown topology")
    result = campaign_plan(repetitions, include_ablations, names)
    if topology is not None:
        for entry in result:
            entry["topology"] = topology
    if any(entry["scenario"] == "multi-flow" and entry["topology"] != "access6" for entry in result):
        raise ValueError("Multi-flow requires the six-switch network")
    return result


def plan_summary(plan):
    return dict(experiments=len(plan), traffic_seconds=90 * len(plan),
                estimated_runtime_note="Each isolated VM adds boot, startup and capture validation time.", plan=plan)


def _atomic(path, payload):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def campaign_directory(value, runtime=live_runtime):
    parent = (Path(runtime.LIVE) / "campaigns").resolve()
    value = Path(value)
    if len(value.parts) == 1:
        directory = parent / value
    else:
        directory = value.absolute()
    if directory.name == "campaign.json":
        directory = directory.parent
    if not CAMPAIGN_PATTERN.fullmatch(directory.name) or directory.is_symlink() or directory.resolve().parent != parent:
        raise ValueError("Campaign must be an immediate directory in live-experiments/campaigns")
    if (directory / "campaign.json").is_symlink():
        raise ValueError("Invalid campaign manifest location")
    return directory


def create_campaign(plan, runtime=live_runtime, clock=time.time):
    fingerprint = source_fingerprint(runtime.ROOT)
    identifier = datetime.fromtimestamp(clock(), timezone.utc).strftime("campaign-%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:8]
    parent = Path(runtime.LIVE) / "campaigns"
    parent.mkdir(parents=True, exist_ok=True)
    directory = parent / identifier
    directory.mkdir(exist_ok=False)
    now = clock()
    manifest = dict(schema=1, campaign_id=identifier, status="planned", created_at=now,
                    updated_at=now, source_fingerprint=fingerprint, plan=plan,
                    entries=[dict(index=index, **entry, status="pending", session_id=None, attempts=[])
                             for index, entry in enumerate(plan)], blocker=None)
    _atomic(directory / "campaign.json", manifest)
    return directory


def read_manifest(directory, runtime=live_runtime):
    directory = campaign_directory(directory, runtime)
    manifest = json.loads((directory / "campaign.json").read_text())
    if manifest.get("schema") != 1 or manifest.get("campaign_id") != directory.name:
        raise ValueError("Invalid campaign identity/schema")
    plan, entries = manifest.get("plan", []), manifest.get("entries", [])
    if not plan or len(plan) != len(entries):
        raise ValueError("Invalid campaign matrix")
    identities = set()
    for index, (planned, entry) in enumerate(zip(plan, entries)):
        if entry.get("index") != index or any(entry.get(key) != planned.get(key) for key in CONFIG_KEYS):
            raise ValueError("Campaign entry does not match its plan")
        if entry["system"] not in SYSTEM_CONFIG or entry["scenario"] not in SCENARIOS or entry["topology"] not in TOPOLOGIES:
            raise ValueError("Unknown campaign configuration")
        if not isinstance(entry["repetition"], int) or isinstance(entry["repetition"], bool) or not 1 <= entry["repetition"] <= 100:
            raise ValueError("Invalid repetition index")
        identity = tuple(entry[key] for key in CONFIG_KEYS)
        if identity in identities:
            raise ValueError("Duplicate campaign repetition")
        identities.add(identity)
    return manifest


@contextmanager
def campaign_lock(directory):
    with (directory / ".driver.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CampaignBlocked("This campaign already has a running supervisor")
        yield


def is_owned_session(session, entry, campaign_id):
    return (session.get("campaign_id") == campaign_id
            and all(session.get(key) == entry.get(key) for key in CONFIG_KEYS))


def find_session(runtime, identifier):
    return next((session for session in runtime.sessions()["sessions"] if session.get("id") == identifier), None)


def recover_launch(runtime, manifest, entry):
    """Adopt a session if a crash occurred between runtime.start and journaling.

    Matching the campaign/configuration and intent timestamp avoids attaching
    to an unrelated dashboard run or reusing an earlier failed attempt.
    """
    intent = entry.get("launch_intent_at")
    if intent is None:
        return None
    used = {attempt.get("session_id") for attempt in entry.get("attempts", []) if attempt.get("session_id")}
    candidates = [session for session in runtime.sessions()["sessions"]
                  if is_owned_session(session, entry, manifest["campaign_id"])
                  and float(session.get("created_at", 0)) >= float(intent) - .01
                  and session.get("id") not in used]
    if len(candidates) > 1:
        raise CampaignBlocked("Ambiguous launch recovery; multiple sessions match the campaign intent")
    return candidates[0] if candidates else None


def verify_capture(runtime, manifest, entry, session):
    path = Path(runtime.LIVE) / session["id"] / "capture/metadata.json"
    metadata = json.loads(path.read_text())
    if metadata.get("research_schema") != 1 or metadata.get("status") != "complete" or metadata.get("quality_gate", {}).get("status") != "PASS":
        raise CampaignBlocked("Session did not produce a verified research capture")
    if metadata.get("smoke", False) or any(metadata.get(key) != entry[key] for key in CONFIG_KEYS):
        raise CampaignBlocked("Capture configuration does not match the full campaign repetition")
    actual = metadata.get("repository", {}).get("source_sha256", {})
    expected = manifest["source_fingerprint"]["files"]
    for name in CAPTURE_SOURCE_FILES:
        if actual.get(name) != expected.get(name):
            raise CampaignBlocked("Capture source fingerprint mismatch: " + name)
    for name, value in actual.items():
        if name in expected and expected[name] != value:
            raise CampaignBlocked("Capture source fingerprint mismatch: " + name)
    return str(path.parent)


def cancel_owned(runtime, manifest, entry):
    identifier = entry.get("session_id")
    if not identifier:
        recovered = recover_launch(runtime, manifest, entry)
        identifier = recovered.get("id") if recovered else None
    if not identifier:
        return False
    session = find_session(runtime, identifier)
    if session is None or not is_owned_session(session, entry, manifest["campaign_id"]):
        return False
    if session.get("status") in TERMINAL:
        return False
    runtime.cancel(identifier)
    entry["session_id"] = identifier
    if entry.get("attempts"):
        entry["attempts"][-1]["session_id"] = identifier
    return True


def wait_for_worker_release(runtime, identifier, clock, sleep, poll_seconds):
    """A terminal session may be published just before its lock is released.

    Wait only for this just-completed worker. An unrelated lock owner stops the
    campaign and is never cancelled or silently waited on as campaign work.
    """
    deadline = clock() + 10
    while runtime.is_busy():
        try:
            owner = (Path(runtime.LIVE) / ".lock").read_text().strip()
        except OSError:
            owner = None
        if owner != identifier:
            raise CampaignBlocked("Another experiment owns the local runner; campaign stopped")
        if clock() > deadline:
            raise CampaignBlocked("Completed experiment supervisor did not release its lock")
        sleep(poll_seconds)


def execute_campaign(directory, resume=False, runtime=live_runtime, poll_seconds=1.0,
                     timeout_seconds=600, clock=time.time, sleep=time.sleep, logger=print):
    if not math.isfinite(poll_seconds) or not .25 <= poll_seconds <= 5:
        raise ValueError("Poll interval must be between 0.25 and 5 seconds")
    if not math.isfinite(timeout_seconds) or not 30 <= timeout_seconds <= 1800:
        raise ValueError("Per-run timeout must be between 30 and 1800 seconds")
    directory = campaign_directory(directory, runtime)
    with campaign_lock(directory):
        manifest = read_manifest(directory, runtime)
        current_entry = None
        def persist():
            manifest["updated_at"] = clock()
            _atomic(directory / "campaign.json", manifest)
        def block(message, kind="experiment_failed"):
            manifest.update(status="blocked", blocker=dict(kind=kind, message=message,
                entry_index=current_entry.get("index") if current_entry else None,
                session_id=current_entry.get("session_id") if current_entry else None, at=clock()))
            persist()
            logger("Campaign stopped: " + message)
            return 1
        try:
            if source_fingerprint(runtime.ROOT) != manifest["source_fingerprint"]:
                return block("Source/assets changed since this campaign was created. Start a new campaign.", "source_changed")
            if manifest["status"] == "complete":
                logger("Campaign already complete: " + manifest["campaign_id"])
                return 0
            if manifest["status"] != "planned" and not resume:
                return block("Use --run --resume to explicitly continue this campaign.", "resume_required")
            manifest.update(status="running", blocker=None)
            persist()
            for entry in manifest["entries"]:
                current_entry = entry
                if entry["status"] == "complete":
                    continue
                if source_fingerprint(runtime.ROOT) != manifest["source_fingerprint"]:
                    return block("Source/assets changed during the campaign. Start a new campaign.", "source_changed")
                identifier = entry.get("session_id")
                if entry["status"] in TERMINAL:
                    # Reaching a failed terminal entry is possible only through
                    # explicit --resume. Preserve the prior attempt before retry.
                    if not resume:
                        return block("A repetition failed; explicit resume is required.")
                    identifier = None
                    entry["session_id"] = None
                    entry["status"] = "pending"
                if not identifier and entry["status"] == "launching":
                    recovered = recover_launch(runtime, manifest, entry)
                    if recovered:
                        identifier = recovered["id"]
                        entry.update(session_id=identifier, status=recovered["status"])
                        entry["attempts"][-1]["session_id"] = identifier
                        persist()
                if not identifier:
                    entry.update(status="launching", launch_intent_at=clock())
                    attempt = dict(number=len(entry["attempts"]) + 1, started_at=clock(), session_id=None, status="launching")
                    entry["attempts"].append(attempt)
                    persist()
                    session = runtime.start(entry["system"], entry["scenario"], entry["topology"],
                                            repetition=entry["repetition"], campaign_id=manifest["campaign_id"])
                    if not is_owned_session(session, entry, manifest["campaign_id"]):
                        raise CampaignBlocked("Supervisor returned an experiment without campaign ownership")
                    identifier = session["id"]
                    entry.update(session_id=identifier, status=session["status"])
                    attempt.update(session_id=identifier, status=session["status"])
                    persist()
                    logger("%d/%d %s %s %s repetition=%d %s" % (
                        entry["index"] + 1, len(manifest["entries"]), entry["system"], entry["scenario"],
                        entry["topology"], entry["repetition"], identifier))
                attempt = entry["attempts"][-1]
                deadline = max(clock(), float(attempt["started_at"])) + timeout_seconds
                while True:
                    session = find_session(runtime, identifier)
                    if session is None or not is_owned_session(session, entry, manifest["campaign_id"]):
                        raise CampaignBlocked("Owned experiment could not be identified; no other experiment was stopped")
                    status = session["status"]
                    if status != entry["status"] and status != "complete":
                        entry["status"] = status
                        attempt["status"] = status
                        persist()
                    if status in TERMINAL:
                        attempt.update(status=status, finished_at=clock(), message=session.get("message", ""))
                        if status != "complete":
                            entry["status"] = status
                            persist()
                            return block(session.get("message") or "Experiment ended with status " + status)
                        capture = verify_capture(runtime, manifest, entry, session)
                        entry.update(status="complete", capture=capture, finished_at=clock())
                        persist()
                        logger("Verified " + identifier)
                        wait_for_worker_release(runtime, identifier, clock, sleep, poll_seconds)
                        break
                    if clock() > deadline:
                        requested = cancel_owned(runtime, manifest, entry)
                        entry["status"] = "interrupted"
                        attempt.update(status="interrupted", finished_at=clock(), cancellation_requested=requested)
                        return block("Owned experiment exceeded the per-run wait limit.", "timeout")
                    sleep(poll_seconds)
            manifest.update(status="complete", finished_at=clock(), blocker=None)
            persist()
            logger("Campaign complete: %d verified experiments -> %s" % (len(manifest["entries"]), directory))
            return 0
        except KeyboardInterrupt:
            cancelled = False
            error = None
            if current_entry:
                try:
                    cancelled = cancel_owned(runtime, manifest, current_entry)
                except Exception as cause:
                    error = str(cause)
                current_entry["status"] = "cancelled" if cancelled else current_entry["status"]
                if current_entry.get("attempts"):
                    current_entry["attempts"][-1].update(cancellation_requested=cancelled, interrupted_at=clock())
            manifest.update(status="cancelled", blocker=dict(kind="user_interrupt", message="Campaign interrupted; captures preserved",
                            cancellation_requested=cancelled, cancellation_error=error, at=clock()))
            persist()
            logger("Campaign interrupted; captured experiments and attempt journal preserved")
            return 130
        except Exception as error:
            if current_entry and current_entry.get("attempts"):
                current_entry["attempts"][-1]["error"] = "%s: %s" % (type(error).__name__, error)
                if current_entry.get("status") != "complete":
                    current_entry["status"] = "failed"
                    current_entry["attempts"][-1]["status"] = "failed"
            return block("%s: %s" % (type(error).__name__, error), "supervisor_error")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--plan", action="store_true")
    action.add_argument("--run", action="store_true")
    parser.add_argument("--resume", help="Existing campaign directory or campaign ID")
    parser.add_argument("--scenarios", nargs="+", choices=tuple(SCENARIOS))
    parser.add_argument("--repetitions", type=int)
    parser.add_argument("--topology", choices=TOPOLOGIES)
    parser.add_argument("--include-ablations", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--timeout-seconds", type=float, default=600)
    args = parser.parse_args(argv)
    if not math.isfinite(args.poll_seconds) or not .25 <= args.poll_seconds <= 5:
        parser.error("Poll interval must be between 0.25 and 5 seconds")
    if not math.isfinite(args.timeout_seconds) or not 30 <= args.timeout_seconds <= 1800:
        parser.error("Per-run timeout must be between 30 and 1800 seconds")
    if args.resume:
        if args.scenarios is not None or args.repetitions is not None or args.topology is not None or args.include_ablations:
            parser.error("Resume uses the original matrix; do not supply new plan options")
        directory = campaign_directory(args.resume)
        manifest = read_manifest(directory)
        if args.plan:
            print(json.dumps(dict(campaign_id=manifest["campaign_id"], status=manifest["status"], **plan_summary(manifest["plan"])), indent=2))
            return 0
    else:
        try:
            plan = build_plan(args.repetitions if args.repetitions is not None else 3,
                              args.scenarios, args.topology, args.include_ablations)
        except ValueError as error:
            parser.error(str(error))
        if args.plan:
            print(json.dumps(plan_summary(plan), indent=2))
            return 0
        directory = create_campaign(plan)
        print("Campaign journal: " + str(directory), flush=True)
    return execute_campaign(directory, resume=bool(args.resume), poll_seconds=args.poll_seconds,
                            timeout_seconds=args.timeout_seconds, logger=lambda message: print(message, flush=True))


if __name__ == "__main__":
    raise SystemExit(main())
