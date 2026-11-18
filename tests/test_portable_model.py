import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("portable_gbdt", ROOT / "cnt-ml/src/portable_gbdt.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PortableModelTests(unittest.TestCase):
    def setUp(self):
        self.source = ROOT / "cnt-ml/models/xgb_link_predictor.joblib"
        self.portable = ROOT / "cnt-ml/models/gbdt_portable.json"
        self.features = json.loads((ROOT / "cnt-ml/models/feature_list.json").read_text())["features"]

    def test_frozen_fixture_parity(self):
        fixture = json.loads((ROOT / "cnt-ml/models/gbdt_parity_fixture.json").read_text())
        model = module.PortableGBDT(self.portable, self.source, self.features)
        np.testing.assert_allclose(model.predict(fixture["inputs"]), fixture["expected"], rtol=0, atol=1e-10)

    def test_threshold_neighbors_match_sklearn(self):
        original = joblib.load(self.source)
        portable = module.PortableGBDT(self.portable, self.source, self.features)
        rows = []
        for estimator in original.estimators_[:, 0]:
            for feature, threshold in zip(estimator.tree_.feature, estimator.tree_.threshold):
                if feature < 0:
                    continue
                for value in (np.float32(threshold), np.nextafter(np.float32(threshold), np.float32(-np.inf)), np.nextafter(np.float32(threshold), np.float32(np.inf))):
                    row = [50.] * len(self.features)
                    row[feature] = float(value)
                    rows.append(row)
        frame = pd.DataFrame(rows, columns=self.features)
        np.testing.assert_allclose(portable.predict(frame), original.predict(frame), rtol=0, atol=1e-10)

    def test_stale_model_and_schema_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "changed.joblib"
            source.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "stale"):
                module.PortableGBDT(self.portable, source, self.features)
        with self.assertRaisesRegex(ValueError, "contract"):
            module.PortableGBDT(self.portable, self.source, list(reversed(self.features)))

    def test_unused_loss_and_flow_features_cannot_affect_output(self):
        portable = module.PortableGBDT(self.portable, self.source, self.features)
        used = {tree["feature"][i] for tree in portable.data["trees"] for i in range(len(tree["feature"])) if tree["left"][i] != -1}
        self.assertNotIn(self.features.index("packet_loss"), used)
        self.assertNotIn(self.features.index("active_flows"), used)
