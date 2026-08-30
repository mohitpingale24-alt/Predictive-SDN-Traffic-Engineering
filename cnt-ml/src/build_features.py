import pandas as pd
import numpy as np
import os

INPUT_PATH = "data/processed/telemetry_clean.csv"
OUTPUT_PATH = "data/processed/telemetry_features.csv"

HORIZON = 5   # predict 5 seconds ahead (since sampling is 1 second, this = 5 rows)

def main():
	df = pd.read_csv(INPUT_PATH)
	print(f"Loaded {len(df)} clean rows")

	processed_links = []

	for link_id, group in df.groupby("link_id"):
		group = group.sort_values("timestamp").reset_index(drop=True)

		# PHASE C — Prediction target
		group["target_util"] = group["utilization"].shift(-HORIZON)

		# PHASE D — Feature engineering
		for lag in [1, 2, 3, 5, 10]:
			group[f"util_lag_{lag}"] = group["utilization"].shift(lag)

		group["util_ma_5s"] = group["utilization"].rolling(window=5).mean()
		group["util_ma_10s"] = group["utilization"].rolling(window=10).mean()
		group["util_slope"] = group["utilization"].diff()

		processed_links.append(group)

	df_feat = pd.concat(processed_links, ignore_index=True)

	before = len(df_feat)
	df_feat = df_feat.dropna().reset_index(drop=True)
	print(f"Dropped {before - len(df_feat)} rows with incomplete history/target "
		  f"(expected at the start/end of each link's series)")

	os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
	df_feat.to_csv(OUTPUT_PATH, index=False)

	print(f"\nSaved {len(df_feat)} rows with features + target -> {OUTPUT_PATH}")
	print(f"Columns: {list(df_feat.columns)}")

if __name__ == "__main__":
	main()
