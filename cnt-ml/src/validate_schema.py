"""Validate the CSV schema expected from the Ryu telemetry collector."""
import os
import sys

import numpy as np
import pandas as pd

INPUT_PATH = "data/raw/telemetry_raw.csv"
REQUIRED_COLUMNS = [
    "timestamp",
    "link_id",
    "utilization",
    "throughput",
    "delay",
    "packet_loss",
    "queue_occupancy",
    "active_flows",
]
NUMERIC_COLUMNS = [
    "timestamp",
    "utilization",
    "throughput",
    "delay",
    "packet_loss",
    "queue_occupancy",
    "active_flows",
]


def main():
    print("=" * 70)
    print("PHASE J - REAL TELEMETRY SCHEMA VALIDATION")
    print("=" * 70)

    if not os.path.exists(INPUT_PATH):
        print(f"FAIL: input file does not exist: {INPUT_PATH}")
        return 1

    try:
        df = pd.read_csv(INPUT_PATH)
    except Exception as error:
        print(f"FAIL: could not read {INPUT_PATH}: {error}")
        return 1

    print(f"Loaded {len(df):,} rows from {INPUT_PATH}")
    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        print(f"FAIL: missing required columns: {missing}")
        return 1
    print("PASS: all 8 required columns are present")

    if df.empty:
        print("FAIL: dataset is empty")
        return 1

    failures = []
    for column in NUMERIC_COLUMNS:
        converted = pd.to_numeric(df[column], errors="coerce")
        invalid = int(converted.isna().sum())
        if invalid:
            failures.append(f"{column}: {invalid} non-numeric or missing value(s)")
        else:
            df[column] = converted
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: numeric columns contain valid numeric values")

    range_checks = {
        "utilization": (0, 100),
        "packet_loss": (0, 100),
        "queue_occupancy": (0, 100),
        "throughput": (0, np.inf),
        "delay": (0, np.inf),
        "active_flows": (0, np.inf),
    }
    for column, (lower, upper) in range_checks.items():
        invalid = int(((df[column] < lower) | (df[column] > upper)).sum())
        if invalid:
            print(f"FAIL: {column} has {invalid} value(s) outside [{lower}, {upper}]")
            return 1
    print("PASS: all telemetry values are within valid ranges")

    if df["link_id"].isna().any() or (df["link_id"].astype(str).str.strip() == "").any():
        print("FAIL: link_id contains missing or empty values")
        return 1
    if df.duplicated(["timestamp", "link_id"]).any():
        print("WARN: duplicate timestamp/link_id rows found; Phase B will remove them")
    print(f"Links found: {df['link_id'].nunique()}")

    ordered = True
    intervals = []
    for link_id, group in df.groupby("link_id"):
        timestamps = group["timestamp"].sort_values()
        if not timestamps.is_monotonic_increasing:
            ordered = False
        differences = timestamps.diff().dropna()
        intervals.extend(differences.tolist())
    if ordered:
        print("PASS: timestamps are ordered within each link")
    else:
        print("WARN: timestamps are not ordered within every link; Phase B will sort them")

    if intervals:
        interval_series = pd.Series(intervals)
        one_second = int((interval_series == 1).sum())
        print(f"Sampling interval: median={interval_series.median():g}s, 1-second intervals={one_second}/{len(intervals)}")
        if not np.isclose(interval_series.median(), 1):
            print("WARN: median sampling interval is not 1 second")
    else:
        print("WARN: not enough samples to calculate sampling interval")

    print(f"Utilization range: {df['utilization'].min():.2f}% to {df['utilization'].max():.2f}%")
    high_load = int((df["utilization"] >= 85).sum())
    print(f"High-utilization samples (>=85%): {high_load}")
    if high_load == 0:
        print("WARN: no high-utilization samples; congestion evaluation will be limited")
    if len(df) < 1000:
        print("WARN: fewer than 1,000 rows; more data is recommended")

    print("\nSchema validation complete: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
