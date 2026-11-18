"""Print a compact read-only campaign status, verifying finished captures."""
import argparse
from collections import Counter
import json
from pathlib import Path
from quality_gate import check_run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    args = parser.parse_args()
    counts = Counter()
    errors = []
    for path in sorted(args.campaign.glob("*/run-*/metadata.json")):
        try:
            metadata = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue  # The runner may be atomically finishing this record.
        status = metadata.get("status", "running")
        if status == "complete":
            try:
                check_run(path.parent, metadata)
                status = "verified"
            except (ValueError, OSError, KeyError) as error:
                status = "invalid"
                errors.append(f"{path.parent}: {error}")
        counts[(metadata["system"], status)] += 1
    for system in ("static", "reactive", "predictive-baseline", "predictive-ml"):
        fields = [f"{counts[(system, state)]} {state}" for state in ("verified", "running", "failed", "invalid") if counts[(system, state)]]
        print(f"{system}: {', '.join(fields) if fields else 'waiting'}")
    for error in errors:
        print(error)
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
