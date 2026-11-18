import unittest

from telemetry_runtime import FeatureHistory, ReroutePolicy


class ResearchRuntimeSafetyTests(unittest.TestCase):
    def sample(self, timestamp):
        return dict(timestamp=timestamp, link_id='S1-S2', utilization=timestamp,
                    throughput=1, delay=12, packet_loss=0, active_flows=1)

    def test_contiguous_history_rewarms_after_missing_second(self):
        history = FeatureHistory(require_contiguous=True)
        for timestamp in range(11):
            features = history.update(self.sample(timestamp))
        self.assertIsNotNone(features)
        self.assertIsNone(history.update(self.sample(12)))
        self.assertEqual(history.count('S1-S2'), 1)
        for timestamp in range(13, 23):
            features = history.update(self.sample(timestamp))
        self.assertEqual(features['util_lag_10'], 12)

    def test_duplicate_and_out_of_order_history_reset(self):
        for timestamp in (10, 8):
            history = FeatureHistory(require_contiguous=True)
            for second in range(11):
                history.update(self.sample(second))
            self.assertIsNone(history.update(self.sample(timestamp)))
            self.assertEqual(history.count('S1-S2'), 1)

    def test_unsafe_alternative_is_rejected(self):
        policy = ReroutePolicy(consecutive_samples=1, alternative_ceiling=85)
        self.assertIsNone(policy.evaluate('A', {'A': 100, 'B': 90}, 1))
        self.assertIsNone(policy.evaluate('A', {'A': 100, 'B': 85}, 2))
        self.assertEqual(policy.evaluate('A', {'A': 100, 'B': 84}, 3), 'B')

    def test_failed_uncommitted_decision_does_not_consume_cooldown(self):
        policy = ReroutePolicy(consecutive_samples=1, commit_on_decision=False)
        self.assertEqual(policy.evaluate('A', {'A': 90, 'B': 45}, 1), 'B')
        self.assertIsNone(policy.last_switch_time)
        policy.reset()
        self.assertEqual(policy.evaluate('A', {'A': 90, 'B': 45}, 2), 'B')
        policy.commit(3)
        self.assertIsNone(policy.evaluate('B', {'A': 20, 'B': 10}, 4))


if __name__ == '__main__':
    unittest.main()
