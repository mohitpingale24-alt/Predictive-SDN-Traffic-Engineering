import pandas as pd
import os

INPUT_PATH = "data/raw/telemetry_raw.csv"
OUTPUT_PATH = "data/processed/telemetry_clean.csv"

def main():
	df = pd.read_csv(INPUT_PATH)
	print(f"Loaded {len(df)} raw rows")
	if "queue_occupancy" in df.columns:
		raise ValueError("queue_occupancy is deprecated; prepare data with the seven-column schema")

	before = len(df)
	df = df.drop_duplicates(subset=["timestamp", "link_id"])
	print(f"Removed {before - len(df)} duplicate rows")

	df = df.sort_values(["link_id", "timestamp"]).reset_index(drop=True)

	for col in ["utilization", "packet_loss"]:
		bad = ((df[col] < 0) | (df[col] > 100)).sum()
		if bad > 0:
			print(f"Fixing {bad} out-of-range values in '{col}'")
			df[col] = df[col].clip(0, 100)

	for col in ["throughput", "delay"]:
		bad = (df[col] < 0).sum()
		if bad > 0:
			print(f"Fixing {bad} negative values in '{col}'")
			df[col] = df[col].clip(lower=0)

	# Preserve real sampling gaps. Fabricating intermediate telemetry would hide
	# collection quality problems and make a row offset look like a time offset.
	df_clean = df.sort_values(["link_id", "timestamp"]).reset_index(drop=True)

	for col in ["utilization", "throughput", "delay", "packet_loss"]:
		df_clean[col] = df_clean[col].round(3)
	df_clean["active_flows"] = df_clean["active_flows"].round().astype(int)

	assert df_clean.isnull().sum().sum() == 0, "There are still missing values!"
	assert (df_clean["utilization"] >= 0).all() and (df_clean["utilization"] <= 100).all()

	os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
	df_clean.to_csv(OUTPUT_PATH, index=False)

	print(f"\nSaved {len(df_clean)} clean rows -> {OUTPUT_PATH}")

if __name__ == "__main__":
	main()
