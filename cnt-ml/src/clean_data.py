import pandas as pd
import numpy as np
import os

INPUT_PATH = "data/raw/telemetry_raw.csv"
OUTPUT_PATH = "data/processed/telemetry_clean.csv"

def main():
	df = pd.read_csv(INPUT_PATH)
	print(f"Loaded {len(df)} raw rows")

	before = len(df)
	df = df.drop_duplicates(subset=["timestamp", "link_id"])
	print(f"Removed {before - len(df)} duplicate rows")

	df = df.sort_values(["link_id", "timestamp"]).reset_index(drop=True)

	for col in ["utilization", "packet_loss", "queue_occupancy"]:
		bad = ((df[col] < 0) | (df[col] > 100)).sum()
		if bad > 0:
			print(f"Fixing {bad} out-of-range values in '{col}'")
			df[col] = df[col].clip(0, 100)

	for col in ["throughput", "delay"]:
		bad = (df[col] < 0).sum()
		if bad > 0:
			print(f"Fixing {bad} negative values in '{col}'")
			df[col] = df[col].clip(lower=0)

	cleaned_links = []
	for link_id, group in df.groupby("link_id"):
		group = group.set_index("timestamp")
		numeric_cols = group.select_dtypes(include=[np.number]).columns.tolist()

		full_range = np.arange(group.index.min(), group.index.max() + 1)
		missing_before = len(full_range) - len(group)

		group = group.reindex(full_range)
		group[numeric_cols] = group[numeric_cols].interpolate(
			method="linear", limit_direction="both"
		)
		group["link_id"] = link_id
		group.index.name = "timestamp"
		group = group.reset_index()
		if missing_before > 0:
			print(f"Link {link_id}: filled {missing_before} missing timestamp(s)")
		cleaned_links.append(group)

	df_clean = pd.concat(cleaned_links, ignore_index=True)
	df_clean = df_clean.sort_values(["link_id", "timestamp"]).reset_index(drop=True)

	for col in ["utilization", "throughput", "delay", "packet_loss", "queue_occupancy"]:
		df_clean[col] = df_clean[col].round(3)
	df_clean["active_flows"] = df_clean["active_flows"].round().astype(int)

	assert df_clean.isnull().sum().sum() == 0, "There are still missing values!"
	assert (df_clean["utilization"] >= 0).all() and (df_clean["utilization"] <= 100).all()

	os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
	df_clean.to_csv(OUTPUT_PATH, index=False)

	print(f"\nSaved {len(df_clean)} clean rows -> {OUTPUT_PATH}")

if __name__ == "__main__":
	main()
