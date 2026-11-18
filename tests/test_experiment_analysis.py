import csv
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

from experiments.analyze_results import (
	active_utilization_rows,
	counterfactual_rates,
	discover_campaign,
	event_timeline,
	mean_confidence_interval,
	percentile,
	read_ping,
)


BUILD_FEATURES_PATH = Path(__file__).resolve().parents[1] / "cnt-ml" / "src" / "build_features.py"
BUILD_FEATURES_SPEC = importlib.util.spec_from_file_location("build_features", BUILD_FEATURES_PATH)
BUILD_FEATURES = importlib.util.module_from_spec(BUILD_FEATURES_SPEC)
BUILD_FEATURES_SPEC.loader.exec_module(BUILD_FEATURES)


class ExperimentAnalysisTests(unittest.TestCase):
	def test_timestamp_target_does_not_cross_sampling_gap(self):
		frame = BUILD_FEATURES.pd.DataFrame({
			"timestamp": [0, 1, 2, 3, 4, 5, 7, 8],
			"utilization": [10, 11, 12, 13, 14, 15, 17, 18],
		})
		labelled = BUILD_FEATURES.attach_timestamp_target(frame)
		row_zero = labelled[labelled["timestamp"] == 0].iloc[0]
		row_one = labelled[labelled["timestamp"] == 1].iloc[0]
		row_two = labelled[labelled["timestamp"] == 2].iloc[0]
		self.assertEqual(row_zero["target_timestamp"], 5)
		self.assertEqual(row_zero["target_util"], 15)
		self.assertTrue(BUILD_FEATURES.pd.isna(row_one["target_util"]))
		self.assertEqual(row_two["target_util"], 17)

	def test_real_dataset_preparation_is_one_second_and_non_destructive(self):
		with tempfile.TemporaryDirectory() as directory:
			run_dir = Path(directory) / "run"
			run_dir.mkdir()
			(run_dir / "metadata.json").write_text(
				json.dumps({
					"status": "complete", "system": "reactive", "model": "persistence",
					"experiment_id": "test-run", "sampling_interval_seconds": 1,
					"prediction_horizon_seconds": 5, "environment": {}, "topology": {},
					"controller": {}, "traffic": {}, "repository": {},
				}),
				encoding="utf-8",
			)
			(run_dir / "ping.log").write_text(
				"[100.0] 64 bytes from 10.0.0.2: time=4.50 ms\n"
				"[101.0] 64 bytes from 10.0.0.2: time=5.50 ms\n",
				encoding="utf-8",
			)
			(run_dir / "control_events.csv").write_text(
				"timestamp,event,control_mode,old_path,new_path,signal_a,signal_b,reason\n"
				"99,controller_start,reactive,,A,,,test\n",
				encoding="utf-8",
			)
			(run_dir / "iperf_r01_01_warmup.json").write_text(json.dumps({
				"start": {"timestamp": {"timesecs": 100}},
				"end": {"sum": {"seconds": 2, "lost_percent": 1.25, "jitter_ms": 0.4}},
			}), encoding="utf-8")
			fields = [
				"timestamp", "link_id", "utilization", "throughput", "delay",
				"packet_loss", "queue_occupancy", "active_flows",
			]
			with open(run_dir / "telemetry.csv", "w", newline="", encoding="utf-8") as handle:
				writer = csv.DictWriter(handle, fieldnames=fields)
				writer.writeheader()
				for timestamp in (100.0, 101.0):
					writer.writerow({
						"timestamp": timestamp, "link_id": "S1-S2", "utilization": 50,
						"throughput": 5, "delay": 0, "packet_loss": 0,
						"queue_occupancy": 0, "active_flows": 1,
					})
			output = Path(directory) / "candidate.csv"
			script = Path(__file__).resolve().parents[1] / "experiments" / "prepare_real_dataset.py"
			subprocess.run(
				[sys.executable, str(script), "--run-dir", str(run_dir), "--output", str(output)],
				check=True, capture_output=True, text=True,
			)
			with open(output, newline="", encoding="utf-8") as handle:
				rows = list(csv.DictReader(handle))
			self.assertEqual([row["timestamp"] for row in rows], ["0", "1"])
			self.assertEqual([row["delay"] for row in rows], ["4.5", "5.5"])
			self.assertEqual([row["packet_loss"] for row in rows], ["1.25", "1.25"])
			self.assertNotIn("queue_occupancy", rows[0])
			self.assertTrue((run_dir / "sampling_gaps.json").exists())
			second = subprocess.run(
				[sys.executable, str(script), "--run-dir", str(run_dir), "--output", str(output)],
				capture_output=True, text=True,
			)
			self.assertNotEqual(second.returncode, 0)
			self.assertIn("Refusing to overwrite", second.stderr + second.stdout)

	def test_real_dataset_excludes_uncovered_rtt_rows_with_provenance(self):
		with tempfile.TemporaryDirectory() as directory:
			run_dir = Path(directory) / "run"
			run_dir.mkdir()
			(run_dir / "metadata.json").write_text(json.dumps({
				"status": "complete", "system": "reactive", "model": "persistence",
				"experiment_id": "rtt-coverage", "sampling_interval_seconds": 1,
				"prediction_horizon_seconds": 5, "environment": {}, "topology": {},
				"controller": {}, "traffic": {}, "repository": {},
			}), encoding="utf-8")
			(run_dir / "ping.log").write_text(
				"[106.0] 64 bytes from 10.0.0.2: time=6.50 ms\n",
				encoding="utf-8",
			)
			(run_dir / "control_events.csv").write_text(
				"timestamp,event,control_mode,old_path,new_path,signal_a,signal_b,reason\n",
				encoding="utf-8",
			)
			(run_dir / "iperf_r01_01_warmup.json").write_text(json.dumps({
				"start": {"timestamp": {"timesecs": 100}},
				"end": {"sum": {"seconds": 10, "lost_percent": 0.5, "jitter_ms": 0.2}},
			}), encoding="utf-8")
			fields = ["timestamp", "link_id", "utilization", "throughput", "delay", "packet_loss", "active_flows"]
			with open(run_dir / "telemetry.csv", "w", newline="", encoding="utf-8") as handle:
				writer = csv.DictWriter(handle, fieldnames=fields)
				writer.writeheader()
				for timestamp in (100, 106):
					writer.writerow({
						"timestamp": timestamp, "link_id": "S1-S2", "utilization": 50,
						"throughput": 5, "delay": 0, "packet_loss": 0, "active_flows": 1,
					})
			output = Path(directory) / "candidate.csv"
			script = Path(__file__).resolve().parents[1] / "experiments" / "prepare_real_dataset.py"
			subprocess.run(
				[sys.executable, str(script), "--run-dir", str(run_dir), "--output", str(output)],
				check=True, capture_output=True, text=True,
			)
			with open(output, newline="", encoding="utf-8") as handle:
				rows = list(csv.DictReader(handle))
			self.assertEqual(len(rows), 1)
			self.assertEqual(rows[0]["timestamp"], "6")
			provenance = json.loads(output.with_suffix(".csv.provenance.json").read_text())
			coverage = provenance["measurement_coverage"]
			self.assertEqual(coverage["excluded_rows_without_nearby_ping"], 1)
			self.assertEqual(coverage["excluded_without_nearby_ping"][0]["absolute_timestamp"], 100.0)

	def test_percentile_interpolates(self):
		self.assertEqual(percentile([1, 2, 3, 4, 5], 50), 3)
		self.assertAlmostEqual(percentile([1, 2, 3, 4], 95), 3.85)

	def test_confidence_interval_uses_sample_standard_deviation(self):
		n, mean, standard_deviation, lower, upper = mean_confidence_interval([1, 2, 3, 4])
		self.assertEqual(n, 4)
		self.assertEqual(mean, 2.5)
		self.assertAlmostEqual(standard_deviation, 1.2909944487)
		self.assertAlmostEqual(lower, 0.44603, places=4)
		self.assertAlmostEqual(upper, 4.55397, places=4)

	def test_campaign_requires_matched_repetition_directories(self):
		with tempfile.TemporaryDirectory() as directory:
			root = Path(directory)
			for system in ("static", "reactive", "predictive-baseline", "predictive-ml"):
				for repetition in ("run-01", "run-02"):
					run = root / system / repetition
					run.mkdir(parents=True)
					(run / "metadata.json").write_text("{}", encoding="utf-8")
			discovered, repetitions = discover_campaign(root)
			self.assertEqual(repetitions, ["run-01", "run-02"])
			self.assertEqual(len(discovered["predictive-ml"]), 2)

	def test_active_path_follows_control_events(self):
		events = [
			{"timestamp": "10", "event": "controller_start", "new_path": "A"},
			{"timestamp": "20", "event": "reroute", "new_path": "B"},
		]
		telemetry = [
			{"timestamp": "15", "link_id": "S1-S2", "utilization": "80"},
			{"timestamp": "15", "link_id": "S1-S3", "utilization": "5"},
			{"timestamp": "25", "link_id": "S1-S2", "utilization": "2"},
			{"timestamp": "25", "link_id": "S1-S3", "utilization": "50"},
		]
		active = active_utilization_rows(telemetry, event_timeline(events))
		self.assertEqual(active, [(15.0, 80.0, "A"), (25.0, 50.0, "B")])

	def test_counterfactual_rates_use_static_pair(self):
		static = {
			"metadata": {"workload_started_at": 100},
			"active_rows": [(104, 80, "A"), (106, 90, "A"), (122, 40, "A")],
		}
		run = {
			"metadata": {"workload_started_at": 200},
			"reroutes": [
				{"timestamp": "205", "old_path": "A", "new_path": "B"},
				{"timestamp": "220", "old_path": "B", "new_path": "A"},
			],
			"active_rows": [(205, 60, "B"), (210, 50, "B"), (220, 30, "A")],
		}
		avoidance, false_rate, prevented, false_reroutes = counterfactual_rates(run, static)
		self.assertEqual(prevented, 1)
		self.assertEqual(false_reroutes, 0)
		self.assertEqual(avoidance, 1)
		self.assertEqual(false_rate, 0)

	def test_ping_parser(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "ping.log"
			path.write_text("[123.25] 64 bytes from 10.0.0.2: time=4.50 ms\n", encoding="utf-8")
			self.assertEqual(read_ping(path), [4.5])


if __name__ == "__main__":
	unittest.main()
