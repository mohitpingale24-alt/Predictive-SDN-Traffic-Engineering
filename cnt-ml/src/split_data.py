import pandas as pd
import os

INPUT_PATH = "data/processed/telemetry_features.csv"
TRAIN_PATH = "data/splits/train.csv"
VAL_PATH = "data/splits/val.csv"
TEST_PATH = "data/splits/test.csv"

TRAIN_RATIO = 0.70
VAL_RATIO = 0.15
TEST_RATIO = 0.15  # Should be 0.15 (remainder)

def main():
	df = pd.read_csv(INPUT_PATH)
	print(f"Loaded {len(df)} rows with features")

	# Keep all links from the same timestamp together and preserve time order.
	df = df.sort_values(["timestamp", "link_id"]).reset_index(drop=True)

	# Compute split indices
	n = len(df)
	timestamps = df["timestamp"].drop_duplicates().to_numpy()
	train_time_end = int(TRAIN_RATIO * len(timestamps))
	val_time_end = int((TRAIN_RATIO + VAL_RATIO) * len(timestamps))
	train_end = df["timestamp"].searchsorted(timestamps[train_time_end], side="left")
	val_end = df["timestamp"].searchsorted(timestamps[val_time_end], side="left")
    
	# Split chronologically (do NOT shuffle)
	train = df.iloc[:train_end]
	val = df.iloc[train_end:val_end]
	test = df.iloc[val_end:]
    
	print(f"\nChronological split (no shuffling):")
	print(f"  Train: rows 0-{train_end-1} ({len(train)} rows, {len(train)/n*100:.1f}%)")
	print(f"  Val:   rows {train_end}-{val_end-1} ({len(val)} rows, {len(val)/n*100:.1f}%)")
	print(f"  Test:  rows {val_end}-{n-1} ({len(test)} rows, {len(test)/n*100:.1f}%)")
    
	# Verify no time-order issues (timestamps should be ascending within each split)
	assert train['timestamp'].is_monotonic_increasing, "Train split not in chronological order!"
	assert val['timestamp'].is_monotonic_increasing, "Val split not in chronological order!"
	assert test['timestamp'].is_monotonic_increasing, "Test split not in chronological order!"
	print(f"\n✓ All splits are in chronological order (no leakage)")
    
	# Verify splits are contiguous in time (no gaps)
	if len(train) > 0 and len(val) > 0:
		assert train['timestamp'].iloc[-1] < val['timestamp'].iloc[0], \
			"Train and Val overlap in time!"
	if len(val) > 0 and len(test) > 0:
		assert val['timestamp'].iloc[-1] < test['timestamp'].iloc[0], \
			"Val and Test overlap in time!"
	print(f"✓ Splits do not overlap in time")
    
	# Check that required columns exist (target + features)
	required = ['target_util', 'utilization', 'timestamp']
	for col in required:
		assert col in train.columns, f"Missing column '{col}' in train"
	print(f"✓ All required columns present (target_util, features, timestamp)")
    
	# Create output directory
	os.makedirs(os.path.dirname(TRAIN_PATH), exist_ok=True)
    
	# Save splits
	train.to_csv(TRAIN_PATH, index=False)
	val.to_csv(VAL_PATH, index=False)
	test.to_csv(TEST_PATH, index=False)
    
	print(f"\nSaved splits:")
	print(f"  {TRAIN_PATH}")
	print(f"  {VAL_PATH}")
	print(f"  {TEST_PATH}")
	print(f"\nNext: run Phase F (baselines) → python src/train_baselines.py")

if __name__ == "__main__":
	main()
