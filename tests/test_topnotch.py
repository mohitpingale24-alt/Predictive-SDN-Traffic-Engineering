"""Tests for the top-notch extension: every number is computed, none hardcoded.

Covers topology discovery + k-shortest paths, ECMP, multi-objective scoring,
traffic classes, link-failure recovery measurement, drift detection +
recalibration, explainability, anomaly mitigation, the new workload/system
registrations, parameterized topologies, and the campaign report generator.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

from topology_graph import (MultiObjectiveScorer, TopologyGraph, TrafficClassifier,
                            ecmp_select, ecmp_weighted_select)
from failure_recovery import FailureRecoveryTracker
from drift_detection import DriftMonitor, recalibrate
from explainability import build_explanation, top_contributing_features
from anomaly_mitigation import SurgeDetector
import topologies_v2


def diamond():
    graph = TopologyGraph()
    graph.add_link("s1", "s2", 10, delay_ms=2, link_id="S1-S2")
    graph.add_link("s2", "s4", 10, delay_ms=2, link_id="S2-S4")
    graph.add_link("s1", "s3", 20, delay_ms=4, link_id="S1-S3")
    graph.add_link("s3", "s4", 20, delay_ms=4, link_id="S3-S4")
    return graph


def test_k_shortest_orders_by_inverse_capacity():
    paths = diamond().k_shortest_paths("s1", "s4", k=2)
    assert paths[0] == ["s1", "s3", "s4"]  # 20 Mbps path wins on inverse capacity
    assert paths[1] == ["s1", "s2", "s4"]


def test_equal_cost_and_failure_reroute_at_graph_level():
    graph = TopologyGraph()
    graph.add_link("a", "b", 10)
    graph.add_link("b", "d", 10)
    graph.add_link("a", "c", 10)
    graph.add_link("c", "d", 10)
    assert len(graph.equal_cost_paths("a", "d", metric="hops")) == 2
    graph.remove_link(link_id="a--b")
    assert graph.k_shortest_paths("a", "d", k=1) == [["a", "c", "d"]]


def test_ecmp_is_deterministic_and_spreads():
    paths = [["s1", "s2", "s4"], ["s1", "s3", "s4"]]
    assert ecmp_select("flow-1", paths) == ecmp_select("flow-1", paths)
    chosen = {tuple(ecmp_select("flow-%d" % i, paths)) for i in range(50)}
    assert len(chosen) == 2
    weighted = ecmp_weighted_select("f", [(paths[0], 1.0), (paths[1], 3.0)])
    assert weighted in paths


def test_multi_objective_and_traffic_classes():
    scorer = MultiObjectiveScorer({"utilization": 1.0, "rtt_ms": 1.0},
                                  class_weights={"latency": {"rtt_ms": 3.0, "utilization": 1.0}})
    candidates = {"fast": {"utilization": 80, "rtt_ms": 5},
                  "empty": {"utilization": 10, "rtt_ms": 30}}
    assert scorer.score_paths(candidates)[0]["path"] == "empty"
    assert scorer.score_paths(candidates, traffic_class="latency")[0]["path"] == "fast"
    classifier = TrafficClassifier([{"class": "latency", "dscp": 46},
                                    {"class": "bulk", "min_bytes": 10_000}])
    assert classifier.classify({"dscp": 46}) == "latency"
    assert classifier.classify({"min_bytes": 20_000}) == "bulk"
    assert classifier.classify({}) == "default"


def test_failure_recovery_measures_from_events():
    tracker = FailureRecoveryTracker(recovery_threshold_pct=85)
    tracker.link_down("S1-S2", 100.0, affected_paths=("A",))
    tracker.note_sample(101.0, utilization_pct=95, packet_loss_pct=12)
    tracker.note_reroute(103.5, "A", "B")
    tracker.note_sample(108.0, utilization_pct=40, packet_loss_pct=1)
    report = tracker.open_failures()[0]
    assert report["time_to_reroute_seconds"] == 3.5
    assert report["time_to_recover_seconds"] == 8.0
    assert report["max_packet_loss_during_outage_pct"] == 12


def test_drift_needs_consecutive_windows_and_recalibrates():
    monitor = DriftMonitor(baseline_rmse=2.0, window=10, factor=1.5, consecutive_windows=2)
    result = None
    for i in range(30):
        result = monitor.observe(50.0, 50.0, timestamp=i)  # perfect: no drift
    assert not result["drift_detected"]
    for i in range(30, 60):
        result = monitor.observe(90.0, 10.0, timestamp=i)  # huge error: drift
    assert result["drift_detected"]
    artifact = recalibrate(monitor.pairs(), coverage=0.9)
    assert artifact["rows"] == 60 and artifact["radius_percentage_points"] > 0


def test_explanation_uses_only_supplied_inputs():
    explanation = build_explanation(
        timestamp=1.0, active_path="A", candidate="B", source="test", reason="unit",
        signals={"A": 92.0, "B": 40.0}, predictions={"A": 91.0},
        intervals={"A": {"lower": 88, "upper": 94, "in_domain": True}},
        features={"utilization": 92.0},
        feature_ranges={"utilization": {"min": 0, "max": 100}},
        importance={"utilization": 0.5}, threshold=85.0)
    assert explanation["projected_improvement_points"] == 52.0
    assert explanation["top_features"][0]["feature"] == "utilization"
    assert top_contributing_features({}, {}, {}) == []


def test_anomaly_detector_flags_surge_not_baseline():
    detector = SurgeDetector(window=20, z_threshold=3.0, min_samples=10)
    for i in range(15):
        assert detector.observe("src", 5.0 + (i % 2) * 0.1, i) is None
    verdict = detector.observe("src", 40.0, 100.0)
    assert verdict is not None and verdict["kind"] in ("surge", "elephant_flow")
    assert detector.active_mitigations(100.0)
    assert detector.active_mitigations(1000.0, ttl_seconds=60.0) == []


def test_new_systems_and_workloads_registered():
    from scenarios import SYSTEM_CONFIG, scenario_definition
    assert SYSTEM_CONFIG["ecmp"]["control_mode"] == "ecmp"
    for name in ("bursty-mixed", "elephant-mice", "multi-pair"):
        definition = scenario_definition(name)
        assert definition["duration_seconds"] == sum(
            s["duration_seconds"] for s in definition["workload"])
        assert definition["duration_seconds"] == 90


def test_parameterized_topologies_generate_wiring():
    fat = topologies_v2.fat_tree_spec(k=2)
    assert len(fat["hosts"]) == 2  # k^3/4
    graph = TopologyGraph()
    for link in fat["links"]:
        graph.add_link(link["a"], link["b"], link["capacity_mbps"], link["delay_ms"], link["link_id"])
    assert graph.k_shortest_paths(fat["hosts"][0], fat["hosts"][-1], k=1)
    spine = topologies_v2.spine_leaf_spec(spines=2, leaves=2, hosts_per_leaf=2)
    assert len(spine["hosts"]) == 4 and len(spine["switches"]) == 4


def test_campaign_report_computes_from_rows(tmp_path):
    import generate_campaign_report as report_module
    run = tmp_path / "static" / "run-01"
    run.mkdir(parents=True)
    (run / "telemetry.csv").write_text(
        "timestamp,link_id,utilization,throughput,delay,packet_loss,active_flows\n"
        "1,S1-S2,90,9,1,2,1\n2,S1-S2,10,1,1,0,1\n")
    (run / "control_events.csv").write_text(
        "timestamp,event,control_mode,old_path,new_path,signal_a,signal_b,reason\n")
    report = report_module.build_report(tmp_path)
    summary = report["systems"]["static"][0]
    assert summary["congestion_seconds"] == 1
    assert summary["mean_utilization"] == 50.0
    assert "static" in report_module.render_markdown(report)
