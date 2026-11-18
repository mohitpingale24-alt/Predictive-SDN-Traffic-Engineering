"""Export existing weights, then prove parity without refitting or tuning."""
import hashlib
import json
from pathlib import Path
import sys
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml/src"))
from portable_gbdt import PortableGBDT


def main():
    directory = ROOT / "cnt-ml/models"
    source = directory / "xgb_link_predictor.joblib"
    model = joblib.load(source)
    features = json.loads((directory / "feature_list.json").read_text())["features"]
    assert model.loss == "squared_error" and hasattr(model.init_, "constant_")
    trees = []
    for estimator in model.estimators_[:, 0]:
        tree = estimator.tree_
        trees.append(dict(left=tree.children_left.tolist(), right=tree.children_right.tolist(),
                          feature=tree.feature.tolist(), threshold=tree.threshold.tolist(),
                          value=tree.value[:, 0, 0].tolist()))
    payload = dict(schema=1, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                   features=features, initial_prediction=float(model.init_.constant_[0, 0]),
                   learning_rate=model.learning_rate, trees=trees)
    destination = directory / "gbdt_portable.json"
    destination.write_text(json.dumps(payload, indent=2) + "\n")
    portable = PortableGBDT(destination, source, features)
    data = pd.concat([pd.read_csv(ROOT / f"cnt-ml/data/splits/{split}.csv") for split in ("train", "val", "test")])
    random = pd.DataFrame(np.random.default_rng(42).uniform(-100, 200, (1000, len(features))), columns=features)
    inputs = pd.concat([data[features], random], ignore_index=True)
    expected = model.predict(inputs)
    actual = portable.predict(inputs)
    error = float(np.max(np.abs(expected - actual)))
    assert error < 1e-10, error
    fixture = dict(features=features, inputs=inputs.iloc[::50].values.tolist(), expected=expected[::50].tolist(),
                   host_parity_rows=len(inputs), maximum_absolute_error=error,
                   feature_importances=dict(zip(features, model.feature_importances_.tolist())))
    (directory / "gbdt_parity_fixture.json").write_text(json.dumps(fixture, indent=2) + "\n")
    print(json.dumps({"parity_rows": len(inputs), "max_error": error, "source_sha256": payload["source_sha256"]}))


if __name__ == "__main__":
    main()
