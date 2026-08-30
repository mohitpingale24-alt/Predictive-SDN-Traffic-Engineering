import json
import os
import time

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

try:
	import tensorflow as tf
	from tensorflow.keras import callbacks, layers, models
except ModuleNotFoundError as error:
	if error.name != "tensorflow":
		raise
	tf = None
	callbacks = layers = models = None

DATA_DIR = "data/splits"
MODELS_DIR = "models"
RESULTS_DIR = "results"
METRICS_CSV = os.path.join(RESULTS_DIR, "metrics_comparison.csv")

SEQUENCE_FEATURES = [
	"utilization",
	"throughput",
	"delay",
	"packet_loss",
	"queue_occupancy",
]
OPTIONAL_FEATURES = ["active_flows"]
LINK_ID_COL = "link_id"
TIMESTAMP_COL = "timestamp"
TARGET_COL = "target_util"
WINDOW_SIZE = 10
RANDOM_SEED = 42
BATCH_SIZE = 64
EPOCHS = 100
PATIENCE = 10

if tf is not None:
	tf.random.set_seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)


def resolve_feature_cols(df):
	feature_cols = list(SEQUENCE_FEATURES)
	feature_cols.extend(column for column in OPTIONAL_FEATURES if column in df.columns)
	missing = [column for column in feature_cols if column not in df.columns]
	if missing:
		raise ValueError(f"Missing expected columns: {missing}")
	return feature_cols


def build_windows(df, feature_cols, window_size):
	windows = []
	targets = []
	for _, group in df.groupby(LINK_ID_COL, sort=False):
		group = group.sort_values(TIMESTAMP_COL).reset_index(drop=True)
		features = group[feature_cols].to_numpy(dtype=np.float32)
		target = group[TARGET_COL].to_numpy(dtype=np.float32)
		for index in range(window_size, len(group)):
			windows.append(features[index - window_size:index])
			targets.append(target[index])
	return np.asarray(windows, dtype=np.float32), np.asarray(targets, dtype=np.float32)


def load_split(name):
	path = os.path.join(DATA_DIR, f"{name}.csv")
	if not os.path.exists(path):
		raise FileNotFoundError(f"Expected {path}; run split_data.py first.")
	return pd.read_csv(path)


def build_gru_model(window_size, feature_count):
	model = models.Sequential([
		layers.Input(shape=(window_size, feature_count)),
		layers.GRU(64, return_sequences=True),
		layers.Dropout(0.2),
		layers.GRU(32),
		layers.Dropout(0.2),
		layers.Dense(16, activation="relu"),
		layers.Dense(1),
	])
	model.compile(optimizer="adam", loss="mse", metrics=["mae"])
	return model


def evaluate(model, x_test, y_test):
	predictions = model.predict(x_test, verbose=0).ravel()
	timing_samples = min(200, len(x_test))
	model.predict(x_test[:1], verbose=0)
	start = time.perf_counter()
	for index in range(timing_samples):
		model.predict(x_test[index:index + 1], verbose=0)
	inference_ms = (time.perf_counter() - start) * 1000 / timing_samples
	return {
		"mae": mean_absolute_error(y_test, predictions),
		"rmse": mean_squared_error(y_test, predictions) ** 0.5,
		"r2": r2_score(y_test, predictions),
		"inference_time_ms": inference_ms,
	}


def update_metrics_csv(metrics):
	row = {
		"Model": "GRU",
		"MAE": round(metrics["mae"], 4),
		"RMSE": round(metrics["rmse"], 4),
		"R2": round(metrics["r2"], 4),
		"Inference_Time_ms": round(metrics["inference_time_ms"], 4),
	}
	os.makedirs(RESULTS_DIR, exist_ok=True)
	existing = pd.read_csv(METRICS_CSV) if os.path.exists(METRICS_CSV) else pd.DataFrame()
	if not existing.empty and "Model" in existing.columns:
		existing = existing[existing["Model"].str.lower() != "gru"]
	pd.concat([existing, pd.DataFrame([row])], ignore_index=True).to_csv(METRICS_CSV, index=False)


def main():
	if tf is None:
		raise SystemExit(
			"TensorFlow is required for Phase K. Install Python 3.11 or 3.12, "
			"select that interpreter in VS Code, then run: "
			"python -m pip install tensorflow"
		)

	print("=" * 80)
	print("PHASE K - GRU TRAINING (advanced comparison model)")
	print("=" * 80)
	train_df = load_split("train")
	val_df = load_split("val")
	test_df = load_split("test")
	feature_cols = resolve_feature_cols(train_df)
	if TARGET_COL not in train_df.columns:
		raise ValueError(f"TARGET_COL='{TARGET_COL}' not found in train.csv")
	print(f"\n[1] Sequence features ({len(feature_cols)}): {feature_cols}")
	print(f"    Target: {TARGET_COL}; window size: {WINDOW_SIZE}")

	print("\n[2] Building sliding windows (per-link, chronological, no leakage)...")
	x_train, y_train = build_windows(train_df, feature_cols, WINDOW_SIZE)
	x_val, y_val = build_windows(val_df, feature_cols, WINDOW_SIZE)
	x_test, y_test = build_windows(test_df, feature_cols, WINDOW_SIZE)
	print(f"    Train windows: {x_train.shape}")
	print(f"    Val windows:   {x_val.shape}")
	print(f"    Test windows:  {x_test.shape}")
	if not len(x_train) or not len(x_val) or not len(x_test):
		raise ValueError("One or more splits produced zero GRU windows.")

	feature_mean = x_train.reshape(-1, x_train.shape[-1]).mean(axis=0)
	feature_std = x_train.reshape(-1, x_train.shape[-1]).std(axis=0) + 1e-8
	x_train = (x_train - feature_mean) / feature_std
	x_val = (x_val - feature_mean) / feature_std
	x_test = (x_test - feature_mean) / feature_std

	print("\n[3] Building and training GRU model...")
	model = build_gru_model(WINDOW_SIZE, len(feature_cols))
	early_stop = callbacks.EarlyStopping(monitor="val_loss", patience=PATIENCE, restore_best_weights=True)
	history = model.fit(
		x_train, y_train,
		validation_data=(x_val, y_val),
		epochs=EPOCHS,
		batch_size=BATCH_SIZE,
		callbacks=[early_stop],
		verbose=2,
	)

	print("\n[4] Evaluating on test set...")
	metrics = evaluate(model, x_test, y_test)
	print(f"    MAE:  {metrics['mae']:.4f}%")
	print(f"    RMSE: {metrics['rmse']:.4f}%")
	print(f"    R2:   {metrics['r2']:.4f}")
	print(f"    Inference time: {metrics['inference_time_ms']:.4f} ms/sample")

	os.makedirs(MODELS_DIR, exist_ok=True)
	model.save(os.path.join(MODELS_DIR, "gru_link_predictor.h5"))
	config = {
		"window_size": WINDOW_SIZE,
		"feature_cols": feature_cols,
		"feature_mean": feature_mean.tolist(),
		"feature_std": feature_std.tolist(),
		"target_col": TARGET_COL,
		"model_mae": round(metrics["mae"], 4),
		"model_rmse": round(metrics["rmse"], 4),
		"model_r2": round(metrics["r2"], 4),
		"inference_time_ms": round(metrics["inference_time_ms"], 4),
		"epochs_trained": len(history.history["loss"]),
	}
	with open(os.path.join(MODELS_DIR, "gru_config.json"), "w", encoding="utf-8") as handle:
		json.dump(config, handle, indent=2)
	update_metrics_csv(metrics)
	print("\nSaved models/gru_link_predictor.h5")
	print("Saved models/gru_config.json")
	print("Updated results/metrics_comparison.csv")
	print("\nPHASE K COMPLETE")


if __name__ == "__main__":
	main()
