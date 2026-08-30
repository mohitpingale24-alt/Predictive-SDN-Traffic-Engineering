import json
import os
import time
import pandas as pd
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

XGBOOST_AVAILABLE = False
try:
	from xgboost import XGBRegressor
	# Test if C API / DLL loads successfully without AppLocker error
	_test_model = XGBRegressor(n_estimators=1)
	_test_model.fit([[0]], [0])
	XGBOOST_AVAILABLE = True
except Exception:
	from sklearn.ensemble import GradientBoostingRegressor
	XGBOOST_AVAILABLE = False

TRAIN_PATH = "data/splits/train.csv"
VAL_PATH = "data/splits/val.csv"
TEST_PATH = "data/splits/test.csv"
MODEL_PATH_XGB = "models/xgb_link_predictor.json"
MODEL_PATH_GBDT = "models/xgb_link_predictor.joblib"
FEATURES_PATH = "models/feature_list.json"
METRICS_PATH = "results/metrics_comparison.csv"
TARGET = "target_util"

FEATURES = [
	"utilization",
	"throughput",
	"delay",
	"packet_loss",
	"queue_occupancy",
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
		model = XGBRegressor(
			n_estimators=200,
			max_depth=5,
			learning_rate=0.1,
			subsample=0.8,
			colsample_bytree=0.8,
			objective="reg:squarederror",
			random_state=42,
			verbosity=0,
		)
	else:
		print("\n[Engine] Windows AppLocker policy blocked xgboost.dll.")
		print("[Fallback] Using scikit-learn GradientBoostingRegressor (100% GBDT compatible).")
		model = GradientBoostingRegressor(
			n_estimators=200,
			max_depth=5,
			learning_rate=0.1,
			subsample=0.8,
			random_state=42,
		)

	print("\n" + "=" * 70)
	print("MODEL TRAINING")
	print("=" * 70)
	print("\n[4] Hyperparameters:")
	print("  n_estimators: 200")
	print("  max_depth: 5")
	print("  learning_rate: 0.1")
	print("  subsample: 0.8")
	print("  random_state: 42")

	print("\n[5] Training Model...")
	start = time.perf_counter()
	if XGBOOST_AVAILABLE:
		model.fit(x_train, y_train, eval_set=[(x_val, y_val)], verbose=False)
	else:
		model.fit(x_train, y_train)
	training_seconds = time.perf_counter() - start
	print(f"  Model trained in {training_seconds:.2f} seconds")

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
			"engine": "xgboost" if XGBOOST_AVAILABLE else "sklearn_gbdt"
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
