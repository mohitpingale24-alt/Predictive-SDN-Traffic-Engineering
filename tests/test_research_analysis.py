"""Arithmetic, paired-reference, validation and audit checks for research data."""
from copy import deepcopy
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from experiments import analyze_research as analysis


def run_fixture(system="predictive-safe", repetition=1):
    meta = dict(capture_schema="research1", research_schema=1, status="complete",
                quality_gate={"status": "PASS"}, protocol_version="safe-te-v1",
                system=system, control_mode="predictive" if system.startswith("predictive") else system,
                model="gradient_boosting", scenario="multi-flow",
                topology="access6", repetition=repetition, smoke=False,
                workload_started_at=100.0, workload_finished_at=110.0,
                path_a_capacity_mbps=10, path_b_capacity_mbps=20,
                congestion_threshold=85, recovery_threshold=70, sampling_interval_seconds=1,
                workload=[dict(name="fixture", rate_mbps=12, duration_seconds=10)],
                flows=[dict(flow_id=i) for i in range(1, 4)],
                controller=dict(confidence_gate=system == "predictive-safe", safe_reroute=system == "predictive-safe"),
                measurement={}, repository={"source_sha256": {"controller.py": "fixture-source"}}, environment={})
    return dict(metadata=meta, run_dir=Path("/fixture/" + system), signature=(system, repetition),
                receiver_results=[dict(bytes=i * 1_000_000, seconds=10, packets=i * 100,
                                       lost_packets=(0, 20, 60)[i - 1], jitter_ms=i) for i in range(1, 4)],
                pings=[dict(flow_id=1, timestamp=101 + i, rtt_ms=10 + i) for i in range(5)],
                active_rows=[(100 + i, 40, "A") for i in range(11)],
                predictions=[dict(timestamp=101, prediction_status="predicted", inference_time_ms=1.2)],
                resources=[dict(timestamp=101, cpu_percent=.7)],
                decisions=[dict(timestamp=101, source="measured_fallback")], transitions=[], reroutes=[],
                transition_summary=dict(Transition_Probe_Loss_pct=None, Transition_P95_RTT_ms=None,
                                        Transition_RTT_Variation_ms=None))


class ResearchAnalysisTests(unittest.TestCase):
    def setUp(self):
        analysis._RUN_CACHE.clear()
        analysis._SUMMARY_CACHE.clear()

    def test_concurrent_goodput_uses_wall_time_and_packet_weighted_loss(self):
        run = run_fixture()
        with patch.object(analysis, "load_research_run", return_value=run):
            summary = analysis.summarize_research(run["run_dir"])
        self.assertAlmostEqual(summary["Average_Throughput_Mbps"], 4.8)
        # Summing simultaneous receiver durations would give 1.6 Mbps.
        self.assertNotAlmostEqual(summary["Average_Throughput_Mbps"], 1.6)
        self.assertAlmostEqual(summary["Average_Packet_Loss_pct"], 100 * 80 / 600)
        self.assertAlmostEqual(summary["Average_Jitter_ms"], 2)
        self.assertIsNone(summary["Congestion_Avoidance_Rate"])
        self.assertIsNone(summary["False_Reroutes"])
        self.assertIsNone(summary["Successful_Reroutes"])
        self.assertEqual(summary["Reference_Status"], "no_paired_static_reference")
        self.assertIsNone(summary["Prediction_to_Reroute_ms"])
        json.dumps(summary, allow_nan=False)

    def test_counterfactual_requires_static_exact_repetition_and_configuration(self):
        run, reference = run_fixture(), run_fixture("static")
        run["active_rows"] = [(100 + i, 40, "A") for i in range(11)]
        reference["active_rows"] = [(100 + i, 90 if 3 <= i <= 7 else 40, "A") for i in range(11)]
        result = analysis._counterfactual(run, reference)
        self.assertEqual(result["Congestion_Avoidance_Rate"], 1)
        for mutate in (lambda row: row.update(system="reactive"),
                       lambda row: row.update(repetition=2),
                       lambda row: row.update(path_b_capacity_mbps=8),
                       lambda row: row.update(smoke=True),
                       lambda row: row.update(repository={"source_sha256": {"controller.py": "different"}})):
            other = deepcopy(reference)
            mutate(other["metadata"])
            result = analysis._counterfactual(run, other)
            self.assertIsNone(result["Congestion_Avoidance_Rate"])
            self.assertIsNone(result["False_Reroutes"])
            self.assertEqual(result["Reference_Status"], "static_reference_configuration_mismatch")

    def test_insufficient_counterfactual_window_is_not_false_reroute(self):
        run, reference = run_fixture(), run_fixture("static")
        run["reroutes"] = [dict(timestamp=109, old_path="A", new_path="B")]
        result = analysis._counterfactual(run, reference)
        self.assertIsNone(result["False_Reroutes"])
        self.assertIsNone(result["Congestion_Relief_Rate"])
        self.assertEqual(result["Reference_Status"], "paired_static_proxy_incomplete_action_window")

    def test_transition_counts_unique_transactions_and_acknowledged_commit_only(self):
        run = run_fixture()
        run["transitions"] = [
            dict(transition_id="one", timestamp=101, stage="transaction", status="started"),
            dict(transition_id="one", timestamp=101.02, stage="commit", status="committed"),
            dict(transition_id="one", timestamp=101.52, stage="transaction", status="complete"),
            dict(transition_id="two", timestamp=102, stage="transaction", status="started"),
            dict(transition_id="two", timestamp=102.1, stage="preparing", status="failed"),
            dict(transition_id="two", timestamp=102.2, stage="transaction", status="aborted"),
            dict(transition_id="three", timestamp=103, stage="transaction", status="started"),
            dict(transition_id="three", timestamp=103.1, stage="transaction", status="complete"),
        ]
        result = analysis._transition_audit(run)
        self.assertAlmostEqual(result["Acknowledged_Commit_ms"], 20)
        self.assertEqual(result["Completed_Safe_Transitions"], 1)
        self.assertEqual(result["Failed_Transitions"], 1)

    def test_measured_fallback_breaks_forecast_latency_streak(self):
        run = run_fixture()
        run["reroutes"] = [dict(timestamp=105, old_path="A", new_path="B")]
        run["decisions"] = [
            dict(timestamp=101, active_path="A", source="learned_confidence", signal_a=90),
            dict(timestamp=102, active_path="A", source="learned_confidence", signal_a=90),
            dict(timestamp=103, active_path="A", source="measured_fallback", signal_a=90),
            dict(timestamp=104, active_path="A", source="measured_fallback", signal_a=90),
        ]
        self.assertIsNone(analysis._prediction_latency(run))

    def test_single_repetition_has_null_uncertainty_and_student_t_for_two(self):
        self.assertEqual(analysis.mean_confidence_interval([None]), (0, None, None, None, None))
        self.assertEqual(analysis.mean_confidence_interval([4]), (1, 4, None, None, None))
        n, mean, sd, low, high = analysis.mean_confidence_interval([2, 4])
        self.assertEqual((n, mean), (2, 3))
        self.assertAlmostEqual(sd, 2 ** .5)
        self.assertAlmostEqual(low, 3 - 12.706)
        self.assertAlmostEqual(high, 3 + 12.706)

    def test_congestion_time_respects_wall_end_and_does_not_fill_missing_samples(self):
        duration, events = analysis._congestion_window([(100, 90, "A"), (101, 90, "A"),
                                                       (104, 90, "A")], 100, 104.5)
        self.assertEqual(duration, 2.5)
        self.assertEqual(events, 2)

    def test_completed_capture_cache_is_invalidated_by_changed_raw_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = run_fixture()["metadata"]
            (root / "metadata.json").write_text(json.dumps(metadata))
            with patch.object(analysis, "check_research_run", return_value={"status": "PASS"}) as gate, \
                    patch.object(analysis, "_transition_summary", return_value={}):
                analysis.load_research_run(root)
                analysis.load_research_run(root)
                self.assertEqual(gate.call_count, 1)
                (root / "controller.log").write_text("Traceback (most recent call last)")
                gate.side_effect = ValueError("Controller error")
                with self.assertRaisesRegex(ValueError, "Controller error"):
                    analysis.load_research_run(root)
                self.assertEqual(gate.call_count, 2)

    def test_validation_rejects_missing_schema_status_failed_quality_and_current_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            base = run_fixture()["metadata"]
            variants = [dict(base, capture_schema="legacy"), dict(base, status="running"),
                        dict(base, quality_gate={"status": "FAIL"})]
            for metadata in variants:
                (root / "metadata.json").write_text(json.dumps(metadata))
                with self.assertRaises(ValueError):
                    analysis.load_research_run(root)
            (root / "metadata.json").write_text(json.dumps(base))
            with patch.object(analysis, "check_research_run", side_effect=ValueError("receiver missing")):
                with self.assertRaisesRegex(ValueError, "receiver missing"):
                    analysis.load_research_run(root)

    def test_overlapping_transition_windows_pool_each_probe_sequence_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = run_fixture()["metadata"]
            metadata["flows"] = [dict(flow_id=1)]
            tx = [dict(flow_id=1, sequence=i + 1, tx_timestamp=100 + i * .02,
                       payload_bytes=128, schedule_lateness_ms=0) for i in range(250)]
            rx = [dict(row, rx_timestamp=row["tx_timestamp"] + .005)
                  for row in tx if row["sequence"] != 120]
            events = [dict(timestamp=102, old_path="A", new_path="B"),
                      dict(timestamp=103, old_path="B", new_path="A")]
            pings = [dict(flow_id=1, timestamp=100 + i * .1, rtt_ms=10 + (i % 2)) for i in range(50)]
            with patch.object(analysis, "load_probe_rows", return_value=(tx, rx)), \
                    patch.object(analysis, "committed_reroutes", return_value=events), \
                    patch.object(analysis, "read_csv", return_value=[]):
                result = analysis._transition_summary(root, metadata, pings)
            self.assertEqual(result["Transition_Complete_Windows"], 2)
            self.assertEqual(result["Transition_Window_Probe_Queued"], 250)
            self.assertEqual(result["Transition_Window_Probe_Delivered"], 249)
            self.assertAlmostEqual(result["Transition_Probe_Loss_pct"], .4)
            self.assertAlmostEqual(result["Transition_RTT_Variation_ms"], 1)


if __name__ == "__main__":
    unittest.main()
