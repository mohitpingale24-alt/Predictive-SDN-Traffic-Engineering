import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "..", "sdn_upgrade"))

from qos_policy import (class_for_dscp, class_for_traffic, load_qos_policy,  # noqa: E402
                        meter_bands, queue_for_dscp)
from acl_firewall import AclEngine, load_acl  # noqa: E402
from link_latency import LatencyTracker  # noqa: E402
from slicing import SliceManager, load_slices  # noqa: E402
from topology_graph import MultiObjectiveScorer  # noqa: E402


class QosTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_qos_policy()

    def test_dscp_classes_follow_diffserv(self):
        self.assertEqual(class_for_dscp(self.policy, 46)["name"], "voice")
        self.assertEqual(class_for_dscp(self.policy, 34)["name"], "video")
        self.assertEqual(class_for_dscp(self.policy, 0)["name"], "default")
        self.assertEqual(class_for_dscp(self.policy, 63)["name"], "default")

    def test_meter_rates_derived_from_capacity(self):
        bands = {b["class_name"]: b for b in meter_bands(self.policy, 10.0)}
        self.assertEqual(bands["voice"]["rate_kbps"], 3000)   # 30% of 10 Mbps
        self.assertEqual(bands["bulk"]["rate_kbps"], 1000)
        self.assertNotIn("default", bands)                    # unmetered
        bands20 = {b["class_name"]: b for b in meter_bands(self.policy, 20.0)}
        self.assertEqual(bands20["voice"]["rate_kbps"], 6000)  # scales with capacity

    def test_traffic_classifier_labels_map(self):
        self.assertEqual(class_for_traffic(self.policy, "bulk")["name"], "bulk")
        self.assertEqual(queue_for_dscp(self.policy, 46), 3)

    def test_shares_cannot_overcommit(self):
        bad = {"classes": [dict(name="a", dscp=[1], share=0.7, queue_id=0, meter_id=1),
                           dict(name="b", dscp=[2], share=0.7, queue_id=0, meter_id=2),
                           dict(name="default", dscp=[0], share=None, queue_id=0,
                                meter_id=None)],
               "default_class": "default"}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(bad, f)
            path = f.name
        with self.assertRaises(ValueError):
            load_qos_policy(path)


class AclTests(unittest.TestCase):
    def setUp(self):
        self.engine = AclEngine([
            dict(priority=100, src="10.0.0.0/24", dst="10.0.0.3", protocol="tcp",
                 dst_port=22, action="deny"),
            dict(priority=50, src="10.0.0.1", dst="any", protocol="any",
                 action="allow"),
            dict(priority=200, dst="10.0.0.0/24", protocol="udp", dst_port="8000-9000",
                 action="deny"),
        ], default_policy="allow")

    def test_highest_priority_wins(self):
        # UDP rule (200) beats the host allow (50) inside its port range.
        action, rule = self.engine.evaluate("10.0.0.1", "10.0.0.9", "udp", 4000, 8080)
        self.assertEqual((action, rule), ("deny", 2))
        # Outside the range the allow applies.
        action, rule = self.engine.evaluate("10.0.0.1", "10.0.0.9", "udp", 4000, 53)
        self.assertEqual((action, rule), ("allow", 1))

    def test_cidr_and_port_matching(self):
        action, rule = self.engine.evaluate("10.0.0.7", "10.0.0.3", "tcp", 5000, 22)
        self.assertEqual((action, rule), ("deny", 0))
        action, _ = self.engine.evaluate("10.0.1.7", "10.0.0.3", "tcp", 5000, 22)
        self.assertEqual(action, "allow")  # source outside the /24

    def test_hits_are_counted(self):
        self.engine.evaluate("10.0.0.7", "10.0.0.3", "tcp", 5000, 22)
        self.engine.evaluate("10.0.0.7", "10.0.0.3", "tcp", 5000, 22)
        self.assertEqual(self.engine.hit_counts()[0], 2)

    def test_load_from_json_with_default_deny(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"default_policy": "deny", "rules": []}, f)
            path = f.name
        engine = load_acl(path)
        self.assertEqual(engine.evaluate("1.2.3.4", "5.6.7.8", "tcp", 1, 80),
                         ("deny", None))


class LatencyTests(unittest.TestCase):
    def test_ewma_and_staleness(self):
        tracker = LatencyTracker(alpha=0.5, stale_after_seconds=10)
        tracker.observe("S1-S2", 10.0, 100.0)
        tracker.observe("S1-S2", 20.0, 101.0)
        self.assertEqual(tracker.latency_ms("S1-S2", 105.0), 15.0)
        self.assertIsNone(tracker.latency_ms("S1-S2", 200.0))  # stale
        self.assertIsNone(tracker.latency_ms("S9-S9", 105.0))   # unknown

    def test_path_latency_sums_links(self):
        tracker = LatencyTracker(alpha=1.0)
        tracker.observe_path_rtt(["S1-S2", "S2-S4"], 12.0, 0.0)
        self.assertEqual(tracker.path_latency_ms(["S1-S2", "S2-S4"], 1.0), 12.0)

    def test_delay_flips_scorer_ranking(self):
        tracker = LatencyTracker(alpha=1.0)
        tracker.observe_path_rtt(["S1-S2", "S2-S4"], 4.0, 0.0)    # path A fast
        tracker.observe_path_rtt(["S1-S3", "S3-S4"], 40.0, 0.0)   # path B slow
        candidates = tracker.scorer_candidates(
            {"A": 50.0, "B": 50.0},
            {"A": ["S1-S2", "S2-S4"], "B": ["S1-S3", "S3-S4"]}, now=1.0)
        scorer = MultiObjectiveScorer(weights={"utilization": 0.5, "rtt_ms": 0.5})
        ranked = scorer.score_paths(candidates)
        self.assertEqual(ranked[0]["path"], "A")  # equal utilization: latency decides


class SlicingTests(unittest.TestCase):
    def setUp(self):
        self.manager = SliceManager([
            dict(slice_id="iot", vlan_id=10, hosts=["10.0.0.1", "10.0.0.2"],
                 bandwidth_share=0.2),
            dict(slice_id="office", vlan_id=20, hosts=["10.0.0.3"],
                 bandwidth_share=0.5),
        ])

    def test_isolation_decision(self):
        self.assertEqual(self.manager.isolation_decision("10.0.0.1", "10.0.0.2"), "allow")
        self.assertEqual(self.manager.isolation_decision("10.0.0.1", "10.0.0.3"), "deny")
        self.assertEqual(self.manager.isolation_decision("10.0.0.1", "10.0.0.99"), "allow")

    def test_vlan_and_bandwidth(self):
        self.assertEqual(self.manager.vlan_for_host("10.0.0.3"), 20)
        self.assertEqual(self.manager.slice_bandwidth_mbps("office", 10.0), 5.0)

    def test_host_in_two_slices_rejected(self):
        with self.assertRaises(ValueError):
            SliceManager([
                dict(slice_id="a", vlan_id=1, hosts=["10.0.0.1"], bandwidth_share=0.1),
                dict(slice_id="b", vlan_id=2, hosts=["10.0.0.1"], bandwidth_share=0.1),
            ])

    def test_load_from_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"slices": [dict(slice_id="x", vlan_id=5, hosts=[],
                                       bandwidth_share=0.1)]}, f)
            path = f.name
        self.assertEqual(load_slices(path).slices()[0]["slice_id"], "x")


if __name__ == "__main__":
    unittest.main()
