import unittest

from confidence_policy import select_signals


def interval(lower, upper, *, gate=False, domain=True):
    return {"lower": lower, "upper": upper,
            "confidence_gate_pass": gate, "in_domain": domain}


class ConfidencePolicyTests(unittest.TestCase):
    def setUp(self):
        self.capacity = {"A": 10, "B": 20}
        self.observed = {"A": 84, "B": 0}
        self.points = {"A": 92, "B": 10}
        self.intervals = {"A": interval(88, 96, gate=True),
                          "B": interval(4, 15)}

    def test_uses_lower_evidence_and_upper_capacity_projection(self):
        result = select_signals("A", self.observed, self.points, self.intervals, self.capacity)
        self.assertEqual(result["source"], "learned_confidence")
        self.assertEqual(result["signals"], {"A": 88, "B": 63})
        self.assertTrue(result["alternative_safe"])
        # Neither the 92% active point nor 56% point-projected alternative is
        # silently substituted for interval endpoints.
        self.assertNotEqual(result["signals"]["A"], self.points["A"])
        self.assertNotEqual(result["signals"]["B"], self.points["B"] + self.points["A"] / 2)

    def test_broad_interval_falls_back_to_measured_not_forecast_point(self):
        intervals = dict(self.intervals, A=interval(66.83, 100, gate=False))
        result = select_signals("A", self.observed, self.points, intervals, self.capacity)
        self.assertEqual(result["source"], "measured_fallback")
        self.assertEqual(result["reason"], "confidence_gate_closed")
        self.assertEqual(result["signals"]["A"], 84)
        self.assertAlmostEqual(result["signals"]["B"], 42)
        self.assertLess(result["signals"]["A"], 85)

    def test_unsafe_upper_bound_blocks_even_when_point_or_measured_is_safe(self):
        intervals = dict(self.intervals, B=interval(5, 40))
        result = select_signals("A", self.observed, self.points, intervals, self.capacity)
        self.assertEqual(result, {"source": "blocked", "signals": None,
                                  "reason": "unsafe_alternative", "alternative_safe": False})
        # Exactly the congestion ceiling is also unsafe: 37 + 96/2 = 85.
        intervals["B"] = interval(5, 37)
        self.assertEqual(select_signals("A", self.observed, self.points, intervals,
                                        self.capacity)["source"], "blocked")

    def test_out_of_domain_on_either_path_falls_back_to_measured(self):
        for path in ("A", "B"):
            intervals = {name: dict(value) for name, value in self.intervals.items()}
            intervals[path]["in_domain"] = False
            result = select_signals("A", self.observed, self.points, intervals, self.capacity)
            self.assertEqual(result["source"], "measured_fallback")
            self.assertEqual(result["reason"], "outside_training_feature_ranges")
            self.assertEqual(result["signals"]["A"], 84)
            self.assertAlmostEqual(result["signals"]["B"], 42)

    def test_missing_unknown_nonfinite_or_inconsistent_forecast_uses_measurements(self):
        cases = [({}, self.intervals), (None, None),
                 (dict(self.points, A=float("nan")), self.intervals),
                 (self.points, {}),
                 (self.points, dict(self.intervals, A=interval(95, 100, gate=True))),
                 (self.points, dict(self.intervals, A=interval(float("nan"), 100, gate=True))),
                 (self.points, dict(self.intervals, A={"lower": 88, "upper": 96})),
                 (self.points, dict(self.intervals, A=interval(88, 96, gate="true")))]
        for points, intervals in cases:
            result = select_signals("A", self.observed, points, intervals, self.capacity)
            self.assertEqual(result["source"], "measured_fallback")
            self.assertEqual(result["signals"]["A"], 84)
            self.assertAlmostEqual(result["signals"]["B"], 42)

    def test_recovery_from_b_always_uses_measured_capacity_projection(self):
        result = select_signals("B", {"A": 0, "B": 25}, self.points, self.intervals, self.capacity)
        self.assertEqual(result["source"], "measured_fallback")
        self.assertEqual(result["reason"], "measured_recovery")
        self.assertEqual(result["signals"], {"A": 50, "B": 25})

    def test_bad_measurements_or_capacities_block_and_unsafe_measurement_is_flagged(self):
        result = select_signals("A", {"A": 95, "B": 50}, {}, {}, self.capacity)
        self.assertEqual(result["source"], "measured_fallback")
        self.assertEqual(result["signals"], {"A": 95, "B": 97.5})
        self.assertFalse(result["alternative_safe"])
        for observed in ({"A": 95}, {"A": float("inf"), "B": 0}):
            self.assertEqual(select_signals("A", observed, {}, {}, self.capacity)["source"], "blocked")
        for capacity in ({"A": 0, "B": 20}, {"A": 10}, {"A": 10, "B": float("nan")}):
            self.assertEqual(select_signals("A", self.observed, self.points, self.intervals,
                                            capacity)["source"], "blocked")
        self.assertEqual(select_signals("unknown", self.observed, {}, {}, self.capacity)["source"], "blocked")

    def test_gate_boolean_cannot_override_lower_threshold(self):
        intervals = dict(self.intervals, A=interval(84, 96, gate=True))
        result = select_signals("A", self.observed, self.points, intervals, self.capacity)
        self.assertEqual(result["source"], "measured_fallback")
        self.assertEqual(result["reason"], "confidence_gate_closed")


if __name__ == "__main__":
    unittest.main()
