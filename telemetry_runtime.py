"""Pure-Python telemetry, feature, and rerouting logic for the Ryu app.

Keeping these components independent from Ryu makes the control policy
deterministic and testable on systems where Mininet/Ryu are unavailable.
"""

from collections import defaultdict, deque
import csv
import os
import re
import threading


TELEMETRY_FIELDS = [
	"timestamp",
	"link_id",
	"utilization",
	"throughput",
	"delay",
	"packet_loss",
	"active_flows",
]

PREDICTION_FIELDS = [
	"timestamp",
	"control_mode",
	"active_path",
	"path",
	"link_id",
	"history_count",
	"prediction_status",
	"current_utilization",
	"signal_utilization",
	"predicted_utilization",
	"inference_time_ms",
]

CONTROL_EVENT_FIELDS = [
	"timestamp",
	"event",
	"control_mode",
	"old_path",
	"new_path",
	"signal_a",
	"signal_b",
	"reason",
]


class RecentRTT:
	"""Read measured end-to-end RTT causally; never invent uncovered values."""
	pattern = re.compile(r"^\[(\d+(?:\.\d+)?)\].*time[=<](\d+(?:\.\d+)?)\s*ms")

	def __init__(self, path, max_age=1.5):
		self.path = path
		self.max_age = max_age

	def at(self, now):
		try:
			with open(self.path, encoding="utf-8", errors="replace") as handle:
				lines = handle.readlines()
		except (OSError, TypeError):
			return None
		for line in reversed(lines):
			match = self.pattern.search(line)
			if match and 0 <= now - float(match[1]) <= self.max_age:
				return float(match[2])
		return None


def project_path_signals(active_path, signals, capacities_mbps):
	"""Estimate alternative utilization after moving the active path's load.

	An idle alternative reports near-zero utilization before a reroute. Comparing
	that raw value directly would underestimate the post-switch load and can cause
	oscillation. This projection transfers the active forecasted Mbps to the
	alternative path and adds any load already present there.
	"""
	if active_path not in ("A", "B") or not {"A", "B"}.issubset(signals):
		raise ValueError("active_path and A/B signals are required")
	if any(float(capacities_mbps[path]) <= 0 for path in ("A", "B")):
		raise ValueError("path capacities must be positive")
	alternative = "B" if active_path == "A" else "A"
	projected = {path: float(signals[path]) for path in ("A", "B")}
	active_mbps = projected[active_path] / 100 * float(capacities_mbps[active_path])
	transferred_percent = active_mbps / float(capacities_mbps[alternative]) * 100
	projected[alternative] = min(100.0, max(0.0, projected[alternative] + transferred_percent))
	return projected


class PortRateTracker:
	"""Convert cumulative OpenFlow port counters into one-second telemetry."""

	def __init__(self):
		self._previous = {}

	def observe(
		self,
		*,
		timestamp,
		link_id,
		capacity_mbps,
		rx_bytes,
		tx_bytes,
		rx_packets,
		tx_packets,
		rx_dropped,
		tx_dropped,
		active_flows=0,
	):
		if capacity_mbps <= 0:
			raise ValueError("capacity_mbps must be positive")
		current = {
			"timestamp": float(timestamp),
			"rx_bytes": int(rx_bytes),
			"tx_bytes": int(tx_bytes),
			"rx_packets": int(rx_packets),
			"tx_packets": int(tx_packets),
			"rx_dropped": int(rx_dropped),
			"tx_dropped": int(tx_dropped),
		}
		previous = self._previous.get(link_id)
		self._previous[link_id] = current
		if previous is None:
			return None

		elapsed = current["timestamp"] - previous["timestamp"]
		if elapsed <= 0:
			return None
		rx_delta = max(0, current["rx_bytes"] - previous["rx_bytes"])
		tx_delta = max(0, current["tx_bytes"] - previous["tx_bytes"])
		# A Mininet TCLink capacity applies per direction. Use the busier direction
		# instead of summing both and potentially reporting more than 100%.
		throughput_mbps = max(rx_delta, tx_delta) * 8 / elapsed / 1_000_000
		utilization = min(100.0, max(0.0, throughput_mbps / capacity_mbps * 100))

		packet_delta = max(
			0,
			(current["rx_packets"] + current["tx_packets"])
			- (previous["rx_packets"] + previous["tx_packets"]),
		)
		drop_delta = max(
			0,
			(current["rx_dropped"] + current["tx_dropped"])
			- (previous["rx_dropped"] + previous["tx_dropped"]),
		)
		packet_loss = 100.0 * drop_delta / (packet_delta + drop_delta) if packet_delta + drop_delta else 0.0

		return {
			"timestamp": int(timestamp),
			"link_id": link_id,
			"utilization": round(utilization, 3),
			"throughput": round(throughput_mbps, 3),
			# OpenFlow 1.3 port statistics do not expose link delay. Dataset
			# preparation replaces this explicit placeholder with measured ping RTT.
			"delay": 0.0,
			"packet_loss": round(min(100.0, packet_loss), 3),
			"active_flows": max(0, int(active_flows)),
		}


class FeatureHistory:
	"""Build the exact lag and rolling features used by tabular models."""

	def __init__(self, max_history=11, require_contiguous=True):
		if max_history < 11:
			raise ValueError("max_history must retain at least 11 samples")
		self._history = defaultdict(lambda: deque(maxlen=max_history))
		self.require_contiguous = bool(require_contiguous)

	def update(self, sample):
		missing = [field for field in TELEMETRY_FIELDS if field not in sample]
		if missing:
			raise ValueError(f"Missing telemetry fields: {missing}")
		link_history = self._history[str(sample["link_id"])]
		# Lag features are one-second lags: rows across a missing or
		# duplicate integer-second sample must never pose as one-second
		# lag observations, so a gap restarts the history by default.
		if (self.require_contiguous and link_history
				and int(sample["timestamp"]) != int(link_history[-1]["timestamp"]) + 1):
			link_history.clear()
		link_history.append(dict(sample))
		if len(link_history) < 11:
			return None

		values = [float(row["utilization"]) for row in link_history]
		features = dict(sample)
		for lag in (1, 2, 3, 5, 10):
			features[f"util_lag_{lag}"] = values[-1 - lag]
		features["util_ma_5s"] = sum(values[-5:]) / 5
		features["util_ma_10s"] = sum(values[-10:]) / 10
		features["util_slope"] = values[-1] - values[-2]
		return features

	def count(self, link_id):
		return len(self._history[str(link_id)])


class TelemetryCsvRecorder:
	"""Append controller telemetry without replacing the training CSV."""

	def __init__(self, path):
		self.path = os.path.abspath(path)
		self._lock = threading.Lock()
		os.makedirs(os.path.dirname(self.path), exist_ok=True)

	def append(self, sample):
		missing = [field for field in TELEMETRY_FIELDS if field not in sample]
		if missing:
			raise ValueError(f"Missing telemetry fields: {missing}")
		with self._lock:
			write_header = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
			with open(self.path, "a", newline="", encoding="utf-8") as handle:
				writer = csv.DictWriter(handle, fieldnames=TELEMETRY_FIELDS)
				if write_header:
					writer.writeheader()
				writer.writerow({field: sample[field] for field in TELEMETRY_FIELDS})


class StructuredCsvRecorder:
	"""Append dictionaries to a fixed-schema CSV used by experiment analysis."""

	def __init__(self, path, fieldnames):
		self.path = os.path.abspath(path)
		self.fieldnames = list(fieldnames)
		self._lock = threading.Lock()
		os.makedirs(os.path.dirname(self.path), exist_ok=True)

	def append(self, record):
		missing = [field for field in self.fieldnames if field not in record]
		if missing:
			raise ValueError(f"Missing structured log fields: {missing}")
		with self._lock:
			write_header = not os.path.exists(self.path) or os.path.getsize(self.path) == 0
			with open(self.path, "a", newline="", encoding="utf-8") as handle:
				writer = csv.DictWriter(handle, fieldnames=self.fieldnames)
				if write_header:
					writer.writeheader()
				writer.writerow({field: record[field] for field in self.fieldnames})


class ReroutePolicy:
	"""Threshold policy with consecutive decisions, hysteresis, and cooldown."""

	def __init__(
		self,
		congestion_threshold=85.0,
		recovery_threshold=70.0,
		minimum_improvement=5.0,
		consecutive_samples=3,
		cooldown_seconds=15.0,
		alternative_ceiling=None,
		commit_on_decision=True,
	):
		if recovery_threshold >= congestion_threshold:
			raise ValueError("recovery_threshold must be below congestion_threshold")
		if consecutive_samples < 1 or cooldown_seconds < 0:
			raise ValueError("invalid consecutive_samples or cooldown_seconds")
		self.congestion_threshold = float(congestion_threshold)
		self.recovery_threshold = float(recovery_threshold)
		self.minimum_improvement = float(minimum_improvement)
		self.consecutive_samples = int(consecutive_samples)
		self.cooldown_seconds = float(cooldown_seconds)
		self.alternative_ceiling = alternative_ceiling
		self.commit_on_decision = bool(commit_on_decision)
		self.last_switch_time = None
		self._candidate = None
		self._candidate_count = 0

	def reset(self):
		self._candidate = None
		self._candidate_count = 0

	def commit(self, now):
		"""Consume cooldown only once the requested route is acknowledged."""
		self.last_switch_time = float(now)
		self.reset()

	def _track(self, candidate):
		if candidate == self._candidate:
			self._candidate_count += 1
		else:
			self._candidate = candidate
			self._candidate_count = 1
		if self._candidate_count < self.consecutive_samples:
			return None
		self._candidate = None
		self._candidate_count = 0
		return candidate

	def evaluate(self, active_path, predictions, now):
		if active_path not in ("A", "B") or not {"A", "B"}.issubset(predictions):
			return None
		if self.last_switch_time is not None and now - self.last_switch_time < self.cooldown_seconds:
			self._candidate = None
			self._candidate_count = 0
			return None

		current = float(predictions[active_path])
		alternative_path = "B" if active_path == "A" else "A"
		alternative = float(predictions[alternative_path])
		candidate = None
		alternative_safe = self.alternative_ceiling is None or alternative < self.alternative_ceiling
		if current >= self.congestion_threshold and alternative <= current - self.minimum_improvement and alternative_safe:
			candidate = alternative_path
		elif (
			active_path == "B"
			and current <= self.recovery_threshold
			and alternative <= self.recovery_threshold
		):
			candidate = "A"

		if candidate is None:
			self._candidate = None
			self._candidate_count = 0
			return None
		decision = self._track(candidate)
		if decision is not None and self.commit_on_decision:
			self.last_switch_time = float(now)
		return decision
