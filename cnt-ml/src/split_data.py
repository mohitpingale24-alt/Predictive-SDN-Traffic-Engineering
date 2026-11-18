import pandas as pd
import os

INPUT_PATH = "data/processed/telemetry_features.csv"
TRAIN_PATH = "data/splits/train.csv"
VAL_PATH = "data/splits/val.csv"
TEST_PATH = "data/splits/test.csv"

TRAIN_RATIO = 0.70
VAL_RATIO = 0.15
TEST_RATIO = 0.15  # Should be 0.15 (remainder)
TARGET_TIMESTAMP = "target_timestamp"

def main():
	df = pd.read_csv(INPUT_PATH)
	print(f"Loaded {len(df)} rows with features")

	# Keep all links from the same timestamp together and preserve time order.
	df = df.sort_values(["timestamp", "link_id"]).reset_index(drop=True)

	if TARGET_TIMESTAMP not in df.columns:
		raise ValueError(
			"Missing target_timestamp. Re-run src/build_features.py so split "
			"boundaries can be purged safely."
		)

	# Compute timestamp boundaries. Feature rows belong to the split containing
	# their observation timestamp, but a row is retained only when its future
	# target timestamp is in that same split.
	n = len(df)
	timestamps = df["timestamp"].drop_duplicates().to_numpy()
	if len(timestamps) < 3:
		raise ValueError("At least three unique timestamps are required to split data")
	train_time_end = max(1, int(TRAIN_RATIO * len(timestamps)))
	val_time_end = max(train_time_end + 1, int((TRAIN_RATIO + VAL_RATIO) * len(timestamps)))
	if val_time_end >= len(timestamps):
		raise ValueError("Not enough timestamps for non-empty train/val/test splits")
	train_boundary = timestamps[train_time_end]
	val_boundary = timestamps[val_time_end]

	train_candidates = df[df["timestamp"] < train_boundary]
	val_candidates = df[(df["timestamp"] >= train_boundary) & (df["timestamp"] < val_boundary)]
	test = df[df["timestamp"] >= val_boundary].copy()
	train = train_candidates[train_candidates[TARGET_TIMESTAMP] < train_boundary].copy()
	val = val_candidates[val_candidates[TARGET_TIMESTAMP] < val_boundary].copy()

	purged_train = len(train_candidates) - len(train)
	purged_val = len(val_candidates) - len(val)
	train = train.reset_index(drop=True)
	val = val.reset_index(drop=True)
	test = test.reset_index(drop=True)
    
	print(f"\nChronological split (no shuffling):")
	print(f"  Train: {len(train)} rows ({len(train)/n*100:.1f}%)")
	print(f"  Val:   {len(val)} rows ({len(val)/n*100:.1f}%)")
	print(f"  Test:  {len(test)} rows ({len(test)/n*100:.1f}%)")
	print(f"  Purged at boundaries: train={purged_train}, val={purged_val}")
    
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
	assert (train[TARGET_TIMESTAMP] < train_boundary).all(), \
		"Training labels cross into validation time"
	assert (val[TARGET_TIMESTAMP] < val_boundary).all(), \
		"Validation labels cross into test time"
	print(f"✓ Future labels do not cross split boundaries")
    
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
