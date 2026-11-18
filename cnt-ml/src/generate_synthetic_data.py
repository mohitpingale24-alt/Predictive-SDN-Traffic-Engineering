import numpy as np
import pandas as pd
import os

SEED = 42
DURATION_SECONDS = 3600
SAMPLE_INTERVAL = 1
LINK_CAPACITY_MBPS = 10
LINKS = ["S1-S2", "S1-S3"]
OUTPUT_PATH = "data/raw/telemetry_raw.csv"

rng = np.random.default_rng(SEED)

def generate_link_series(n_samples, link_id):
	t = np.arange(n_samples)
	phase = 0.0 if link_id == LINKS[0] else np.pi / 2

	# Multiple timescales plus a bounded random walk avoid a nearly straight,
	# globally increasing series that makes linear extrapolation unrealistically
	# easy. The second link uses a phase offset to represent competing paths.
	baseline = 28 + 10 * np.sin(t / 140 + phase) + 6 * np.sin(t / 37 + phase / 2)
	random_walk = np.cumsum(rng.normal(0, 0.18, n_samples))
	random_walk -= pd.Series(random_walk).rolling(300, min_periods=1).mean().to_numpy()
	utilization = baseline + random_walk + rng.normal(0, 1.8, n_samples)

	# Place bursts throughout the complete experiment (including every temporal
	# split), with sharp starts/stops and nonlinear decay.
	burst_starts = np.arange(90, n_samples - 90, 180)
	burst_starts = np.clip(burst_starts + rng.integers(-25, 26, len(burst_starts)), 0, n_samples - 80)
	for start in burst_starts:
		length = int(rng.integers(30, 80))
		height = rng.uniform(35, 70)
		attack = max(2, length // 8)
		plateau = max(2, length // 3)
		decay = length - attack - plateau
		window = np.concatenate([
			np.linspace(0, height, attack, endpoint=False),
			np.full(plateau, height),
			height * np.exp(-np.linspace(0, 4, decay)),
		])
		utilization[start:start + length] += window

	# Sustained competing-flow periods recur across train, validation, and test.
	sustained_starts = np.arange(420, n_samples - 220, 720)
	sustained_starts = sustained_starts + (60 if link_id == LINKS[1] else 0)
	for start in sustained_starts:
		length = int(rng.integers(120, 220))
		utilization[start:start + length] += rng.uniform(35, 55)

	# Abrupt flow completion/drop events.
	n_drops = max(2, n_samples // 900)
	drop_points = rng.choice(n_samples - 60, size=n_drops, replace=False)
	for point in drop_points:
		length = rng.integers(20, 50)
		utilization[point:point + length] *= rng.uniform(0.2, 0.5)

	utilization = np.clip(utilization, 0, 100)

	throughput = utilization / 100 * LINK_CAPACITY_MBPS
	delay = 2 + (utilization / 100) ** 3 * 40 + rng.normal(0, 0.4, n_samples)
	delay = np.clip(delay, 1, None)

	packet_loss = np.where(
		utilization > 80,
		(utilization - 80) * 0.15 + rng.normal(0, 0.05, n_samples),
		rng.normal(0, 0.01, n_samples)
	)
	packet_loss = np.clip(packet_loss, 0, 100)


	active_flows = np.round(2 + utilization / 15 + rng.normal(0, 0.5, n_samples)).astype(int)
	active_flows = np.clip(active_flows, 1, None)

	return pd.DataFrame({
		"timestamp": t * SAMPLE_INTERVAL,
		"link_id": link_id,
		"utilization": np.round(utilization, 3),
		"throughput": np.round(throughput, 3),
		"delay": np.round(delay, 3),
		"packet_loss": np.round(packet_loss, 3),
		"active_flows": active_flows,
	})

def main():
	n_samples = DURATION_SECONDS // SAMPLE_INTERVAL
	all_links = []
	for link_id in LINKS:
		df_link = generate_link_series(n_samples, link_id)
		all_links.append(df_link)

	df = pd.concat(all_links, ignore_index=True)
	df = df.sort_values(["timestamp", "link_id"]).reset_index(drop=True)

	os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
	df.to_csv(OUTPUT_PATH, index=False)

	print(f"Generated {len(df)} rows -> {OUTPUT_PATH}")
	print(df.groupby("link_id")["utilization"].describe())

if __name__ == "__main__":
	main()
