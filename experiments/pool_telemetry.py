"""Pool all-link telemetry captures into one GNN training file.

A single 90-second run yields ~90 aligned snapshots — far too few to
train anything. Pooling concatenates the per-run
``telemetry_all_links.csv`` files, shifting each run's timestamps by a
cumulative offset (with a gap) so runs stay chronological segments of
one series instead of colliding on the same wall-clock seconds.

Usage: python3 experiments/pool_telemetry.py CAPTURE_DIR [CAPTURE_DIR ...]
       [--out cnt-ml/data/processed/telemetry_pooled.csv]
Output rows keep the telemetry schema; train_gnn.py consumes the file
directly. The active_flows column is carried through as recorded —
in all-links captures it is 0, meaning not measured (the field counts
flow-table entries on s1 in the legacy telemetry, never application
flows).
"""

import argparse
import csv
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FIELDS = ["timestamp", "link_id", "utilization", "throughput", "delay",
          "packet_loss", "active_flows"]
GAP_SECONDS = 10


def pool_rows(capture_dirs, filename="telemetry_all_links.csv"):
    """Return (rows, per-run counts) with timestamps offset per run."""
    pooled = []
    counts = {}
    offset = 0
    last_stamp = None
    for directory in capture_dirs:
        path = os.path.join(directory, filename)
        if not os.path.exists(path):
            counts[directory] = 0  # pre-all-link capture; nothing to pool
            continue
        with open(path, newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            counts[directory] = 0
            continue
        stamps = [int(float(row["timestamp"])) for row in rows]
        shift = 0 if last_stamp is None else (last_stamp + GAP_SECONDS) - min(stamps)
        for row in rows:
            pooled.append({field: (int(float(row["timestamp"])) + shift
                                   if field == "timestamp" else row[field])
                           for field in FIELDS})
        last_stamp = max(stamps) + shift
        counts[directory] = len(rows)
    pooled.sort(key=lambda row: row["timestamp"])
    return pooled, counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="+")
    parser.add_argument("--out", default=None)
    parser.add_argument("--filename", default="telemetry_all_links.csv")
    args = parser.parse_args(argv)
    out = args.out
    if out is None:
        out = os.path.join(PROJECT_ROOT, "cnt-ml", "data", "processed",
                           "telemetry_pooled.csv")
    rows, counts = pool_rows(args.captures, args.filename)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print("pooled %d rows from %d captures -> %s"
          % (len(rows), len(args.captures), out))
    for directory, count in counts.items():
        print("  %s: %d rows" % (directory, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
