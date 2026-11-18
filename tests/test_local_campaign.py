"""Local campaign journals, explicit resume and experiment ownership."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import run_local_campaign as campaign


class Clock:
    def __init__(self): self.value = 1791000000.0; self.on_sleep = None
    def now(self): return self.value
    def sleep(self, seconds):
        self.value += seconds
        if self.on_sleep: self.on_sleep()


class FakeRuntime:
    def __init__(self, root, clock):
        self.ROOT, self.LIVE, self.clock = root, root / "live-experiments", clock
        self.items = []
        self.started = []
        self.cancelled = []
        self.status_sequence = ["running", "complete"]
        self.positions = {}
        self.corrupt_source = False
        for name in campaign.SOURCE_FILES:
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture " + name)
        clock.on_sleep = self.progress

    def start(self, system, scenario, topology, repetition=1, campaign_id=None):
        number = len(self.started) + 1
        identifier = "run-20261003T%06d-%08x" % (number, number)
        item = dict(id=identifier, system=system, scenario=scenario, topology=topology,
                    repetition=repetition, campaign_id=campaign_id, status=self.status_sequence[0],
                    created_at=self.clock.now(), message="fake capture")
        self.started.append(dict(item))
        self.items.append(item)
        self.positions[identifier] = (list(self.status_sequence), 0)
        output = self.LIVE / identifier / "capture"
        output.mkdir(parents=True)
        hashes = campaign.source_fingerprint(self.ROOT)["files"]
        if self.corrupt_source: hashes["path_switch_controller.py"] = "wrong"
        meta = dict(research_schema=1, status="complete", quality_gate=dict(status="PASS"),
                    smoke=False, system=system, scenario=scenario, topology=topology,
                    repetition=repetition, repository=dict(source_sha256=hashes))
        (output / "metadata.json").write_text(json.dumps(meta))
        return dict(item)

    def sessions(self):
        return dict(sessions=[dict(item) for item in self.items])

    def progress(self):
        for item in self.items:
            sequence, position = self.positions[item["id"]]
            position = min(position + 1, len(sequence) - 1)
            item["status"] = sequence[position]
            self.positions[item["id"]] = sequence, position

    def cancel(self, identifier):
        self.cancelled.append(identifier)
        next(item for item in self.items if item["id"] == identifier)["status"] = "cancelled"

    def is_busy(self): return False


class LocalCampaignTests(unittest.TestCase):
    def fixture(self, root):
        clock = Clock()
        runtime = FakeRuntime(root, clock)
        return clock, runtime

    def execute(self, directory, runtime, clock, **kwargs):
        return campaign.execute_campaign(directory, runtime=runtime, clock=clock.now,
                                         sleep=clock.sleep, logger=lambda _message: None, **kwargs)

    def test_plan_is_read_only_and_has_105_default_experiments(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(campaign.live_runtime, "LIVE", Path(temporary) / "absent"), \
             patch.object(campaign.live_runtime, "start") as start, \
             patch.object(campaign, "source_fingerprint") as fingerprint, \
             patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(campaign.main(["--plan"]), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["experiments"], 105)
            self.assertEqual(payload["traffic_seconds"], 105 * 90)
            self.assertFalse((Path(temporary) / "absent").exists())
            start.assert_not_called()
            fingerprint.assert_not_called()

    def test_matched_subset_and_larger_topology(self):
        plan = campaign.build_plan(3, ["ramp", "multi-flow"], "access6")
        self.assertEqual(len(plan), 30)
        self.assertEqual({item["topology"] for item in plan}, {"access6"})
        self.assertEqual({item["repetition"] for item in plan}, {1, 2, 3})
        with self.assertRaisesRegex(ValueError, "Multi-flow"):
            campaign.build_plan(3, ["multi-flow"], "diamond4")

    def test_sequential_completion_binds_capture_sources_and_repetition(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            plan = campaign.build_plan(1, ["normal"])
            directory = campaign.create_campaign(plan, runtime, clock.now)
            self.assertEqual(self.execute(directory, runtime, clock), 0)
            manifest = campaign.read_manifest(directory, runtime)
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(len(runtime.started), 5)
            self.assertTrue(all(item["campaign_id"] == directory.name for item in runtime.started))
            self.assertTrue(all(item["status"] == "complete" and item["capture"] for item in manifest["entries"]))

    def test_failure_stops_without_retry_and_resume_preserves_attempts(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            runtime.status_sequence = ["running", "failed"]
            directory = campaign.create_campaign(campaign.build_plan(1, ["normal"]), runtime, clock.now)
            self.assertEqual(self.execute(directory, runtime, clock), 1)
            self.assertEqual(len(runtime.started), 1)
            before = campaign.read_manifest(directory, runtime)
            self.assertEqual(before["status"], "blocked")
            self.assertEqual(before["entries"][0]["status"], "failed")
            runtime.status_sequence = ["running", "complete"]
            self.assertEqual(self.execute(directory, runtime, clock, resume=True), 0)
            after = campaign.read_manifest(directory, runtime)
            self.assertEqual(len(runtime.started), 6)
            self.assertEqual(len(after["entries"][0]["attempts"]), 2)
            self.assertEqual(after["entries"][0]["attempts"][0]["status"], "failed")

    def test_changed_source_blocks_resume_without_starting_or_cancelling(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            directory = campaign.create_campaign(campaign.build_plan(1, ["normal"]), runtime, clock.now)
            (runtime.ROOT / "path_switch_controller.py").write_text("changed")
            self.assertEqual(self.execute(directory, runtime, clock, resume=True), 1)
            self.assertEqual(runtime.started, [])
            self.assertEqual(runtime.cancelled, [])
            self.assertEqual(campaign.read_manifest(directory, runtime)["blocker"]["kind"], "source_changed")

    def test_interrupt_cancels_only_owned_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            runtime.status_sequence = ["running"]
            directory = campaign.create_campaign(campaign.build_plan(1, ["normal"]), runtime, clock.now)
            def interrupt(): raise KeyboardInterrupt()
            clock.on_sleep = interrupt
            self.assertEqual(self.execute(directory, runtime, clock), 130)
            self.assertEqual(runtime.cancelled, [runtime.started[0]["id"]])
            self.assertEqual(campaign.read_manifest(directory, runtime)["status"], "cancelled")

    def test_interrupt_does_not_cancel_unrelated_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            runtime.status_sequence = ["running"]
            directory = campaign.create_campaign(campaign.build_plan(1, ["normal"]), runtime, clock.now)
            def interrupt():
                runtime.items[0]["campaign_id"] = None
                raise KeyboardInterrupt()
            clock.on_sleep = interrupt
            self.assertEqual(self.execute(directory, runtime, clock), 130)
            self.assertEqual(runtime.cancelled, [])
            self.assertFalse(campaign.read_manifest(directory, runtime)["blocker"]["cancellation_requested"])

    def test_resume_adopts_launch_that_was_not_yet_journaled(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            plan = campaign.build_plan(1, ["normal"])[:1]
            directory = campaign.create_campaign(plan, runtime, clock.now)
            manifest = campaign.read_manifest(directory, runtime)
            manifest["status"] = "running"
            entry = manifest["entries"][0]
            entry.update(status="launching", launch_intent_at=clock.now())
            entry["attempts"].append(dict(number=1, started_at=clock.now(), session_id=None, status="launching"))
            campaign._atomic(directory / "campaign.json", manifest)
            runtime.start(entry["system"], entry["scenario"], entry["topology"],
                          repetition=entry["repetition"], campaign_id=manifest["campaign_id"])
            self.assertEqual(self.execute(directory, runtime, clock, resume=True), 0)
            self.assertEqual(len(runtime.started), 1)
            result = campaign.read_manifest(directory, runtime)["entries"][0]
            self.assertEqual(result["session_id"], runtime.started[0]["id"])
            self.assertEqual(len(result["attempts"]), 1)

    def test_bad_capture_source_is_not_recorded_as_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            runtime.corrupt_source = True
            directory = campaign.create_campaign(campaign.build_plan(1, ["normal"]), runtime, clock.now)
            self.assertEqual(self.execute(directory, runtime, clock), 1)
            result = campaign.read_manifest(directory, runtime)
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["entries"][0]["status"], "failed")
            self.assertNotIn("capture", result["entries"][0])

    def test_campaign_manifest_cannot_escape_storage_or_be_reused(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            for value in ("../campaign-20261003T000000-12345678", "/tmp/campaign-20261003T000000-12345678"):
                with self.assertRaises(ValueError): campaign.campaign_directory(value, runtime)
            directory = campaign.create_campaign(campaign.build_plan(1, ["normal"]), runtime, clock.now)
            with campaign.campaign_lock(directory), self.assertRaises(campaign.CampaignBlocked):
                with campaign.campaign_lock(directory): pass

    def test_waits_for_completed_worker_lock_but_not_unrelated_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            clock, runtime = self.fixture(Path(temporary))
            runtime.LIVE.mkdir()
            (runtime.LIVE / ".lock").write_text("owned")
            with patch.object(runtime, "is_busy", side_effect=[True, False]):
                before = clock.now()
                campaign.wait_for_worker_release(runtime, "owned", clock.now, clock.sleep, 1)
                self.assertEqual(clock.now() - before, 1)
            (runtime.LIVE / ".lock").write_text("unrelated")
            with patch.object(runtime, "is_busy", return_value=True), self.assertRaises(campaign.CampaignBlocked):
                campaign.wait_for_worker_release(runtime, "owned", clock.now, clock.sleep, 1)
            self.assertEqual(runtime.cancelled, [])


if __name__ == "__main__":
    unittest.main()
