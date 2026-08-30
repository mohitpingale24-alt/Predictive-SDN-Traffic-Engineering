"""Prediction integration contract for the networking/Ryu controller."""

import json
import os
from typing import Dict
import numpy as np

ACTIVE_MODEL = "persistence"
MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
FEATURES_PATH = os.path.join(MODELS_DIR, "feature_list.json")
XGB_MODEL_PATH_JSON = os.path.join(MODELS_DIR, "xgb_link_predictor.json")
XGB_MODEL_PATH_JOBLIB = os.path.join(MODELS_DIR, "xgb_link_predictor.joblib")
METRICS_PATH = os.path.join(os.path.dirname(__file__), "..", "results", "metrics_comparison.csv")

_feature_dict = None
_xgb_model = None


def _load_features():
	global _feature_dict
	if _feature_dict is None:
		with open(FEATURES_PATH, encoding="utf-8") as handle:
			_feature_dict = json.load(handle)
	return _feature_dict


def _load_xgb_model():
	global _xgb_model
	if _xgb_model is None:
		if os.path.exists(XGB_MODEL_PATH_JSON):
			import xgboost as xgb
			_xgb_model = xgb.XGBRegressor()
			_xgb_model.load_model(XGB_MODEL_PATH_JSON)
		elif os.path.exists(XGB_MODEL_PATH_JOBLIB):
			import joblib
			_xgb_model = joblib.load(XGB_MODEL_PATH_JOBLIB)
		else:
			raise FileNotFoundError("No trained XGBoost/GBDT model file found in models/")
	return _xgb_model


def predict_future_utilization(recent_telemetry: Dict) -> float:
	"""Predict link utilization five seconds into the future."""
	if not isinstance(recent_telemetry, dict):
		raise ValueError("Input must be a dictionary")

	required_fields = [
		"timestamp", "link_id", "utilization", "throughput",
		"delay", "packet_loss", "queue_occupancy", "active_flows",
	]
	missing = [field for field in required_fields if field not in recent_telemetry]
	if missing:
		raise ValueError(f"Missing required fields: {missing}")

	utilization = float(recent_telemetry["utilization"])
	if not 0 <= utilization <= 100:
		raise ValueError(f"utilization must be 0-100%, got {utilization}")

	if ACTIVE_MODEL == "persistence":
		return utilization

	if ACTIVE_MODEL == "linear_regression":
		raise NotImplementedError(
			"Linear Regression is not integrated. Use persistence or xgboost."
		)

	if ACTIVE_MODEL in ["xgboost", "gradient_boosting"]:
		feature_cols = _load_features()["features"]
		missing_features = [
			feature for feature in feature_cols if feature not in recent_telemetry
		]
		if missing_features:
			raise ValueError(f"Missing model features: {missing_features}")
		feature_vector = [recent_telemetry[feature] for feature in feature_cols]
		prediction = _load_xgb_model().predict(np.array([feature_vector]))[0]
		return float(np.clip(prediction, 0, 100))

	raise ValueError(
		f"Unknown model: {ACTIVE_MODEL}. Options: persistence, linear_regression, xgboost"
	)


def get_model_info() -> Dict:
	"""Return metadata about the active model for controller logging."""
	feature_dict = _load_features()
	model_info = {
		"active_model": ACTIVE_MODEL,
		"model_mae": None,
		"model_rmse": None,
		"model_r2": None,
		"n_features": len(feature_dict["features"]),
		"features": feature_dict["features"],
		"prediction_horizon_seconds": feature_dict.get("horizon_seconds", 5),
		"sampling_interval_seconds": 1,
	}
	if os.path.exists(METRICS_PATH):
		import pandas as pd

		metrics = pd.read_csv(METRICS_PATH)
		row = metrics[metrics["Model"].str.lower() == ACTIVE_MODEL]
		if not row.empty:
			model_info.update({
				"model_mae": float(row.iloc[0]["MAE"]),
				"model_rmse": float(row.iloc[0]["RMSE"]),
				"model_r2": float(row.iloc[0]["R2"]),
				"inference_time_ms": float(row.iloc[0]["Inference_Time_ms"]),
			})
	model_info.setdefault("inference_time_ms", None)
	return model_info


if __name__ == "__main__":
	telemetry = {
		"timestamp": 1234567890,
		"link_id": "S1-S2",
		"utilization": 20.0,
		"throughput": 2.0,
		"delay": 3.5,
		"packet_loss": 0.0,
		"queue_occupancy": 5.0,
		"active_flows": 3,
	}
	print(f"Active model: {ACTIVE_MODEL}")
	print(f"Model info: {get_model_info()}")
	print(f"20.0% utilization -> {predict_future_utilization(telemetry):.2f}% prediction")

	try:
		predict_future_utilization({"utilization": 45.0})
	except ValueError as error:
		print(f"Missing-field validation passed: {error}")

	invalid = telemetry.copy()
	invalid["utilization"] = 150.0
	try:
		predict_future_utilization(invalid)
	except ValueError as error:
		print(f"Range validation passed: {error}")
