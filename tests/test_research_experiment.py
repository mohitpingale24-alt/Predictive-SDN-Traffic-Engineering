"""Focused checks for new scenario controls and concurrent receiver capture."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.scenarios import SCENARIOS, PRIMARY_SYSTEMS, ABLATION_SYSTEMS, scenario_definition, campaign_plan
from experiments.run_research_experiment import controller_environment, receiver_intervals, build_network


class ResearchExperimentTests(unittest.TestCase):
    def test_workload_duration_and_aggregate_flow_rates_are_controlled(self):
        for name in SCENARIOS:
            with self.subTest(name=name):
                definition = scenario_definition(name)
                self.assertEqual(definition["duration_seconds"], 90)
                self.assertEqual(sum(flow["rate_fraction"] for flow in definition["flows"]), 1)
        multi = scenario_definition("multi-flow")
        self.assertEqual(multi["topology"], "access6")
        congestion = next(row for row in multi["workload"] if row["name"] == "shared_congestion")
        self.assertEqual([congestion["rate_mbps"] * flow["rate_fraction"] for flow in multi["flows"]], [3, 4, 5])
        multi["workload"][0]["rate_mbps"] = 900
        self.assertEqual(scenario_definition("multi-flow")["workload"][0]["rate_mbps"], 3)

    def test_full_campaign_has_all_matched_systems_and_reverses_order(self):
        plan = campaign_plan(3)
        self.assertEqual(len(plan), 7 * 3 * len(PRIMARY_SYSTEMS))
        self.assertEqual({row["system"] for row in plan}, set(PRIMARY_SYSTEMS))
        width = len(PRIMARY_SYSTEMS)
        self.assertEqual([row["system"] for row in plan[:width]], list(PRIMARY_SYSTEMS))
        self.assertEqual([row["system"] for row in plan[width:2 * width]], list(reversed(PRIMARY_SYSTEMS)))
        ablation = campaign_plan(1, include_ablations=True, scenarios=["near-threshold"])
        self.assertEqual({row["system"] for row in ablation}, set(PRIMARY_SYSTEMS + ABLATION_SYSTEMS))

    def test_scaled_smoke_and_stressed_alternative_are_explicit(self):
        self.assertTrue(scenario_definition("ramp", duration_scale=.5)["smoke"])
        self.assertEqual(scenario_definition("alternative-stress")["path_b_capacity_mbps"], 8)
        for value in (0, -1, float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                scenario_definition("normal", path_b_capacity=value)

    def test_safe_configuration_and_ablation_flags(self):
        root, output = Path("/repo"), Path("/capture")
        for system, expected in (("predictive-safe", ("1", "1")), ("predictive-confidence", ("0", "1")),
                                 ("predictive-safe-only", ("1", "0")), ("predictive-ml", ("0", "0"))):
            env, config = controller_environment(root, output, SimpleNamespace(system=system), scenario_definition("ramp"))
            self.assertEqual((env["SDN_SAFE_REROUTE"], env["SDN_CONFIDENCE_GATE"]), expected)
            self.assertEqual(env["SDN_CONTIGUOUS_HISTORY"], "1")
            self.assertEqual(config["model"], "gradient_boosting")

    def test_receiver_intervals_are_explicit_not_sender_substitution(self):
        summary = dict(sender=False, bytes=0, seconds=1, packets=100, lost_packets=2, jitter_ms=.5)
        interval = dict(summary, bytes=9800, start=0, end=1)
        payload = dict(server_output_json=dict(end=dict(sum=summary), intervals=[dict(sum=interval)]))
        rows = receiver_intervals(payload, 100, dict(flow_id=2, receiver="h4"), "burst")
        self.assertEqual(rows[0]["start_timestamp"], 100)
        self.assertEqual(rows[0]["end_timestamp"], 101)
        self.assertEqual(rows[0]["bytes"], 9800)
        self.assertIn("approximate", rows[0]["interval_reference"])
        interval["sender"] = True
        with self.assertRaisesRegex(ValueError, "receiver"):
            receiver_intervals(payload, 100, dict(flow_id=2, receiver="h4"), "burst")

    def test_access_topology_preserves_core_ports_and_has_three_pairs(self):
        class Node:
            def __init__(self, name): self.name = name
            def start(self, *_args): pass
        class FakeNet:
            def __init__(self, **_kwargs): self.links = []; self.switches = []
            def addController(self, name, **_kwargs): return Node(name)
            def addHost(self, name, **_kwargs): return Node(name)
            def addSwitch(self, name, **_kwargs):
                self.switches.append(name)
                return Node(name)
            def addLink(self, left, right, **kwargs): self.links.append((left.name, right.name, kwargs))
            def build(self): pass
            def stop(self): pass
        modules = {"mininet": SimpleNamespace(), "mininet.link": SimpleNamespace(TCLink=object),
                   "mininet.net": SimpleNamespace(Mininet=FakeNet),
                   "mininet.node": SimpleNamespace(OVSSwitch=object, RemoteController=object)}
        with patch.dict("sys.modules", modules):
            net, hosts = build_network(6633, scenario_definition("multi-flow"))
        self.assertEqual(set(hosts), {"h1", "h2", "h3", "h4", "h5", "h6"})
        self.assertEqual(len(net.switches), 6)
        links = {(left, right): options for left, right, options in net.links}
        self.assertEqual((links["s5", "s1"]["port1"], links["s5", "s1"]["port2"]), (4, 1))
        self.assertEqual((links["s6", "s4"]["port1"], links["s6", "s4"]["port2"]), (4, 1))
        self.assertEqual((links["s1", "s2"]["port1"], links["s1", "s3"]["port1"]), (2, 3))
        self.assertEqual((links["s2", "s4"]["port1"], links["s3", "s4"]["port1"]), (2, 2))


if __name__ == "__main__":
    unittest.main()
