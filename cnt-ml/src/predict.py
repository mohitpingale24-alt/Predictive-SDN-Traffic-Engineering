"""Prediction integration contract for the networking/Ryu controller."""

import json
import os
from typing import Dict
import numpy as np
try:
	from .portable_gbdt import PortableGBDT
except ImportError:
	from portable_gbdt import PortableGBDT

# Persistence remains the safe live default. SDN_ACTIVE_MODEL=auto resolves
# to the frozen validation-selected model (gradient_boosting) when its
# artifact exists — never to a ranking of the held-out test tables.
ACTIVE_MODEL = os.getenv("SDN_ACTIVE_MODEL", "persistence").strip().lower()
MODELS_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
FEATURES_PATH = os.path.join(MODELS_DIR, "feature_list.json")
LINEAR_MODEL_PATH = os.path.join(MODELS_DIR, "linear_link_predictor.joblib")
XGB_MODEL_PATH_JSON = os.path.join(MODELS_DIR, "xgb_link_predictor.json")
XGB_MODEL_PATH_JOBLIB = os.path.join(MODELS_DIR, "xgb_link_predictor.joblib")
METRICS_PATH = os.path.join(os.path.dirname(__file__), "..", "results", "metrics_comparison.csv")
CONGESTION_METRICS_PATH = os.path.join(os.path.dirname(__file__), "..", "results", "congestion_metrics.csv")
# Retained for provenance reporting only: model selection no longer
# reads the test-result tables (see resolve_active_model).
MODEL_SELECTION_METRIC = "frozen_validation_choice"

_feature_dict = None
_linear_model = None
_xgb_model = None
_resolved_model = None


def _load_features():
	global _feature_dict
	if _feature_dict is None:
		with open(FEATURES_PATH, encoding="utf-8") as handle:
			_feature_dict = json.load(handle)
	return _feature_dict


def _load_xgb_model(model_name=None):
	global _xgb_model
	if _xgb_model is None:
		engine = _load_features().get("engine")
		use_gbdt = model_name == "gradient_boosting" or (
			model_name is None and engine == "sklearn_gbdt"
		)
		if use_gbdt and os.path.exists(os.path.join(MODELS_DIR, "gbdt_portable.json")):
			_xgb_model = PortableGBDT(os.path.join(MODELS_DIR, "gbdt_portable.json"), XGB_MODEL_PATH_JOBLIB, _load_features()["features"])
		elif use_gbdt and os.path.exists(XGB_MODEL_PATH_JOBLIB):
			import joblib
			_xgb_model = joblib.load(XGB_MODEL_PATH_JOBLIB)
		elif os.path.exists(XGB_MODEL_PATH_JSON):
			import xgboost as xgb
			_xgb_model = xgb.XGBRegressor()
			_xgb_model.load_model(XGB_MODEL_PATH_JSON)
		elif os.path.exists(XGB_MODEL_PATH_JOBLIB):
			import joblib
			_xgb_model = joblib.load(XGB_MODEL_PATH_JOBLIB)
		else:
			raise FileNotFoundError("No trained XGBoost/GBDT model file found in models/")
	return _xgb_model


def _load_linear_model():
	global _linear_model
	if _linear_model is None:
		if not os.path.exists(LINEAR_MODEL_PATH):
			raise FileNotFoundError(
				"No saved Linear Regression model found. Run src/train_baselines.py."
			)
		import joblib
		_linear_model = joblib.load(LINEAR_MODEL_PATH)
	return _linear_model


def _artifact_available(model_name: str) -> bool:
	if model_name == "persistence":
		return True
	if _load_features().get("artifact_status") != "current":
		return False
	if model_name == "linear_regression":
		return os.path.exists(LINEAR_MODEL_PATH)
	if model_name == "xgboost":
		return _load_features().get("engine") == "xgboost" and os.path.exists(XGB_MODEL_PATH_JSON)
	if model_name == "gradient_boosting":
		return _load_features().get("engine") == "sklearn_gbdt" and os.path.exists(XGB_MODEL_PATH_JOBLIB)
	return False


def _canonical_model_name(metrics_name: str):
	return {
		"persistence": "persistence",
		"linear regression": "linear_regression",
		"xgboost": "xgboost",
		"gradient boosting": "gradient_boosting",
	}.get(str(metrics_name).strip().lower())


def resolve_active_model() -> str:
	"""Resolve an explicit model, or the frozen default under "auto".

	"auto" previously ranked candidate models by the committed
	metrics_comparison / congestion_metrics CSVs. Those tables are the
	held-out TEST comparison — choosing the production model by them is
	test-set selection, and the chosen model would change silently
	whenever the tables are regenerated. Model choice belongs to
	validation (the frozen campaign selected gradient_boosting on
	validation F1; see cnt-ml/models/feature_list.json). "auto" now
	resolves to that frozen choice when its artifact exists, and to
	persistence otherwise. Set SDN_ACTIVE_MODEL explicitly to run any
	other model.
	"""
	global _resolved_model
	if _resolved_model is not None:
		return _resolved_model

	valid = {"auto", "persistence", "linear_regression", "xgboost", "gradient_boosting"}
	if ACTIVE_MODEL not in valid:
		raise ValueError(f"Unknown model: {ACTIVE_MODEL}. Options: {sorted(valid)}")
	if ACTIVE_MODEL != "auto":
		if not _artifact_available(ACTIVE_MODEL):
			raise FileNotFoundError(
				f"Required current-schema artifact for {ACTIVE_MODEL} is not available; "
				"retrain only after the real-data quality gate passes"
			)
		_resolved_model = ACTIVE_MODEL
		return _resolved_model

	if _artifact_available("gradient_boosting"):
		_resolved_model = "gradient_boosting"
		return _resolved_model

	_resolved_model = "persistence"
	return _resolved_model


def predict_future_utilization(recent_telemetry: Dict) -> float:
	"""Predict link utilization five seconds into the future."""
	if not isinstance(recent_telemetry, dict):
		raise ValueError("Input must be a dictionary")

	required_fields = [
		"timestamp", "link_id", "utilization", "throughput",
		"delay", "packet_loss", "active_flows",
	]
	missing = [field for field in required_fields if field not in recent_telemetry]
	if missing:
		raise ValueError(f"Missing required fields: {missing}")

	utilization = float(recent_telemetry["utilization"])
	if not 0 <= utilization <= 100:
		raise ValueError(f"utilization must be 0-100%, got {utilization}")

	model_name = resolve_active_model()

	if model_name == "persistence":
		return utilization

	if model_name in ["linear_regression", "xgboost", "gradient_boosting"]:
		feature_cols = _load_features()["features"]
		missing_features = [
			feature for feature in feature_cols if feature not in recent_telemetry
		]
		if missing_features:
			raise ValueError(f"Missing model features: {missing_features}")
		import pandas as pd
		feature_vector = pd.DataFrame(
			[[float(recent_telemetry[feature]) for feature in feature_cols]],
			columns=feature_cols,
			dtype=float,
		)
		model = _load_linear_model() if model_name == "linear_regression" else _load_xgb_model(model_name)
		prediction = model.predict(feature_vector)[0]
		return float(np.clip(prediction, 0, 100))

	raise AssertionError(f"Unhandled resolved model: {model_name}")


def get_model_info() -> Dict:
	"""Return metadata about the active model for controller logging."""
	feature_dict = _load_features()
	model_name = resolve_active_model()
	model_info = {
		"configured_model": ACTIVE_MODEL,
		"active_model": model_name,
		"model_selection_metric": MODEL_SELECTION_METRIC,
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
		canonical = metrics["Model"].map(_canonical_model_name)
		row = metrics[canonical == model_name]
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
		"active_flows": 3,
	}
	for feature in _load_features()["features"]:
		if feature.startswith("util_lag_") or feature.startswith("util_ma_"):
			telemetry.setdefault(feature, telemetry["utilization"])
	telemetry.setdefault("util_slope", 0.0)
	print(f"Configured model: {ACTIVE_MODEL}")
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
