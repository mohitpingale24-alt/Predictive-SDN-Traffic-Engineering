import csv
import os
import tempfile
import unittest

from telemetry_runtime import (
	FeatureHistory,
	PortRateTracker,
	ReroutePolicy,
	TelemetryCsvRecorder,
	project_path_signals,
)


class PortRateTrackerTests(unittest.TestCase):
	def test_counter_delta_produces_link_measurement(self):
		tracker = PortRateTracker()
		base = dict(
			link_id="S1-S2", capacity_mbps=10, rx_bytes=0, tx_bytes=0,
			rx_packets=0, tx_packets=0, rx_dropped=0, tx_dropped=0,
		)
		self.assertIsNone(tracker.observe(timestamp=10, **base))
		sample = tracker.observe(
			timestamp=11, **{**base, "tx_bytes": 625_000, "tx_packets": 100, "tx_dropped": 5}
		)
		self.assertAlmostEqual(sample["throughput"], 5.0)
		self.assertAlmostEqual(sample["utilization"], 50.0)
		self.assertAlmostEqual(sample["packet_loss"], 100 * 5 / 105, places=3)


class FeatureHistoryTests(unittest.TestCase):
	def test_lags_and_rolling_features_match_training_definition(self):
		history = FeatureHistory()
		features = None
		for timestamp in range(11):
			features = history.update({
				"timestamp": timestamp, "link_id": "S1-S2", "utilization": timestamp,
				"throughput": 0, "delay": 0, "packet_loss": 0, "active_flows": 1,
			})
		self.assertEqual(features["util_lag_10"], 0)
		self.assertEqual(features["util_lag_1"], 9)
		self.assertEqual(features["util_ma_5s"], 8)
		self.assertEqual(features["util_ma_10s"], 5.5)
		self.assertEqual(features["util_slope"], 1)
		self.assertEqual(history.count("S1-S2"), 11)


class ReroutePolicyTests(unittest.TestCase):
	def test_projection_prevents_return_to_under_capacity_path(self):
		projected = project_path_signals(
			"B", {"A": 0, "B": 65}, {"A": 10, "B": 20},
		)
		self.assertEqual(projected["A"], 100)
		self.assertEqual(projected["B"], 65)
		recovered = project_path_signals(
			"B", {"A": 0, "B": 25}, {"A": 10, "B": 20},
		)
		self.assertEqual(recovered["A"], 50)

	def test_requires_consecutive_predictions_and_cooldown(self):
		policy = ReroutePolicy(consecutive_samples=3, cooldown_seconds=10)
		predictions = {"A": 90, "B": 20}
		self.assertIsNone(policy.evaluate("A", predictions, 1))
		self.assertIsNone(policy.evaluate("A", predictions, 2))
		self.assertEqual(policy.evaluate("A", predictions, 3), "B")
		self.assertIsNone(policy.evaluate("B", {"A": 10, "B": 20}, 5))
		self.assertIsNone(policy.evaluate("B", {"A": 10, "B": 20}, 13))
		self.assertIsNone(policy.evaluate("B", {"A": 10, "B": 20}, 14))
		self.assertEqual(policy.evaluate("B", {"A": 10, "B": 20}, 15), "A")


class RecorderTests(unittest.TestCase):
	def test_writes_one_header_and_rows(self):
		with tempfile.TemporaryDirectory() as directory:
			path = os.path.join(directory, "telemetry.csv")
			recorder = TelemetryCsvRecorder(path)
			sample = {
				"timestamp": 1, "link_id": "S1-S2", "utilization": 10,
				"throughput": 1, "delay": 0, "packet_loss": 0, "active_flows": 1,
			}
			recorder.append(sample)
			recorder.append({**sample, "timestamp": 2})
			with open(path, newline="", encoding="utf-8") as handle:
				rows = list(csv.DictReader(handle))
			self.assertEqual(len(rows), 2)
			self.assertEqual(rows[0]["link_id"], "S1-S2")


if __name__ == "__main__":
	unittest.main()
