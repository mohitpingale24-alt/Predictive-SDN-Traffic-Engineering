"""Regression tests for the 2026-11-18 external review fixes.

Each test names the finding it guards. Controller methods are exercised
through AST extraction with fake datapaths (the project's established
pattern — Ryu is not installed on the test machine), pure modules are
tested directly.
"""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))
sys.path.insert(0, str(ROOT / "cnt-ml" / "src"))

from acl_firewall import load_acl, protocol_name  # noqa: E402
from failure_recovery import FailureRecoveryTracker  # noqa: E402
from openflow_routes import connection_match_fields  # noqa: E402
from slicing import load_slices  # noqa: E402
from telemetry_runtime import FeatureHistory  # noqa: E402


def extract_method(name):
    tree = ast.parse((ROOT / "path_switch_controller.py").read_text())
    controller = next(node for node in tree.body
                      if isinstance(node, ast.ClassDef))
    method = next(node for node in controller.body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    return method


def exec_method(name, namespace):
    method = extract_method(name)
    exec(compile(ast.Module(body=[method], type_ignores=[]),
                 "<controller-%s>" % name, "exec"), namespace)
    return namespace[name]


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return SimpleNamespace(args=args, kwargs=kwargs)


# --- Finding: deny rules matched entire MAC pairs -------------------------

class MatchFieldTests(unittest.TestCase):
    def test_full_connection_scope(self):
        fields = connection_match_fields(
            in_port=1, eth_src="aa", eth_dst="bb", ip_src="10.0.0.1",
            ip_dst="10.0.0.3", protocol=6, src_port=40000, dst_port=22,
            dscp=46)
        self.assertEqual(fields["tcp_dst"], 22)
        self.assertEqual(fields["tcp_src"], 40000)
        self.assertEqual(fields["ip_proto"], 6)
        self.assertEqual(fields["ipv4_src"], "10.0.0.1")
        self.assertEqual(fields["ip_dscp"], 46)

    def test_ip_pair_scope_omits_ports(self):
        fields = connection_match_fields(ip_src="10.0.0.1", ip_dst="10.0.0.3")
        self.assertEqual(fields["eth_type"], 0x0800)
        self.assertNotIn("ip_proto", fields)
        self.assertNotIn("tcp_dst", fields)


class CnPolicyCheckTests(unittest.TestCase):
    """Drive the real _cn_policy_check with fake packets/datapaths."""

    def setUp(self):
        self.drops = []
        self.events = []
        namespace = dict(
            ipv4=SimpleNamespace(ipv4="ipv4"), tcp=SimpleNamespace(tcp="tcp"),
            udp=SimpleNamespace(udp="udp"), protocol_name=protocol_name,
            connection_match_fields=connection_match_fields,
            class_for_dscp=lambda policy, dscp: {})
        self.check = exec_method("_cn_policy_check", namespace)

    def run_check(self, pkt, eth, engine=None, manager=None):
        obj = SimpleNamespace(
            slice_manager=manager, acl_engine=engine, qos_policy=None,
            _install_drop=lambda dp, fields: self.drops.append(fields),
            _cn_event=lambda event, reason: self.events.append((event, reason)))
        return self.check(obj, None, pkt, eth, 1)

    @staticmethod
    def packet(ip, segment=None):
        protocols = {"ipv4": ip}
        if segment is not None:
            protocols[segment[0]] = segment[1]
        return SimpleNamespace(
            get_protocol=lambda cls: protocols.get(cls))

    def test_acl_drop_is_scoped_to_the_denied_connection(self):
        engine = load_acl(ROOT / "acl_rules.example.json")
        ip = SimpleNamespace(src="10.0.0.9", dst="10.0.0.3", proto=6, tos=0)
        segment = ("tcp", SimpleNamespace(src_port=40000, dst_port=22))
        eth = SimpleNamespace(src="aa", dst="bb")
        verdict = self.run_check(self.packet(ip, segment), eth, engine=engine)
        self.assertEqual(verdict, "drop")
        self.assertEqual(len(self.drops), 1)
        fields = self.drops[0]
        # Only the denied SSH connection is dropped — not the MAC pair.
        self.assertEqual(fields["tcp_dst"], 22)
        self.assertEqual(fields["tcp_src"], 40000)
        self.assertEqual(fields["ipv4_src"], "10.0.0.9")
        self.assertEqual(self.events[0][0], "acl_drop")

    def test_permitted_connection_installs_no_drop(self):
        engine = load_acl(ROOT / "acl_rules.example.json")
        ip = SimpleNamespace(src="10.0.0.9", dst="10.0.0.3", proto=17, tos=0)
        segment = ("udp", SimpleNamespace(src_port=40000, dst_port=5201))
        eth = SimpleNamespace(src="aa", dst="bb")
        verdict = self.run_check(self.packet(ip, segment), eth, engine=engine)
        self.assertNotEqual(verdict, "drop")
        self.assertEqual(self.drops, [])

    def test_slice_drop_is_scoped_to_the_ip_pair(self):
        manager = load_slices(ROOT / "slices.example.json")
        ip = SimpleNamespace(src="10.0.0.1", dst="10.0.0.3", proto=6, tos=0)
        segment = ("tcp", SimpleNamespace(src_port=40000, dst_port=5201))
        eth = SimpleNamespace(src="aa", dst="bb")
        verdict = self.run_check(self.packet(ip, segment), eth, manager=manager)
        self.assertEqual(verdict, "drop")
        fields = self.drops[0]
        self.assertEqual(fields["ipv4_src"], "10.0.0.1")
        self.assertNotIn("tcp_dst", fields)
        self.assertEqual(self.events[0][0], "slice_isolation_drop")


# --- Finding: learned flows bypassed later policy checks ------------------

class PacketInFlowTests(unittest.TestCase):
    def setUp(self):
        self.flows = []
        self.packet_outs = []
        self.ip = SimpleNamespace(src="10.0.0.1", dst="10.0.0.3", proto=6, tos=0)
        self.segment = SimpleNamespace(src_port=5000, dst_port=5201)
        self.eth = SimpleNamespace(src="aa", dst="bb", ethertype=0x0800)
        protocols = {"eth": self.eth, "ipv4": self.ip,
                     "tcp": self.segment, "udp": None}
        pkt = SimpleNamespace(get_protocol=lambda cls: protocols.get(cls))
        namespace = dict(
            set_ev_cls=lambda *a, **k: (lambda fn: fn),
            ofp_event=SimpleNamespace(EventOFPPacketIn="packetin"),
            MAIN_DISPATCHER="main",
            packet=SimpleNamespace(Packet=lambda data: pkt),
            ethernet=SimpleNamespace(ethernet="eth"),
            ipv4=SimpleNamespace(ipv4="ipv4"), tcp=SimpleNamespace(tcp="tcp"),
            udp=SimpleNamespace(udp="udp"),
            connection_match_fields=connection_match_fields)
        self.handler = exec_method("packet_in_handler", namespace)

    def run_handler(self, ip_present=True):
        if not ip_present:
            self.ip = None
        # Rebuild the packet protocol map for the ARP case.
        protocols = {"eth": self.eth, "ipv4": self.ip,
                     "tcp": self.segment, "udp": None}
        pkt = SimpleNamespace(get_protocol=lambda cls: protocols.get(cls))
        namespace_packet = self.handler.__globals__["packet"]
        namespace_packet.Packet = lambda data: pkt
        ofproto = SimpleNamespace(OFPP_FLOOD=0xFFFFFFFB,
                                  OFP_NO_BUFFER=0xFFFFFFFF)
        datapath = SimpleNamespace(
            id=5, ofproto=ofproto,
            ofproto_parser=SimpleNamespace(
                OFPPacketOut=lambda **kw: self.packet_outs.append(kw)))
        obj = SimpleNamespace(
            _topnotch=True, acl_engine=None, slice_manager=None,
            qos_policy=None, mac_to_port={5: {"bb": 2}},
            _actions_output=lambda dp, port: [("output", port)],
            _match=lambda dp, **kw: kw,
            add_flow=lambda dp, prio, match, actions:
                self.flows.append((prio, match, actions)),
            add_flow_metered=lambda dp, prio, match, actions, meter:
                self.flows.append((prio, match, actions)),
            logger=SimpleNamespace(info=lambda *a: None))
        datapath.send_msg = lambda msg: None
        ev = SimpleNamespace(msg=SimpleNamespace(
            datapath=datapath, data=b"x", buffer_id=ofproto.OFP_NO_BUFFER,
            match={"in_port": 1}))
        self.handler(obj, ev)

    def test_learned_flow_is_scoped_to_the_checked_connection(self):
        self.run_handler(ip_present=True)
        self.assertEqual(len(self.flows), 1)
        _prio, match, _actions = self.flows[0]
        self.assertEqual(match["ipv4_src"], "10.0.0.1")
        self.assertEqual(match["ipv4_dst"], "10.0.0.3")
        self.assertEqual(match["tcp_dst"], 5201)
        self.assertEqual(match["ip_proto"], 6)

    def test_arp_installs_no_forwarding_flow(self):
        self.run_handler(ip_present=False)
        self.assertEqual(self.flows, [])
        self.assertEqual(len(self.packet_outs), 1)


# --- Finding: ECMP omitted return-forwarding rules ------------------------

class EcmpTests(unittest.TestCase):
    def test_return_flows_installed_for_both_branches(self):
        sent = []

        class FakeParser:
            def __getattr__(self, name):
                def build(*args, **kwargs):
                    return SimpleNamespace(kind=name, args=args, kwargs=kwargs)
                return build

        datapath = SimpleNamespace(
            id=1, ofproto_parser=FakeParser(),
            ofproto=SimpleNamespace(OFPGC_ADD="add", OFPGT_SELECT="select",
                                    OFPIT_APPLY_ACTIONS="apply"),
            send_msg=sent.append)
        namespace = dict(S1_PATH_PORT={"A": 2, "B": 3},
                         S4_PATH_PORT={"A": 2, "B": 3},
                         PATH_CAPACITY_MBPS={"A": 10.0, "B": 20.0})
        install = exec_method("_install_ecmp", namespace)
        obj = SimpleNamespace(
            logger=SimpleNamespace(info=lambda *a: None))
        install(obj, datapath)
        flow_mods = [m for m in sent if m.kind == "OFPFlowMod"]
        by_in_port = {}
        for mod in flow_mods:
            match = mod.kwargs["match"]
            by_in_port[match.kwargs["in_port"]] = mod
        self.assertIn(1, by_in_port)  # forward via SELECT group
        for branch in (2, 3):  # return traffic from either branch
            self.assertIn(branch, by_in_port)
            actions = by_in_port[branch].kwargs["instructions"][0].args[1]
            action = actions[0]
            port = action.kwargs.get("port", action.args[0] if action.args else None)
            self.assertEqual(port, 1)


# --- Finding: policy could reroute onto a failed link ---------------------

class SwitchPathGateTests(unittest.TestCase):
    PATH_LINKS = {"A": ("S1-S2", "S2-S4"), "B": ("S1-S3", "S3-S4")}

    def make_obj(self, down_links):
        commits = []
        events = []
        blocked_fn = exec_method(
            "_path_blocked_link", dict(PATH_LINKS=self.PATH_LINKS))
        obj = SimpleNamespace(
            active_path="B", _down_links=set(down_links), datapaths={},
            last_control_signals={},
            control_event_recorder=SimpleNamespace(append=events.append),
            logger=SimpleNamespace(error=lambda *a: None,
                                   info=lambda *a: None),
            _match=lambda dp, **kw: kw,
            _actions_output=lambda dp, port: [("output", port)],
            modify_flow=lambda *a: None,
            _commit_path=lambda *a: commits.append(a))
        obj._path_blocked_link = lambda path: blocked_fn(obj, path)
        return obj, commits, events

    def switch(self):
        namespace = dict(SAFE_REROUTE=False, CONTROL_MODE="predictive",
                         S1_PATH_PORT={"A": 2, "B": 3},
                         S4_PATH_PORT={"A": 2, "B": 3},
                         S1_PORT_LINK={2: ("A", "S1-S2"), 3: ("B", "S1-S3")},
                         PATH_LINKS=self.PATH_LINKS,
                         time=__import__("time"))
        return exec_method("switch_path", namespace)

    def test_reroute_onto_down_link_is_refused(self):
        obj, commits, events = self.make_obj({"S1-S2"})
        self.switch()(obj, "A", "predictive: test")
        self.assertEqual(obj.active_path, "B")
        self.assertEqual(commits, [])
        self.assertEqual(events[0]["event"], "reroute_blocked")

    def test_reroute_blocked_by_s4_side_link_too(self):
        # Finding 10 remainder: the gate covers the whole path, so a
        # failure on S2-S4 (visible only at s4) also blocks path A.
        obj, commits, events = self.make_obj({"S2-S4"})
        self.switch()(obj, "A", "predictive: test")
        self.assertEqual(obj.active_path, "B")
        self.assertEqual(commits, [])
        self.assertEqual(events[0]["event"], "reroute_blocked")

    def test_reroute_onto_up_link_proceeds(self):
        obj, commits, _events = self.make_obj(set())
        self.switch()(obj, "A", "predictive: test")
        self.assertEqual(len(commits), 1)


# --- Finding: failure recovery mistook zero traffic for recovery ----------

class FailureRecoveryAttributionTests(unittest.TestCase):
    def test_dead_links_own_zero_traffic_is_not_recovery(self):
        tracker = FailureRecoveryTracker(recovery_threshold_pct=85)
        tracker.link_down("S1-S2", 100.0, affected_paths=("A",))
        # The dead link's own port stats read ~0% — that is the failure.
        tracker.note_sample(101.0, utilization_pct=0.0, packet_loss_pct=0.0,
                            link_id="S1-S2")
        report = tracker.open_failures()[0]
        self.assertIsNone(report["time_to_recover_seconds"])
        # Traffic on the surviving link before any reroute is not
        # recovery either.
        tracker.note_sample(102.0, utilization_pct=30.0, link_id="S1-S3")
        self.assertIsNone(tracker.open_failures()[0]["time_to_recover_seconds"])
        # After a reroute, under-threshold traffic on the surviving link
        # is service recovery.
        tracker.note_reroute(103.0, "A", "B")
        tracker.note_sample(104.0, utilization_pct=30.0, link_id="S1-S3")
        report = tracker.open_failures()[0]
        self.assertEqual(report["time_to_recover_seconds"], 4.0)


# --- Finding: training features used future measurements ------------------

class CausalPingTests(unittest.TestCase):
    def test_latest_ping_never_uses_the_future(self):
        from prepare_real_dataset import latest_ping
        measurements = [(100.0, 10.0), (101.4, 50.0)]
        # Nearest-in-time would pick the 101.4 ping (0.4 s away, future).
        self.assertEqual(latest_ping(101.0, measurements), 10.0)
        self.assertIsNone(latest_ping(99.0, measurements))
        self.assertIsNone(latest_ping(103.0, measurements))


# --- Finding: history features crossed sampling gaps ----------------------

class TimeHistoryTests(unittest.TestCase):
    @staticmethod
    def load_build_features():
        spec = importlib.util.spec_from_file_location(
            "build_features", ROOT / "cnt-ml" / "src" / "build_features.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_lags_come_from_exact_timestamps(self):
        import pandas as pd
        build = self.load_build_features()
        stamps = [t for t in range(30) if t != 10]  # gap at t=10
        frame = pd.DataFrame({
            "timestamp": stamps, "link_id": ["S1-S2"] * len(stamps),
            "utilization": [float(t) for t in stamps],
            "throughput": 1.0, "delay": 1.0, "packet_loss": 0.0,
            "active_flows": 1})
        group = build.attach_timestamp_target(frame.copy())
        group = build.attach_time_history(group)
        by_stamp = group.set_index("timestamp")
        self.assertTrue(pd.isna(by_stamp.loc[11, "util_lag_1"]))
        self.assertTrue(pd.isna(by_stamp.loc[15, "util_lag_5"]))
        self.assertEqual(by_stamp.loc[16, "util_lag_5"], 11.0)
        self.assertEqual(by_stamp.loc[9, "util_ma_5s"], 7.0)
        survivors = set(group.dropna()["timestamp"])
        # Only rows whose full 10 s history and 5 s target avoid the gap.
        self.assertEqual(survivors, {21, 22, 23, 24})

    def test_feature_history_is_contiguous_by_default(self):
        history = FeatureHistory()
        sample = lambda ts: dict(timestamp=ts, link_id="S1-S2",
                                 utilization=ts, throughput=1, delay=12,
                                 packet_loss=0, active_flows=1)
        features = None
        for ts in range(11):
            features = history.update(sample(ts))
        self.assertIsNotNone(features)
        self.assertIsNone(history.update(sample(12 + 5)))
        self.assertEqual(history.count("S1-S2"), 1)


# --- Finding: forecast DM paired different outcomes -----------------------

class AlignedForecastStatsTests(unittest.TestCase):
    def write_capture(self, directory):
        directory = Path(directory)
        telemetry = ["timestamp,link_id,utilization,throughput,delay,packet_loss,active_flows"]
        for t in range(31):
            telemetry.append("%d,S1-S2,%f,1,1,0,1" % (t, 10 + (t % 7)))
        (directory / "telemetry.csv").write_text("\n".join(telemetry) + "\n")
        predictions = ["timestamp,link_id,predicted_utilization,prediction_status"]
        for t in range(26):
            actual = 10 + ((t + 5) % 7)
            predictions.append("%d,S1-S2,%f,predicted" % (t, actual))
        (directory / "predictions.csv").write_text("\n".join(predictions) + "\n")

    def test_dm_compares_forecasters_on_identical_targets(self):
        from forecast_stats import build_aligned_errors, compare_captures
        with tempfile.TemporaryDirectory() as temporary:
            capture = Path(temporary) / "predictive-ml" / "run-01"
            capture.mkdir(parents=True)
            self.write_capture(capture)
            aligned = build_aligned_errors(capture)
            self.assertEqual(len(aligned), 26)
            self.assertTrue(all(ml == 0.0 for ml, _pe in aligned))
            self.assertTrue(any(pe != 0.0 for _ml, pe in aligned))
            report = compare_captures([capture])
            self.assertIn("cross_system_dm", report)
            dm_keys = list(report["diebold_mariano"])
            self.assertEqual(len(dm_keys), 1)
            self.assertIn("same targets", dm_keys[0])
            self.assertLess(
                report["diebold_mariano"][dm_keys[0]]["mean_loss_diff"], 0)


# --- Finding: conformal coverage was measured on calibration data ---------

class ConformalHeldOutTests(unittest.TestCase):
    def test_artifact_reports_held_out_coverage_only(self):
        from calibrate_conformal import build_artifact
        capture = (ROOT / "final-evaluation-artifacts" / "campaign-02"
                   / "predictive-ml" / "run-01")
        artifact = build_artifact(capture)
        self.assertNotIn("empirical_coverage_on_calibration", artifact)
        self.assertEqual(artifact["calibration_rows"], 103)
        self.assertEqual(artifact["heldout_rows"], 69)
        self.assertEqual(artifact["heldout_coverage"]["n"], 69)

    def test_committed_artifact_uses_held_out_fields(self):
        artifact = json.loads(
            (ROOT / "cnt-ml" / "models" / "conformal_calibration.json")
            .read_text())
        self.assertIn("heldout_coverage", artifact)
        self.assertNotIn("empirical_coverage_on_calibration", artifact)

    def test_artifact_reports_coverage_from_a_separate_capture(self):
        from calibrate_conformal import build_artifact
        base = (ROOT / "final-evaluation-artifacts" / "campaign-02"
                / "predictive-ml")
        artifact = build_artifact(base / "run-01",
                                  eval_capture=str(base / "run-02"))
        self.assertIn("eval_capture_coverage", artifact)
        self.assertIn("eval_capture_coverage", artifact["coverage_basis"])
        bare = build_artifact(base / "run-01")
        self.assertNotIn("eval_capture_coverage", bare)
        self.assertIn("no evaluation capture", bare["coverage_basis"])


# --- Finding: GNN windows crossed gaps and split boundaries ---------------

class GnnDatasetTests(unittest.TestCase):
    def write_telemetry(self, directory, stamp_sets):
        path = Path(directory) / "telemetry.csv"
        lines = ["timestamp,link_id,utilization,throughput,packet_loss"]
        for link_id, stamps in stamp_sets.items():
            for t in stamps:
                lines.append("%d,%s,%f,1,0" % (t, link_id, t % 50))
        path.write_text("\n".join(lines) + "\n")
        return path

    def test_windows_never_cross_a_gap(self):
        from gnn_model import build_dataset
        present = [t for t in range(40) if t not in (20, 21, 22)]
        with tempfile.TemporaryDirectory() as temporary:
            path = self.write_telemetry(
                temporary, {"S1-S2": present, "S1-S3": present})
            _X, _Y, _links, stamps = build_dataset(
                str(path), horizon_steps=2, return_stamps=True)
            present_set = set(present)
            for stamp in stamps:
                self.assertTrue(
                    all((stamp - k) in present_set for k in range(5)))
                self.assertIn(stamp + 2, present_set)
            self.assertNotIn(23, stamps)
            self.assertNotIn(24, stamps)


# --- Finding: quantile training leaked labels across the split ------------

class QuantileEmbargoTests(unittest.TestCase):
    def test_training_rows_with_targets_in_test_period_are_purged(self):
        import train_quantile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lines = ["timestamp,target_timestamp,target,f1"]
            for t in range(100):
                lines.append("%d,%d,%f,%f" % (t, t + 5, float(t), float(t)))
            data = root / "data.csv"
            data.write_text("\n".join(lines) + "\n")
            features = root / "features.json"
            features.write_text(json.dumps(["f1"]))
            metrics = root / "metrics.json"
            argv = ["train_quantile", str(data), "--features", str(features),
                    "--out", str(root / "model.joblib"),
                    "--metrics", str(metrics)]
            with patch.object(sys, "argv", argv):
                train_quantile.main()
            report = json.loads(metrics.read_text())
            # Split at row 70 (t=70); rows 65..69 target t=70..74, inside
            # the test period — all five must be purged from training.
            self.assertEqual(report["boundary_purged_rows"], 5)
            self.assertEqual(report["train_rows"], 65)
            self.assertIn("embargo", report["split_method"])


# --- Finding: missing telemetry became zero congestion --------------------

class AnalyzerGuardTests(unittest.TestCase):
    def test_run_without_telemetry_rows_is_rejected(self):
        import analyze_results
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "metadata.json").write_text(
                json.dumps({"status": "complete"}))
            (run_dir / "telemetry.csv").write_text(
                "timestamp,link_id,utilization\n")
            with patch.object(analyze_results, "check_run", lambda *a: None):
                with self.assertRaisesRegex(ValueError, "no telemetry rows"):
                    analyze_results.load_run(run_dir)


# --- Finding: strong simple baselines were missing ------------------------

class StaticFastestBaselineTests(unittest.TestCase):
    def test_static_fastest_pins_the_fast_path(self):
        from run_research_experiment import controller_environment
        from scenarios import scenario_definition
        env, config = controller_environment(
            Path("/repo"), Path("/capture"),
            SimpleNamespace(system="static-fastest"),
            scenario_definition("ramp"))
        self.assertEqual(config["control_mode"], "static")
        self.assertEqual(env["SDN_INITIAL_PATH"], "B")
        env, _config = controller_environment(
            Path("/repo"), Path("/capture"),
            SimpleNamespace(system="static"), scenario_definition("ramp"))
        self.assertEqual(env["SDN_INITIAL_PATH"], "A")


# --- Finding 16: DM test reported a constant advantage as equality -------

class DmDegenerateTests(unittest.TestCase):
    def test_constant_nonzero_difference_keeps_its_mean(self):
        from forecast_stats import diebold_mariano
        result = diebold_mariano([2.0] * 20, [1.0] * 20, horizon=5)
        self.assertEqual(result["mean_loss_diff"], 3.0)
        self.assertIsNone(result["p_value"])
        self.assertTrue(result["degenerate"])
        self.assertFalse(result["significant_5pct"])

    def test_identical_errors_still_p_one(self):
        from forecast_stats import diebold_mariano
        result = diebold_mariano([1.5] * 20, [1.5] * 20, horizon=5)
        self.assertEqual(result["mean_loss_diff"], 0.0)
        self.assertEqual(result["p_value"], 1.0)


# --- Finding 27: campaign_plan crashed on extension scenarios -------------

class CampaignPlanScenarioTests(unittest.TestCase):
    def test_every_exposed_scenario_plans(self):
        from scenarios import (EXTENSION_SCENARIOS, PRIMARY_SYSTEMS,
                               SCENARIOS, campaign_plan, scenario_definition)
        for name in list(SCENARIOS) + list(EXTENSION_SCENARIOS):
            plan = campaign_plan(scenarios=[name], repetitions=1)
            self.assertEqual(len(plan), len(PRIMARY_SYSTEMS), name)
            self.assertEqual(plan[0]["topology"],
                             scenario_definition(name)["topology"], name)


# --- Finding 21 (gate half): outcome evidence is required -----------------

class QualityGateEvidenceTests(unittest.TestCase):
    def _run_dir(self, tmp):
        directory = Path(tmp)
        (directory / "controller.log").write_text("")
        return directory

    def test_missing_telemetry_fails(self):
        from quality_gate import check_run
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                check_run(self._run_dir(tmp), {"workload": []})

    def test_thin_telemetry_fails(self):
        from quality_gate import check_run
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._run_dir(tmp)
            (directory / "telemetry.csv").write_text(
                "timestamp,utilization\n1,10\n2,20\n")
            with self.assertRaises(ValueError):
                check_run(directory, {"workload": []})

    def test_missing_ping_samples_fail(self):
        from quality_gate import check_run
        with tempfile.TemporaryDirectory() as tmp:
            directory = self._run_dir(tmp)
            rows = "".join("%d,10.0\n" % i for i in range(12))
            (directory / "telemetry.csv").write_text(
                "timestamp,utilization\n" + rows)
            (directory / "ping.log").write_text("no replies\n")
            with self.assertRaises(ValueError):
                check_run(directory, {"workload": []})


# --- Finding 3 (dispatch half): ECMP can only run on the research runner --

class EcmpDispatchTests(unittest.TestCase):
    def test_ecmp_legacy_is_coerced(self):
        from live_runtime import resolve_scenario
        self.assertEqual(resolve_scenario("ecmp", "legacy"), "ramp")
        self.assertEqual(resolve_scenario("ecmp", "burst"), "burst")
        self.assertEqual(resolve_scenario("static", "legacy"), "legacy")
        self.assertEqual(resolve_scenario("predictive-safe", "legacy"), "ramp")

    def test_guest_has_last_line_defense(self):
        text = (ROOT / "experiments" / "live_guest.py").read_text()
        self.assertIn("args.system == 'ecmp'", text)


# --- Findings 7/8: conformal domain is computed, model is bound -----------

class ConformalRuntimeBindingTests(unittest.TestCase):
    def _artifact(self, tmp, model_bytes):
        import hashlib
        model = Path(tmp) / "model.json"
        model.write_bytes(model_bytes)
        artifact = {
            "method": "split_conformal_absolute_residual",
            "alpha": 0.1, "nominal_coverage": 0.9,
            "radius_percentage_points": 10.0,
            "aci": {"gamma": 0.01, "scores": [5.0, 10.0]},
            "calibration_ranges": {"prediction": [0.0, 80.0],
                                   "actual": [0.0, 90.0]},
            "model_binding": {
                "model_sha256": hashlib.sha256(model_bytes).hexdigest()},
        }
        path = Path(tmp) / "artifact.json"
        path.write_text(json.dumps(artifact))
        return path, model

    def test_domain_computed_from_calibration_range(self):
        from conformal_runtime import ConformalRuntime
        with tempfile.TemporaryDirectory() as tmp:
            artifact, model = self._artifact(tmp, b"model-a")
            runtime = ConformalRuntime(str(artifact), model_path=str(model))
            self.assertTrue(runtime.interval(50.0)["in_domain"])
            outside = runtime.interval(95.0)
            self.assertFalse(outside["in_domain"])
            self.assertEqual(outside["out_of_domain_features"], ["prediction"])

    def test_swapped_model_is_refused(self):
        from conformal_runtime import ConformalRuntime
        with tempfile.TemporaryDirectory() as tmp:
            artifact, _ = self._artifact(tmp, b"model-a")
            other = Path(tmp) / "other.json"
            other.write_bytes(b"model-b")
            with self.assertRaises(ValueError):
                ConformalRuntime(str(artifact), model_path=str(other))


# --- Finding 18: forecasts pair only with their own target time -----------

class _StubDetector:
    def __init__(self):
        self.seen = []

    def observe(self, value):
        self.seen.append(value)
        return False


class DriftPairingTests(unittest.TestCase):
    def _controller(self):
        namespace = {}
        observe = exec_method("_observe_drift", namespace)
        detector = _StubDetector()
        fake = SimpleNamespace(
            _topnotch=True, _track_pairs=True, prediction_horizon=5,
            _pending_predictions={"A": [(100.0, 50.0), (101.0, 60.0),
                                         (102.0, 70.0)], "B": []},
            conformal=None, _recent_residuals=[], drift_adaptation=None,
            detectors={"stub": detector}, _detector_fired=set(),
            control_event_recorder=Recorder(), drift_monitor=None,
            drift_recorder=Recorder(), _unpaired_forecasts=0,
            logger=SimpleNamespace(error=lambda *a, **k: None))
        return fake, observe, detector

    def test_only_the_matching_target_pairs(self):
        fake, observe, detector = self._controller()
        observe(fake, "A", 105.2, 55.0)
        self.assertEqual(detector.seen, [5.0])
        self.assertEqual(len(fake._pending_predictions["A"]), 2)
        self.assertEqual(fake._unpaired_forecasts, 0)

    def test_gap_expires_unpaired_never_repairs(self):
        fake, observe, detector = self._controller()
        observe(fake, "A", 120.0, 10.0)
        self.assertEqual(detector.seen, [])
        self.assertEqual(fake._pending_predictions["A"], [])
        self.assertEqual(fake._unpaired_forecasts, 3)


# --- Finding 17: GNN node identity + self-describing artifacts ------------

class GnnResidualTests(unittest.TestCase):
    def test_asymmetric_links_get_distinct_predictions(self):
        import numpy as np
        from gnn_model import (GCNForecaster, line_graph_adjacency,
                               normalized_adjacency)
        graph = normalized_adjacency(line_graph_adjacency(["S1-S2", "S1-S3"]))
        X = np.array([[[10.0, 1.0, 0.0], [90.0, 9.0, 5.0]]] * 4)
        model = GCNForecaster(3, hidden=8, seed=1)
        prediction = model.predict(graph, X)
        self.assertFalse(np.allclose(prediction[:, 0], prediction[:, 1]))

    def test_save_load_round_trip_with_meta(self):
        import numpy as np
        from gnn_model import (GCNForecaster, line_graph_adjacency,
                               normalized_adjacency)
        graph = normalized_adjacency(line_graph_adjacency(["S1-S2", "S1-S3"]))
        X = np.random.default_rng(0).normal(size=(3, 2, 4))
        model = GCNForecaster(4, hidden=8, seed=2)
        meta = {"link_ids": ["S1-S2", "S1-S3"], "horizon_steps": 5,
                "target_mean": 40.0, "target_std": 20.0}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "model.npz")
            model.save(path, meta=meta)
            loaded = GCNForecaster.load(path)
        self.assertEqual(loaded.meta, meta)
        self.assertTrue(np.allclose(model.predict(graph, X),
                                    loaded.predict(graph, X)))


# --- Finding 25: auto model selection never reads test tables -------------

class AutoModelSelectionTests(unittest.TestCase):
    def test_auto_resolves_to_frozen_gradient_boosting(self):
        import predict
        with patch.object(predict, "ACTIVE_MODEL", "auto"), \
                patch.object(predict, "_resolved_model", None):
            self.assertEqual(predict.resolve_active_model(),
                             "gradient_boosting")


# --- Finding 4 (loss half): iperf loss only from completed segments -------

class CausalLossTests(unittest.TestCase):
    def test_segment_summary_not_used_inside_its_own_segment(self):
        from prepare_real_dataset import iperf_completed_at
        intervals = [{"start": 0.0, "end": 30.0, "loss_percent": 5.0},
                     {"start": 30.0, "end": 60.0, "loss_percent": 7.0}]
        self.assertIsNone(iperf_completed_at(10.0, intervals))
        self.assertEqual(iperf_completed_at(45.0, intervals)["loss_percent"], 5.0)
        self.assertEqual(iperf_completed_at(60.0, intervals)["loss_percent"], 7.0)


# --- Findings 10/19/23: controller wiring guards --------------------------

class ControllerSourceGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = (ROOT / "path_switch_controller.py").read_text()

    def test_s4_failures_are_handled(self):
        self.assertIn("PORT_LINK_BY_DPID = {1: S1_PORT_LINK, 4: S4_PORT_LINK}",
                      self.text)
        self.assertIn("S4_PORT_LINK = {2: ('A', 'S2-S4'), 3: ('B', 'S3-S4')}",
                      self.text)

    def test_switch_gate_covers_whole_path(self):
        self.assertIn("self._path_blocked_link(new_path)", self.text)

    def test_latency_only_from_active_path(self):
        self.assertIn("and path == self.active_path", self.text)

    def test_path_file_written_without_following_symlinks(self):
        self.assertIn("O_NOFOLLOW", self.text)


if __name__ == "__main__":
    unittest.main()
