"""Exercise the real controller cache helper without installing Ryu on macOS."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

from telemetry_runtime import ReroutePolicy


class ForecastCacheTests(unittest.TestCase):
    def helper(self, confidence_enabled):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'path_switch_controller.py').read_text())
        controller = next(node for node in tree.body if isinstance(node, ast.ClassDef))
        method = next(node for node in controller.body if isinstance(node, ast.FunctionDef)
                      and node.name == '_invalidate_prediction')
        namespace = {'CONFIDENCE_GATE':confidence_enabled}
        exec(compile(ast.Module(body=[method],type_ignores=[]),'<controller-cache>','exec'),namespace)
        return namespace['_invalidate_prediction']

    def test_rtt_outage_clears_forecast_but_preserves_measured_streak(self):
        policy=ReroutePolicy(consecutive_samples=3)
        obj=SimpleNamespace(latest_predictions={'A':95},latest_prediction_times={'A':10},
                            latest_intervals={'A':{'lower':90}},last_decision_source='measured_fallback',
                            reroute_policy=policy)
        for timestamp in (1,2):
            self.assertIsNone(policy.evaluate('A',{'A':95,'B':45},timestamp))
            self.helper(True)(obj,'A')
        self.assertEqual(policy.evaluate('A',{'A':95,'B':45},3),'B')
        self.assertFalse(obj.latest_predictions)
        self.assertFalse(obj.latest_prediction_times)
        self.assertFalse(obj.latest_intervals)

    def test_invalid_forecast_cannot_continue_a_learned_streak(self):
        for confidence,source in ((True,'learned_confidence'),(False,None)):
            policy=ReroutePolicy(consecutive_samples=3)
            policy.evaluate('A',{'A':95,'B':45},1)
            policy.evaluate('A',{'A':95,'B':45},2)
            obj=SimpleNamespace(latest_predictions={'A':95},latest_prediction_times={'A':10},
                latest_intervals={'A':{'lower':90}},last_decision_source=source,reroute_policy=policy)
            self.helper(confidence)(obj,'A')
            self.assertIsNone(policy.evaluate('A',{'A':95,'B':45},3))


if __name__ == '__main__':
    unittest.main()
