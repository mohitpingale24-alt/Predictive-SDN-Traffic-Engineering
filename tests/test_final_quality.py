import json
from pathlib import Path
import tempfile
import unittest
from telemetry_runtime import RecentRTT
from experiments.quality_gate import receiver_summary, check_run
from experiments.analyze_results import read_iperf, episode_avoidance, counterfactual_rates


class FinalQualityTests(unittest.TestCase):
    def test_rtt_is_causal_and_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ping.log"
            path.write_text("[10.0] reply time=4.5 ms\n[12.0] reply time=8.5 ms\n")
            rtt = RecentRTT(path)
            self.assertEqual(rtt.at(11), 4.5)
            self.assertIsNone(rtt.at(11.6))
            self.assertIsNone(rtt.at(9))
            self.assertEqual(rtt.at(12), 8.5)

    def test_sender_summary_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "receiver"):
            receiver_summary({"end": {"sum": {"sender": True, "bytes": 999}}})

    def test_receiver_aggregation_is_weighted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for i, seconds in enumerate((1, 3)):
                row = dict(sender=False, bytes=1000000, seconds=seconds, packets=100,
                           lost_packets=10 * i, jitter_ms=2 * i)
                (root / f"iperf_{i}.json").write_text(json.dumps({"server_output_json": {"end": {"sum": row}}}))
            goodput, jitter, loss = read_iperf(root)
            self.assertEqual(goodput, [4])
            self.assertEqual(jitter, [1.5])
            self.assertEqual(loss, [5])

    def test_controller_traceback_fails_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "controller.log").write_text("Traceback (most recent call last)")
            with self.assertRaisesRegex(ValueError, "Controller error"):
                check_run(root, {})

    def test_iperf_zero_end_bytes_uses_receiver_intervals(self):
        row = dict(sender=False, bytes=0, seconds=2, packets=100, lost_packets=10, jitter_ms=.2)
        payload = {"server_output_json": {"end": {"sum": row}, "intervals": [
            {"sum": dict(sender=False, bytes=9000, seconds=1)},
            {"sum": dict(sender=False, bytes=8000, seconds=1)},
        ]}}
        self.assertEqual(receiver_summary(payload)["bytes"], 17000)
        payload["server_output_json"]["intervals"] = []
        with self.assertRaisesRegex(ValueError, "contradict"):
            receiver_summary(payload)

    def test_relief_is_not_episode_avoidance(self):
        static = {"metadata": {"workload_started_at": 0}, "active_rows": [(i, 90, "A") for i in range(10, 21)]}
        run = {"metadata": {"workload_started_at": 100}, "active_rows": [(110, 90, "A"), (111, 50, "B"), (120, 50, "B")]}
        self.assertEqual(episode_avoidance(run, static), 0)
        run["active_rows"][0] = (110, 50, "B")
        self.assertEqual(episode_avoidance(run, static), 1)

    def test_false_congestion_action_is_counted(self):
        static = {"metadata": {"workload_started_at": 0}, "active_rows": [(10, 40, "A"), (11, 40, "A")]}
        run = {"metadata": {"workload_started_at": 100}, "active_rows": [(110, 20, "B")],
               "reroutes": [{"timestamp": 110, "old_path": "A", "new_path": "B"}]}
        _, false_rate, success, false = counterfactual_rates(run, static)
        self.assertEqual((false_rate, success, false), (1, 0, 1))
