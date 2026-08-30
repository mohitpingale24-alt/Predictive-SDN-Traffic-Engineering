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

	baseline = 15 + 10 * np.sin(t / 600) + rng.normal(0, 1.5, n_samples)
	ramp = np.linspace(0, 25, n_samples)
	utilization = baseline + ramp

	n_bursts = max(1, n_samples // 400)
	burst_starts = rng.choice(n_samples - 60, size=n_bursts, replace=False)
	for start in burst_starts:
		length = rng.integers(20, 60)
		height = rng.uniform(25, 55)
		window = np.sin(np.linspace(0, np.pi, length)) * height
		utilization[start:start + length] += window

	n_sustained = max(1, n_samples // 1200)
	sustained_starts = rng.choice(n_samples - 200, size=n_sustained, replace=False)
	for start in sustained_starts:
		length = rng.integers(100, 200)
		utilization[start:start + length] += rng.uniform(30, 45)

	n_drops = max(1, n_samples // 1500)
	drop_points = rng.choice(n_samples - 50, size=n_drops, replace=False)
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

	queue_occupancy = np.clip(utilization * 0.9 + rng.normal(0, 3, n_samples), 0, 100)

	active_flows = np.round(2 + utilization / 15 + rng.normal(0, 0.5, n_samples)).astype(int)
	active_flows = np.clip(active_flows, 1, None)

	return pd.DataFrame({
		"timestamp": t * SAMPLE_INTERVAL,
		"link_id": link_id,
		"utilization": np.round(utilization, 3),
		"throughput": np.round(throughput, 3),
		"delay": np.round(delay, 3),
		"packet_loss": np.round(packet_loss, 3),
		"queue_occupancy": np.round(queue_occupancy, 3),
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
