import json
import os
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
import joblib

TEST_PATH = "data/splits/test.csv"
TRAIN_PATH = "data/splits/train.csv"
RESULTS_PATH = "results/metrics_comparison.csv"
CONGESTION_RESULTS_PATH = "results/congestion_metrics.csv"
MODEL_PATH_XGB = "models/xgb_link_predictor.json"
MODEL_PATH_GBDT = "models/xgb_link_predictor.joblib"
FEATURES_PATH = "models/feature_list.json"
PLOTS_DIR = "results/plots"


def ensure_plots_dir():
	os.makedirs(PLOTS_DIR, exist_ok=True)


def compute_error_by_load(y_true, y_pred, utilization, bin_edges=(0, 30, 60, 85, 100)):
	results = []
	for index, (lower, upper) in enumerate(zip(bin_edges[:-1], bin_edges[1:])):
		if index == len(bin_edges) - 2:
			mask = (utilization >= lower) & (utilization <= upper)
		else:
			mask = (utilization >= lower) & (utilization < upper)
		if mask.sum() == 0:
			continue
		results.append({
			"Load_Range": f"{lower}-{upper}%",
			"Samples": int(mask.sum()),
			"MAE": mean_absolute_error(y_true[mask], y_pred[mask]),
			"RMSE": mean_squared_error(y_true[mask], y_pred[mask]) ** 0.5,
			"R2": r2_score(y_true[mask], y_pred[mask]) if mask.sum() > 1 else np.nan,
		})
	return pd.DataFrame(results)


def metrics_for_predictions(y_true, predictions, inference_ms):
	return {
		"MAE": mean_absolute_error(y_true, predictions),
		"RMSE": mean_squared_error(y_true, predictions) ** 0.5,
		"R2": r2_score(y_true, predictions),
		"Inference_Time_ms": inference_ms,
	}


def congestion_metrics(y_true, predictions, threshold=85.0):
	actual = y_true >= threshold
	predicted = predictions >= threshold
	tp = int((actual & predicted).sum())
	fp = int((~actual & predicted).sum())
	fn = int((actual & ~predicted).sum())
	tn = int((~actual & ~predicted).sum())
	precision = tp / (tp + fp) if tp + fp else np.nan
	recall = tp / (tp + fn) if tp + fn else np.nan
	f1 = 2 * precision * recall / (precision + recall) if precision + recall else np.nan
	return {
		"Threshold": threshold,
		"TP": tp,
		"FP": fp,
		"FN": fn,
		"TN": tn,
		"Precision": precision,
		"Recall": recall,
		"F1": f1,
	}


def save_plots(y_test, predictions, load_tables):
	labels = list(predictions)
	colors = ["steelblue", "seagreen", "indianred"]
	sample_index = np.arange(len(y_test))

	fig, ax = plt.subplots(figsize=(14, 6))
	ax.plot(sample_index, y_test, "k-", label="Actual", alpha=0.7)
	for label, color in zip(labels, colors[:len(labels)]):
		ax.plot(sample_index, predictions[label], "--", label=label, alpha=0.75)
	ax.set(xlabel="Sample Index (Time)", ylabel="Link Utilization (%)", title="Model Predictions vs Actual Utilization")
	ax.legend()
	ax.grid(alpha=0.3)
	fig.tight_layout()
	fig.savefig(os.path.join(PLOTS_DIR, "01_predictions_vs_actual.png"), dpi=150)
	plt.close(fig)

	fig, axes = plt.subplots(1, len(labels), figsize=(5 * len(labels), 4))
	if len(labels) == 1:
		axes = [axes]
	for axis, label, color in zip(axes, labels, colors[:len(labels)]):
		errors = np.abs(y_test - predictions[label])
		axis.hist(errors, bins=30, color=color, alpha=0.7, edgecolor="black")
		axis.set(xlabel="Absolute Error (%)", ylabel="Frequency", title=f"{label}\nMAE: {mean_absolute_error(y_test, predictions[label]):.4f}%")
		axis.grid(alpha=0.3, axis="y")
	fig.tight_layout()
	fig.savefig(os.path.join(PLOTS_DIR, "02_error_distribution.png"), dpi=150)
	plt.close(fig)

	fig, axes = plt.subplots(1, len(labels), figsize=(5 * len(labels), 4))
	if len(labels) == 1:
		axes = [axes]
	for axis, label, color in zip(axes, labels, colors[:len(labels)]):
		axis.scatter(y_test, y_test - predictions[label], alpha=0.5, s=20, color=color)
		axis.axhline(0, color="black", linestyle="--")
		axis.set(xlabel="Actual Utilization (%)", ylabel="Residual (Actual - Predicted)", title=label)
		axis.grid(alpha=0.3)
	fig.tight_layout()
	fig.savefig(os.path.join(PLOTS_DIR, "03_residuals_vs_actual.png"), dpi=150)
	plt.close(fig)

	load_ranges = load_tables[labels[0]]["Load_Range"]
	x = np.arange(len(load_ranges))
	width = 0.25
	fig, ax = plt.subplots(figsize=(10, 6))
	offsets = np.linspace(-width, width, len(labels))
	for offset, label, color in zip(offsets, labels, colors[:len(labels)]):
		ax.bar(x + offset, load_tables[label]["MAE"], width, label=label, color=color, alpha=0.8)
	ax.set(xlabel="Link Utilization Range", ylabel="Mean Absolute Error (%)", title="Model Performance by Load Level")
	ax.set_xticks(x, load_ranges)
	ax.legend()
	ax.grid(alpha=0.3, axis="y")
	fig.tight_layout()
	fig.savefig(os.path.join(PLOTS_DIR, "04_mae_by_load_level.png"), dpi=150)
	plt.close(fig)

	fig, ax = plt.subplots(figsize=(10, 6))
	ax.boxplot([np.abs(y_test - predictions[label]) for label in labels], tick_labels=labels, showmeans=True, patch_artist=True)
	ax.set(ylabel="Absolute Error (%)", title="Error Distribution Comparison")
	ax.grid(alpha=0.3, axis="y")
	fig.tight_layout()
	fig.savefig(os.path.join(PLOTS_DIR, "05_error_boxplot.png"), dpi=150)
	plt.close(fig)

	window_size = 100
	step = window_size // 2
	starts = range(0, max(len(y_test) - window_size + 1, 1), step)
	fig, ax = plt.subplots(figsize=(12, 6))
	for label, marker in zip(labels, ("o", "s", "^")):
		values = [mean_absolute_error(y_test[start:start + window_size], predictions[label][start:start + window_size]) for start in starts]
		ax.plot(np.arange(len(values)), values, marker + "-", label=label)
	ax.set(xlabel="Time Window", ylabel="MAE in Window (%)", title=f"Model Stability Over Time (Window size: {window_size})")
	ax.legend()
	ax.grid(alpha=0.3)
	fig.tight_layout()
	fig.savefig(os.path.join(PLOTS_DIR, "06_mae_over_time.png"), dpi=150)
	plt.close(fig)


def main():
	print("=" * 70)
	print("PHASE H - COMPREHENSIVE EVALUATION & VISUALIZATION")
	print("=" * 70)
	ensure_plots_dir()

	test = pd.read_csv(TEST_PATH)
	train = pd.read_csv(TRAIN_PATH)
	feature_dict = json.load(open(FEATURES_PATH, encoding="utf-8"))
	feature_cols = feature_dict["features"]
	required = set(feature_cols + ["target_util", "utilization"])
	missing = required - set(test.columns) | required - set(train.columns)
	if missing:
		raise ValueError(f"Missing required columns: {sorted(missing)}")

	x_test = test[feature_cols]
	y_test = test["target_util"].to_numpy()
	current_util = test["utilization"].to_numpy()
	predictions = {}
	inference = {}

	start = time.perf_counter()
	predictions["Persistence"] = current_util
	inference["Persistence"] = (time.perf_counter() - start) * 1000 / len(test)

	linear_model = LinearRegression().fit(train[feature_cols], train["target_util"])
	start = time.perf_counter()
	predictions["Linear Regression"] = linear_model.predict(x_test)
	inference["Linear Regression"] = (time.perf_counter() - start) * 1000 / len(test)

	gbdt_label = "Gradient Boosting"
	model = None
	engine = feature_dict.get("engine", "sklearn_gbdt")
	if engine == "xgboost" and os.path.exists(MODEL_PATH_XGB):
		try:
			import xgboost as xgb
			model = xgb.XGBRegressor()
			model.load_model(MODEL_PATH_XGB)
		except Exception:
			model = None

	if engine == "sklearn_gbdt" and os.path.exists(MODEL_PATH_GBDT):
		model = joblib.load(MODEL_PATH_GBDT)

	if model is None:
		raise FileNotFoundError("No trained XGBoost/GBDT model found in models/")

	start = time.perf_counter()
	predictions[gbdt_label] = model.predict(x_test)
	inference[gbdt_label] = (time.perf_counter() - start) * 1000 / len(test)

	overall = []
	load_tables = {}
	for label, prediction in predictions.items():
		result = metrics_for_predictions(y_test, prediction, inference[label])
		overall.append({"Model": label, **result})
		load_tables[label] = compute_error_by_load(y_test, prediction, y_test)
		print(f"\n{label}: MAE {result['MAE']:.4f}% | RMSE {result['RMSE']:.4f}% | R2 {result['R2']:.4f}")

	print("\nERROR ANALYSIS BY LOAD LEVEL")
	for label, table in load_tables.items():
		print(f"\n{label}:\n{table.to_string(index=False)}")

	high_load = y_test >= 85
	print(f"\nCRITICAL: Future High-Load Performance (target utilization >= 85%)")
	print(f"{high_load.sum()} samples at high load")
	congestion_rows = []
	for label, prediction in predictions.items():
		if high_load.sum():
			print(f"  {label:<20} MAE at high load: {mean_absolute_error(y_test[high_load], prediction[high_load]):.4f}%")
		classification = congestion_metrics(y_test, prediction)
		congestion_rows.append({"Model": label, **classification})
		print(
			f"  {label:<20} congestion precision={classification['Precision']:.4f} "
			f"recall={classification['Recall']:.4f} F1={classification['F1']:.4f}"
		)

	save_plots(y_test, predictions, load_tables)
	pd.DataFrame(overall).to_csv(RESULTS_PATH, index=False)
	pd.DataFrame(congestion_rows).to_csv(CONGESTION_RESULTS_PATH, index=False)
	print(f"\nSaved metrics -> {RESULTS_PATH}")
	print(f"Saved congestion metrics -> {CONGESTION_RESULTS_PATH}")
	print(f"Generated plots -> {PLOTS_DIR}")
	print("\nPHASE H COMPLETE")


if __name__ == "__main__":
	main()
