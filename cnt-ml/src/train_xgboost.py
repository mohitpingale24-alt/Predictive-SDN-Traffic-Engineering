import json
import os
import time
import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

XGBOOST_AVAILABLE = False
XGBOOST_LOAD_ERROR = None
BOOSTING_ENGINE = os.getenv("SDN_BOOSTING_ENGINE", "sklearn_gbdt").strip().lower()
from sklearn.ensemble import GradientBoostingRegressor
if BOOSTING_ENGINE == "native_xgboost":
	try:
		from xgboost import XGBRegressor
		_test_model = XGBRegressor(n_estimators=1)
		_test_model.fit([[0]], [0])
		XGBOOST_AVAILABLE = True
	except Exception as error:
		XGBOOST_LOAD_ERROR = error
		XGBOOST_AVAILABLE = False
elif BOOSTING_ENGINE != "sklearn_gbdt":
	raise ValueError("SDN_BOOSTING_ENGINE must be sklearn_gbdt or native_xgboost")

TRAIN_PATH = "data/splits/train.csv"
VAL_PATH = "data/splits/val.csv"
TEST_PATH = "data/splits/test.csv"
MODEL_PATH_XGB = "models/xgb_link_predictor.json"
MODEL_PATH_GBDT = "models/xgb_link_predictor.joblib"
FEATURES_PATH = "models/feature_list.json"
METRICS_PATH = "results/metrics_comparison.csv"
TARGET = "target_util"
CONGESTION_THRESHOLD = 85.0

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

BASE_SEARCH_SPACE = [
	{"n_estimators": 100, "max_depth": 2, "learning_rate": 0.03, "subsample": 1.0, "colsample_bytree": 1.0},
	{"n_estimators": 200, "max_depth": 2, "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.8},
	{"n_estimators": 200, "max_depth": 3, "learning_rate": 0.05, "subsample": 1.0, "colsample_bytree": 0.8},
	{"n_estimators": 300, "max_depth": 3, "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 1.0},
	{"n_estimators": 200, "max_depth": 4, "learning_rate": 0.05, "subsample": 0.8, "colsample_bytree": 0.8},
	{"n_estimators": 300, "max_depth": 5, "learning_rate": 0.03, "subsample": 0.8, "colsample_bytree": 0.8},
]
SEARCH_SPACE = [
	{**parameters, "high_load_weight": high_load_weight}
	for parameters in BASE_SEARCH_SPACE
	for high_load_weight in (1, 3, 8, 20, 30)
]


def create_model(parameters):
	common = {
		"n_estimators": parameters["n_estimators"],
		"max_depth": parameters["max_depth"],
		"learning_rate": parameters["learning_rate"],
		"subsample": parameters["subsample"],
		"random_state": 42,
	}
	if XGBOOST_AVAILABLE:
		return XGBRegressor(
			**common,
			colsample_bytree=parameters["colsample_bytree"],
			objective="reg:squarederror",
			verbosity=0,
		)
	return GradientBoostingRegressor(
		**common,
		max_features=parameters["colsample_bytree"],
	)


def main():
	print("=" * 70)
	print("PHASE G - GRADIENT BOOSTING / XGBOOST TRAINING")
	print("=" * 70)

	print("\n[1] Loading data splits...")
	train = pd.read_csv(TRAIN_PATH)
	val = pd.read_csv(VAL_PATH)
	test = pd.read_csv(TEST_PATH)
	print(f"  Train: {len(train)} rows")
	print(f"  Val:   {len(val)} rows")
	print(f"  Test:  {len(test)} rows")

	required = set(FEATURES + [TARGET])
	missing = required - set(train.columns) | required - set(val.columns) | required - set(test.columns)
	if missing:
		raise ValueError(f"Missing required columns: {sorted(missing)}")

	x_train, y_train = train[FEATURES], train[TARGET]
	x_val, y_val = val[FEATURES], val[TARGET]
	x_test, y_test = test[FEATURES], test[TARGET]
	print(f"\n[2] Features identified ({len(FEATURES)} total):")
	print(f"  {FEATURES}")
	print("\n[3] Feature shapes:")
	print(f"  X_train: {x_train.shape}, X_val: {x_val.shape}, X_test: {x_test.shape}")

	if XGBOOST_AVAILABLE:
		print("\n[Engine] Native XGBoost engine active.")
	else:
		if XGBOOST_LOAD_ERROR is not None:
			print(f"\n[Engine] Native XGBoost unavailable: {XGBOOST_LOAD_ERROR}")
		print("\n[Engine] Using requested scikit-learn GradientBoostingRegressor.")

	print("\n" + "=" * 70)
	print("MODEL TRAINING")
	print("=" * 70)
	print(f"\n[4] Validation-only congestion-aware search ({len(SEARCH_SPACE)} candidates)")
	start = time.perf_counter()
	best_model = None
	best_parameters = None
	best_val_rmse = float("inf")
	best_val_f1 = -1.0
	best_val_precision = 0.0
	best_val_recall = 0.0
	for index, parameters in enumerate(SEARCH_SPACE, 1):
		candidate = create_model(parameters)
		sample_weight = np.where(
			y_train.to_numpy() >= CONGESTION_THRESHOLD,
			parameters["high_load_weight"],
			1.0,
		)
		candidate.fit(x_train, y_train, sample_weight=sample_weight)
		val_predictions = candidate.predict(x_val)
		val_rmse = mean_squared_error(y_val, val_predictions) ** 0.5
		actual_high = y_val.to_numpy() >= CONGESTION_THRESHOLD
		predicted_high = val_predictions >= CONGESTION_THRESHOLD
		tp = int((actual_high & predicted_high).sum())
		fp = int((~actual_high & predicted_high).sum())
		fn = int((actual_high & ~predicted_high).sum())
		precision = tp / (tp + fp) if tp + fp else 0.0
		recall = tp / (tp + fn) if tp + fn else 0.0
		f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
		print(
			f"  {index}/{len(SEARCH_SPACE)} validation F1={f1:.4f} "
			f"RMSE={val_rmse:.4f} params={parameters}"
		)
		if f1 > best_val_f1 or (f1 == best_val_f1 and val_rmse < best_val_rmse):
			best_model = candidate
			best_parameters = parameters
			best_val_rmse = val_rmse
			best_val_f1 = f1
			best_val_precision = precision
			best_val_recall = recall
	model = best_model
	training_seconds = time.perf_counter() - start
	print(
		f"\n[5] Selected validation F1={best_val_f1:.4f} "
		f"precision={best_val_precision:.4f} recall={best_val_recall:.4f} "
		f"RMSE={best_val_rmse:.4f}"
	)
	print(f"  Selected parameters: {best_parameters}")
	print(f"  Search completed in {training_seconds:.2f} seconds")

	print("\n" + "=" * 70)
	print("MODEL EVALUATION ON TEST SET")
	print("=" * 70)
	print(f"\n[6] Predicting on test set ({len(test)} samples)...")
	start = time.perf_counter()
	predictions = model.predict(x_test)
	inference_seconds = time.perf_counter() - start
	metrics = {
		"Model": "XGBoost" if XGBOOST_AVAILABLE else "Gradient Boosting",
		"MAE": mean_absolute_error(y_test, predictions),
		"RMSE": mean_squared_error(y_test, predictions) ** 0.5,
		"R2": r2_score(y_test, predictions),
		"Inference_Time_ms": inference_seconds * 1000 / len(test),
	}
	print(f"  MAE:              {metrics['MAE']:.4f} %")
	print(f"  RMSE:             {metrics['RMSE']:.4f} %")
	print(f"  R2:               {metrics['R2']:.4f}")
	print(f"  Inference time:   {metrics['Inference_Time_ms']:.4f} ms/sample")

	print("\n[7] Top 10 features by importance:")
	for rank, (feature, importance) in enumerate(
		sorted(zip(FEATURES, model.feature_importances_), key=lambda item: item[1], reverse=True)[:10], 1
	):
		print(f"  {rank:2}. {feature:<20} {importance:.4f}")

	os.makedirs("models", exist_ok=True)
	os.makedirs("results", exist_ok=True)

	import joblib
	if XGBOOST_AVAILABLE:
		model.save_model(MODEL_PATH_XGB)
		save_target_path = MODEL_PATH_XGB
	else:
		joblib.dump(model, MODEL_PATH_GBDT)
		save_target_path = MODEL_PATH_GBDT

	with open(FEATURES_PATH, "w", encoding="utf-8") as handle:
		json.dump({
			"features": FEATURES,
			"target": TARGET,
			"horizon_seconds": 5,
			"target_alignment": "exact_timestamp",
			"artifact_status": "current",
			"engine": "xgboost" if XGBOOST_AVAILABLE else "sklearn_gbdt",
			"selected_parameters": best_parameters,
			"validation_rmse": best_val_rmse,
			"validation_congestion_precision": best_val_precision,
			"validation_congestion_recall": best_val_recall,
			"validation_congestion_f1": best_val_f1,
			"selection_metric": "validation_congestion_f1_then_rmse",
		}, handle, indent=2)

	existing = pd.read_csv(METRICS_PATH) if os.path.exists(METRICS_PATH) else pd.DataFrame()
	if not existing.empty and "Model" in existing.columns:
		existing = existing[~existing["Model"].str.lower().isin(["xgboost", "gradient boosting"])]
	comparison = pd.concat([existing, pd.DataFrame([metrics])], ignore_index=True)
	comparison.to_csv(METRICS_PATH, index=False)

	print("\n[8] Saved deliverables:")
	print(f"  {save_target_path}")
	print(f"  {FEATURES_PATH}")
	print(f"  {METRICS_PATH}")
	print("\nPHASE G COMPLETE")


if __name__ == "__main__":
	main()
