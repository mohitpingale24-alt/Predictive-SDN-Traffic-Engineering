import pandas as pd
import numpy as np
import os

INPUT_PATH = "data/processed/telemetry_clean.csv"
OUTPUT_PATH = "data/processed/telemetry_features.csv"

HORIZON_SECONDS = 5
LAG_SECONDS = (1, 2, 3, 5, 10)


def attach_timestamp_target(group, horizon_seconds=HORIZON_SECONDS):
	"""Attach an exact t+horizon target without assuming fixed row spacing."""
	group = group.sort_values("timestamp").reset_index(drop=True).copy()
	future = group[["timestamp", "utilization"]].rename(columns={
		"timestamp": "target_timestamp",
		"utilization": "target_util",
	})
	group["target_timestamp"] = group["timestamp"] + horizon_seconds
	return group.merge(future, on="target_timestamp", how="left", validate="many_to_one")


def contiguous_segments(group):
	"""Split one link's series wherever the sampling step breaks.

	The step is the smallest positive timestamp delta in the series
	(the sampling quantum). Any larger delta is a gap: a missing
	stretch inside one capture, or the seam where two captures were
	concatenated into one file. History restarts at every segment
	boundary, exactly as the live FeatureHistory restarts on a gap —
	a lag or rolling window may never reach across a seam and pose
	another run's samples as this run's past. Segments too short for
	the longest window simply yield no rows after the NaN drop.
	"""
	group = group.sort_values("timestamp").reset_index(drop=True)
	stamps = group["timestamp"].astype(int)
	deltas = stamps.diff()
	positive = deltas[deltas > 0]
	if positive.empty:
		return [group]
	step = positive.min()
	breaks = (deltas > step).cumsum()
	return [part.reset_index(drop=True) for _, part in group.groupby(breaks)]


def attach_time_history(group):
	"""Lag and rolling features looked up by timestamp, never row position.

	A lag of k seconds means the sample at exactly t-k. Where that sample
	does not exist (a sampling gap, a duplicate-collapsed timestamp, the
	start of the series) the feature is NaN and the row is dropped later:
	rows on the far side of a gap must not pose as one-second history.
	This matches the live FeatureHistory, which restarts on any gap.
	"""
	group = group.sort_values("timestamp").reset_index(drop=True).copy()
	stamps = group["timestamp"].astype(int)
	dedup = group.drop_duplicates(subset="timestamp")
	by_stamp = pd.Series(dedup["utilization"].to_numpy(),
						 index=dedup["timestamp"].astype(int).to_numpy())

	def at(offset):
		return stamps.sub(offset).map(by_stamp)

	for lag in LAG_SECONDS:
		group[f"util_lag_{lag}"] = at(lag)
	window_5 = [at(offset) for offset in range(5)]
	window_10 = [at(offset) for offset in range(10)]
	group["util_ma_5s"] = pd.concat(window_5, axis=1).mean(axis=1, skipna=False)
	group["util_ma_10s"] = pd.concat(window_10, axis=1).mean(axis=1, skipna=False)
	group["util_slope"] = group["utilization"] - at(1)
	return group


def main():
	df = pd.read_csv(INPUT_PATH)
	print(f"Loaded {len(df)} clean rows")

	processed_links = []

	for link_id, group in df.groupby("link_id"):
		# PHASE C/D run per contiguous segment: targets and history
		# are computed inside one unbroken sampling run, never across
		# a gap or a concatenation seam between captures.
		for segment in contiguous_segments(group):
			segment = attach_timestamp_target(segment)
			segment = attach_time_history(segment)
			processed_links.append(segment)

	df_feat = pd.concat(processed_links, ignore_index=True)

	before = len(df_feat)
	df_feat = df_feat.dropna().reset_index(drop=True)
	print(f"Dropped {before - len(df_feat)} rows with incomplete history/target "
		  f"(expected at the start/end of each link's series and around sampling gaps)")

	os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
	df_feat.to_csv(OUTPUT_PATH, index=False)

	print(f"\nSaved {len(df_feat)} rows with features + target -> {OUTPUT_PATH}")
	print(f"Columns: {list(df_feat.columns)}")

if __name__ == "__main__":
	main()
