import csv
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml/src"))
from confidence import ConfidenceEstimator

spec = importlib.util.spec_from_file_location("calibrate_confidence", ROOT / "experiments/calibrate_confidence.py")
calibration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(calibration)


class ConfidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.estimator = ConfidenceEstimator()
        with open(ROOT / "cnt-ml/data/real/calibration-02/prepared/train.csv", newline="", encoding="utf-8") as handle:
            cls.row = next(csv.DictReader(handle))

    def test_reproducible_validation_only_calibration(self):
        expected = json.loads((ROOT / "cnt-ml/models/gbdt_confidence.json").read_text())
        rebuilt = calibration.build_artifact()
        self.assertEqual(rebuilt, expected)
        self.assertEqual(expected["calibration_rows"], 204)
        self.assertEqual(expected["quantile_rank"], 185)
        self.assertAlmostEqual(expected["radius_percentage_points"], 25.172974455995735)
        self.assertTrue(expected["validation_reused_for_model_selection"])
        self.assertFalse(expected["coverage_guarantee"])
        self.assertFalse(expected["test_labels_used"])
        self.assertFalse(expected["model_refit"])

    def test_current_data_cannot_support_proactive_85_percent_gate(self):
        interval = self.estimator.interval(100, self.row)
        self.assertTrue(interval["in_domain"])
        self.assertFalse(interval["confidence_gate_pass"])
        self.assertEqual(interval["gate_reason"], "lower_bound_below_threshold")
        self.assertAlmostEqual(interval["lower"], 74.82702554400426)
        self.assertEqual(interval["upper"], 100)
        self.assertAlmostEqual(interval["width"], 100 - interval["lower"])
        self.assertTrue(interval["exploratory"])
        self.assertEqual(interval["nominal_coverage"], 0.9)

    def test_domain_uses_tree_features_and_not_unused_loss_or_flow_count(self):
        row = dict(self.row, active_flows=20, packet_loss=50)
        self.assertEqual(set(self.estimator.ignored_features), {"active_flows", "packet_loss", "util_lag_5"})
        self.assertTrue(self.estimator.interval(40, row)["in_domain"])
        row["throughput"] = self.estimator.bounds["throughput"]["max"] + 1
        interval = self.estimator.interval(40, row, threshold=0)
        self.assertFalse(interval["in_domain"])
        self.assertFalse(interval["confidence_gate_pass"])
        self.assertEqual(interval["out_of_domain_features"], ["throughput"])
        self.assertEqual(interval["gate_reason"], "outside_training_feature_ranges")

    def test_missing_or_invalid_inputs_fail_closed(self):
        row = dict(self.row)
        del row["delay"]
        interval = self.estimator.interval(90, row)
        self.assertEqual((interval["lower"], interval["upper"], interval["width"]), (0, 100, 100))
        self.assertEqual(interval["gate_reason"], "missing_features")
        self.assertEqual(interval["missing_features"], ["delay"])
        row["delay"] = float("nan")
        interval = self.estimator.interval(90, row)
        self.assertEqual(interval["gate_reason"], "nonfinite_features")
        self.assertFalse(interval["confidence_gate_pass"])
        for value in (float("nan"), float("inf")):
            with self.assertRaisesRegex(ValueError, "finite"):
                self.estimator.interval(value, self.row)

    def test_stale_weight_schema_and_split_hashes_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "changed"
            changed.write_bytes(b"changed")
            for argument, label in (("model_path", "portable_model"),
                                    ("source_model_path", "source_model"),
                                    ("schema_path", "feature_schema")):
                with self.assertRaisesRegex(ValueError, label + " hash mismatch"):
                    ConfidenceEstimator(**{argument: changed})
            for name in ("train", "val", "test"):
                shutil.copyfile(ROOT / ("cnt-ml/data/real/calibration-02/prepared/" + name + ".csv"),
                                Path(directory) / (name + ".csv"))
            (Path(directory) / "val.csv").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "val split hash mismatch"):
                ConfidenceEstimator(split_directory=directory)
            estimator = ConfidenceEstimator(split_directory=directory, verify_split_hashes=False)
            self.assertFalse(estimator.metadata()["split_hashes_verified"])

    def test_test_file_is_opaque_hash_and_never_calibration_input(self):
        expected = calibration.build_artifact()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for binding in expected["bindings"].values():
                path = root / binding["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / binding["path"], path)
            # A file with no parseable labels remains hashable. Calibration must
            # produce exactly the same interval and training support bounds.
            test_path = root / expected["bindings"]["test_split"]["path"]
            test_path.write_bytes(b"opaque test content, not a CSV")
            actual = calibration.build_artifact(root)
            self.assertEqual(actual["radius_percentage_points"], expected["radius_percentage_points"])
            self.assertEqual(actual["training_feature_ranges"], expected["training_feature_ranges"])
            self.assertNotEqual(actual["bindings"]["test_split"]["sha256"], expected["bindings"]["test_split"]["sha256"])
            self.assertFalse(actual["test_labels_used"])

    def test_quantile_rank_has_no_interpolation_or_optimistic_small_sample_cap(self):
        radius, rank = calibration.ordered_quantile(list(range(10)), 0.8)
        self.assertEqual((radius, rank), (8, 9))
        with self.assertRaisesRegex(ValueError, "Too few"):
            calibration.ordered_quantile([1, 2], 0.9)


if __name__ == "__main__":
    unittest.main()
