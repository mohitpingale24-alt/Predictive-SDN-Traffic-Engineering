import os
import time

import numpy as np
import pandas as pd
import joblib
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

TRAIN_PATH = "data/splits/train.csv"
VAL_PATH = "data/splits/val.csv"
TEST_PATH = "data/splits/test.csv"
OUTPUT_PATH = "results/metrics_comparison.csv"
LINEAR_MODEL_PATH = "models/linear_link_predictor.joblib"
FEATURES_PATH = "models/feature_list.json"
TARGET = "target_util"

FEATURES = [
	"utilization",
	"throughput",
	"delay",
	"packet_loss",
	"active_flows",
	"util_lag_1",
	"util_lag_2",
	"util_lag_3",
	"util_lag_5",
	"util_lag_10",
	"util_ma_5s",
	"util_ma_10s",
	"util_slope",
]


def evaluate_predictions(y_true, predictions, elapsed_seconds):
	return {
		"MAE": mean_absolute_error(y_true, predictions),
		"RMSE": mean_squared_error(y_true, predictions) ** 0.5,
		"R2": r2_score(y_true, predictions),
		"Inference_Time_ms": elapsed_seconds * 1000 / len(predictions),
	}


def main():
	print("=" * 70)
	print("PHASE F - BASELINE MODELS (Persistence + Linear Regression)")
	print("=" * 70)

	print("\n[1] Loading data splits...")
	train = pd.read_csv(TRAIN_PATH)
	val = pd.read_csv(VAL_PATH)
	test = pd.read_csv(TEST_PATH)
	print(f"  Train: {len(train)} rows")
	print(f"  Val:   {len(val)} rows")
	print(f"  Test:  {len(test)} rows")

	missing = set(FEATURES + [TARGET]) - set(train.columns)
	if missing:
		raise ValueError(f"Missing required columns: {sorted(missing)}")

	x_train = train[FEATURES]
	y_train = train[TARGET]
	x_test = test[FEATURES]
	y_test = test[TARGET]

	print(f"\n[2] Features identified ({len(FEATURES)} total):")
	print(f"  {FEATURES}")
	print("\n[3] Feature shapes:")
	print(f"  X_train: {x_train.shape}")
	print(f"  y_train: {y_train.shape}")
	print(f"  X_test:  {x_test.shape}")
	print(f"  y_test:  {y_test.shape}")

	print("\n" + "=" * 70)
	print("BASELINE 1: PERSISTENCE (future = current)")
	print("=" * 70)
	start = time.perf_counter()
	persistence_predictions = test["utilization"].to_numpy()
	persistence_elapsed = time.perf_counter() - start
	persistence_metrics = evaluate_predictions(
		y_test, persistence_predictions, persistence_elapsed
	)
	print(f"  MAE:              {persistence_metrics['MAE']:.4f} %")
	print(f"  RMSE:             {persistence_metrics['RMSE']:.4f} %")
	print(f"  R2:               {persistence_metrics['R2']:.4f}")
	print(f"  Inference time:   {persistence_metrics['Inference_Time_ms']:.4f} ms/sample")
	print("\n  Interpretation: Assumes future utilization = current utilization.")
	print("                 This is the naive baseline to beat.")

	print("\n" + "=" * 70)
	print("BASELINE 2: LINEAR REGRESSION")
	print("=" * 70)
	print(f"  Training on {len(x_train)} samples...")
	linear_model = LinearRegression()
	linear_model.fit(x_train, y_train)
	print("  Linear Regression trained")
	os.makedirs("models", exist_ok=True)
	joblib.dump(linear_model, LINEAR_MODEL_PATH)
	start = time.perf_counter()
	linear_predictions = linear_model.predict(x_test)
	linear_elapsed = time.perf_counter() - start
	linear_metrics = evaluate_predictions(y_test, linear_predictions, linear_elapsed)
	print(f"\n  MAE:              {linear_metrics['MAE']:.4f} %")
	print(f"  RMSE:             {linear_metrics['RMSE']:.4f} %")
	print(f"  R2:               {linear_metrics['R2']:.4f}")
	print(f"  Inference time:   {linear_metrics['Inference_Time_ms']:.4f} ms/sample")

	weights = pd.Series(linear_model.coef_, index=FEATURES)
	print("\n  Top 5 features by absolute weight:")
	for rank, (feature, weight) in enumerate(weights.abs().sort_values(ascending=False).head(5).items(), 1):
		print(f"    {rank}. {feature:<20} = {weights[feature]: .4f}")

	comparison = pd.DataFrame([
		{"Model": "Persistence", **persistence_metrics},
		{"Model": "Linear Regression", **linear_metrics},
	])
	os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
	comparison.to_csv(OUTPUT_PATH, index=False)
	import json
	with open(FEATURES_PATH, "w", encoding="utf-8") as handle:
		json.dump({
			"features": FEATURES,
			"target": TARGET,
			"horizon_seconds": 5,
		}, handle, indent=2)

	print("\n" + "=" * 70)
	print("BASELINE COMPARISON")
	print("=" * 70)
	print(comparison.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
	improvement = persistence_metrics["MAE"] - linear_metrics["MAE"]
	if improvement > 0:
		print(f"\n  Linear Regression beats Persistence by {improvement:.4f}% MAE")
	else:
		print(f"\n  Persistence beats Linear Regression by {-improvement:.4f}% MAE")
	print(f"\n  XGBoost target: beat MAE of {min(persistence_metrics['MAE'], linear_metrics['MAE']):.4f}%")
	print(f"\nSaved baseline metrics -> {OUTPUT_PATH}")
	print(f"Saved Linear Regression model -> {LINEAR_MODEL_PATH}")


if __name__ == "__main__":
	main()
