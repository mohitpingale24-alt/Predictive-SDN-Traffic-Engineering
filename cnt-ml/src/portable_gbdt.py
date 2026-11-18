"""Version-independent inference for the frozen squared-error sklearn GBDT."""
import hashlib
import json
import numpy as np


class PortableGBDT:
    def __init__(self, path, source_path, features):
        with open(path, encoding="utf-8") as handle:
            self.data = json.load(handle)
        with open(source_path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        if self.data["source_sha256"] != digest:
            raise ValueError("Portable model is stale: re-export the frozen model")
        if self.data["schema"] != 1 or self.data["features"] != features:
            raise ValueError("Portable model feature contract mismatch")

    def predict(self, values):
        values = np.asarray(values, dtype=np.float32)
        if values.ndim != 2 or values.shape[1] != len(self.data["features"]) or not np.isfinite(values).all():
            raise ValueError("Expected finite two-dimensional model inputs")
        results = []
        for row in values:
            result = self.data["initial_prediction"]
            for tree in self.data["trees"]:
                node = 0
                while tree["left"][node] != -1:
                    # sklearn compares float32 inputs to float64 thresholds.
                    # Explicit float avoids NumPy-version-dependent promotion.
                    node = tree["left"][node] if float(row[tree["feature"][node]]) <= tree["threshold"][node] else tree["right"][node]
                result += self.data["learning_rate"] * tree["value"][node]
            results.append(result)
        return np.asarray(results)
