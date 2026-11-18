"""Exercise real runtime inference and frozen-weight parity before networking."""
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml/src"))
from predict import predict_future_utilization, get_model_info


def main():
    fixture = json.loads((ROOT / "cnt-ml/models/gbdt_parity_fixture.json").read_text())
    info = get_model_info()
    errors = []
    for inputs, expected in zip(fixture["inputs"], fixture["expected"]):
        row = dict(zip(fixture["features"], inputs))
        if not 0 <= row["utilization"] <= 100:
            continue
        row.update(timestamp=0, link_id="S1-S2")
        actual = predict_future_utilization(row)
        assert math.isfinite(actual)
        if info["active_model"] == "gradient_boosting":
            errors.append(abs(actual - min(100, max(0, expected))))
    if info["active_model"] == "gradient_boosting":
        assert errors and max(errors) < 1e-9, errors
    print(json.dumps(dict(status="PASS", model=info["active_model"], parity_cases=len(errors),
                         max_error=max(errors) if errors else None)))


if __name__ == "__main__":
    main()
