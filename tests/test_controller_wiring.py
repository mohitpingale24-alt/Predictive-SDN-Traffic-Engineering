"""Guard: controller constructor calls must match module signatures.

The 2026-11-18 live-run failure (FailureRecoveryTracker called with
recovery_threshold= instead of recovery_threshold_pct=) killed the
controller at startup in every mode, and no unit test caught it because
nothing instantiated the controller. This test statically extracts the
keyword arguments of every known constructor call in the controller
source and checks them against the real signatures — no Ryu needed.
"""

import ast
import inspect
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "..", "sdn_upgrade"))

from failure_recovery import FailureRecoveryTracker  # noqa: E402
from drift_detection import DriftMonitor  # noqa: E402
from anomaly_mitigation import SurgeDetector  # noqa: E402
from conformal_runtime import ConformalRuntime  # noqa: E402
from drift_adaptation import AdwinLite, PageHinkley  # noqa: E402
from link_latency import LatencyTracker  # noqa: E402
from link_map import load_link_map  # noqa: E402

CONSTRUCTORS = {
    "FailureRecoveryTracker": FailureRecoveryTracker,
    "DriftMonitor": DriftMonitor,
    "SurgeDetector": SurgeDetector,
    "ConformalRuntime": ConformalRuntime,
    "PageHinkley": PageHinkley,
    "AdwinLite": AdwinLite,
    "LatencyTracker": LatencyTracker,
    "load_link_map": load_link_map,
    "load_qos_policy": None,   # filled below (keyword-only use is fine)
    "load_acl": None,
    "load_slices": None,
}

from qos_policy import load_qos_policy, meter_bands, class_for_dscp  # noqa: E402
from acl_firewall import load_acl, protocol_name, AclEngine  # noqa: E402
from slicing import load_slices, SliceManager  # noqa: E402
from explainability import (build_explanation, explanation_csv_row,  # noqa: E402
                            portable_gbdt_importance)
from drift_detection import write_recalibration_artifact  # noqa: E402

CONSTRUCTORS.update({"load_qos_policy": load_qos_policy,
                     "load_acl": load_acl,
                     "load_slices": load_slices})

# Plain module-level functions the controller calls.
FUNCTIONS = {"meter_bands": meter_bands,
             "class_for_dscp": class_for_dscp,
             "protocol_name": protocol_name,
             "build_explanation": build_explanation,
             "explanation_csv_row": explanation_csv_row,
             "portable_gbdt_importance": portable_gbdt_importance,
             "write_recalibration_artifact": write_recalibration_artifact}

# Methods called on known controller attributes (self.<attr>.<method>).
RECEIVERS = {"failure_tracker": FailureRecoveryTracker,
             "anomaly_detector": SurgeDetector,
             "latency_tracker": LatencyTracker,
             "acl_engine": AclEngine,
             "slice_manager": SliceManager,
             "drift_monitor": DriftMonitor,
             "conformal": ConformalRuntime}


def _call_sites(source, name):
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            called = None
            if isinstance(func, ast.Name):
                called = func.id
            elif isinstance(func, ast.Attribute):
                called = func.attr
            if called == name:
                yield node


class ControllerWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "path_switch_controller.py"),
                  encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_every_constructor_kwarg_exists_in_signature(self):
        problems = []
        for name, cls in CONSTRUCTORS.items():
            params = set(inspect.signature(cls.__init__).parameters) \
                if inspect.isclass(cls) else \
                set(inspect.signature(cls).parameters)
            sites = list(_call_sites(self.source, name))
            self.assertTrue(sites, "controller never calls %s" % name)
            for site in sites:
                for keyword in site.keywords:
                    if keyword.arg is not None and keyword.arg not in params:
                        problems.append("%s() got unexpected kwarg %s (line %d)"
                                        % (name, keyword.arg, site.lineno))
        self.assertEqual(problems, [])

    def test_function_and_method_kwargs_match_signatures(self):
        tree = ast.parse(self.source)
        problems = []
        checked = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            target = None
            label = None
            if isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
                target = FUNCTIONS[node.func.id]
                label = node.func.id
            elif isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Attribute) \
                    and isinstance(node.func.value.value, ast.Name) \
                    and node.func.value.value.id == "self" \
                    and node.func.value.attr in RECEIVERS:
                cls = RECEIVERS[node.func.value.attr]
                if hasattr(cls, node.func.attr):
                    target = getattr(cls, node.func.attr)
                    label = "%s.%s" % (node.func.value.attr, node.func.attr)
            if target is None:
                continue
            params = set(inspect.signature(target).parameters)
            checked += 1
            for keyword in node.keywords:
                if keyword.arg is not None and keyword.arg not in params:
                    problems.append("%s() got unexpected kwarg %s (line %d)"
                                    % (label, keyword.arg, node.lineno))
        self.assertGreater(checked, 10)  # the sweep must actually engage
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()


class RyuHandlerSignatureTests(unittest.TestCase):
    """Ryu calls @set_ev_cls handlers with only the event object, so every
    parameter after ev must have a default. (Regression: _record_all_links
    carried the PortStatsReply decorator while requiring `now`, and every
    live run's all-link recording crashed.)"""

    def test_event_handlers_accept_event_only(self):
        with open(os.path.join(ROOT, "path_switch_controller.py")) as fh:
            tree = ast.parse(fh.read())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            decorated = any('set_ev_cls' in ast.unparse(d)
                            for d in node.decorator_list)
            if not decorated:
                continue
            params = node.args.args[1:]  # drop self
            required = len(params) - len(node.args.defaults)
            if required > 1:
                offenders.append(
                    f'{node.name} line {node.lineno}: Ryu passes only ev, '
                    f'but {required - 1} extra arg(s) are required')
        self.assertEqual(offenders, [])


class InitAttributeOrderTests(unittest.TestCase):
    """__init__ must not load a self.* attribute before assigning it.

    (Regression: the top-notch import-error log read self._topnotch seven
    lines before `self._topnotch = TOPNOTCH_AVAILABLE`, so every live run
    died in __init__ with AttributeError once that log was added.)
    """

    def test_no_use_before_assign_in_init(self):
        with open(os.path.join(ROOT, "path_switch_controller.py")) as fh:
            tree = ast.parse(fh.read())
        cls = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.ClassDef)
                   and n.name == "PathSwitchController")
        class_attrs = {t.id for stmt in cls.body
                       if isinstance(stmt, ast.Assign)
                       for t in stmt.targets if isinstance(t, ast.Name)}
        init = next(n for n in cls.body
                    if isinstance(n, ast.FunctionDef) and n.name == "__init__")
        def ordered(node):
            # ast.walk is breadth-first; iter_child_nodes follows source
            # order, which is what use-before-assign means here.
            yield node
            for child in ast.iter_child_nodes(node):
                yield from ordered(child)
        assigned = set()
        offenders = []
        for node in ordered(init):
            if isinstance(node, ast.Attribute) \
                    and isinstance(node.value, ast.Name) \
                    and node.value.id == "self":
                if isinstance(node.ctx, ast.Store):
                    assigned.add(node.attr)
                elif isinstance(node.ctx, ast.Load) \
                        and node.attr not in assigned \
                        and node.attr not in class_attrs:
                    # Only flag attributes __init__ itself assigns later:
                    # anything else comes from RyuApp/super().__init__.
                    offenders.append(node.attr)
        later = {n.attr for n in ast.walk(init)
                 if isinstance(n, ast.Attribute)
                 and isinstance(n.ctx, ast.Store)
                 and isinstance(n.value, ast.Name) and n.value.id == "self"}
        offenders = sorted({a for a in offenders if a in later})
        self.assertEqual(offenders, [])


class CompletedReportsWiringTests(unittest.TestCase):
    """completed_reports() must have a production call site.

    It is the recovery detail page's data source for finished outages
    (the controller writes a recovery_complete failures.csv row from
    it on link_up). An AST scan keeps it from silently becoming a
    dead API again: some method of the controller must call it.
    """

    def test_controller_calls_completed_reports(self):
        with open(os.path.join(ROOT, "path_switch_controller.py")) as fh:
            tree = ast.parse(fh.read())
        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "completed_reports"]
        self.assertTrue(calls)
