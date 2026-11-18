import ast
import importlib.util
from pathlib import Path
import unittest
from telemetry_runtime import PREDICTION_FIELDS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('dashboard', ROOT / 'frontend/server.py')
dashboard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dashboard)

class DashboardTests(unittest.TestCase):
    def test_prediction_log_branches_supply_full_schema(self):
        tree = ast.parse((ROOT / 'path_switch_controller.py').read_text())
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'append':
                owner = node.func.value
                if isinstance(owner, ast.Attribute) and owner.attr == 'prediction_recorder':
                    keys = {key.value for key in node.args[0].keys}
                    self.assertEqual(keys, set(PREDICTION_FIELDS))
                    count += 1
        self.assertEqual(count, 4)

    def test_unknown_run_cannot_escape_capture_directory(self):
        for system, run in [('static', '../../etc/passwd'), ('../static', 'run-01'), ('static', 'run-99')]:
            with self.assertRaises(ValueError): dashboard.run_data(system, run)

    def test_recorded_run_uses_real_metrics_and_events(self):
        run = dashboard.run_data('predictive-ml', 'run-01')
        self.assertTrue(run['verified'])
        self.assertFalse(run['live'])
        self.assertGreater(len(run['points']), 150)
        self.assertEqual(len(run['events']), 2)
        self.assertEqual(run['summary']['Congestion_Duration_seconds'], 0)

    def test_json_serialization_converts_missing_values(self):
        self.assertEqual(dashboard.safe({'value': float('nan'), 'items': [float('inf'), 1]}), {'value': None, 'items': [None, 1]})

    def test_final_catalog_matches_saved_statistics(self):
        import csv
        state = dashboard.catalog()
        self.assertTrue(state['complete'])
        self.assertEqual(state['verified'], 32)
        self.assertEqual(state['issues'], [])
        with (dashboard.CAMPAIGN / 'final-analysis/statistics/network_statistics.csv').open() as handle:
            saved = {(row['System'], row['Metric']): row for row in csv.DictReader(handle)}
        for row in state['stats']:
            expected = saved[row['system'], row['metric']]
            self.assertEqual(row['n'], int(expected['N']))
            if row['mean'] is not None:
                self.assertAlmostEqual(row['mean'], float(expected['Mean']))
                self.assertAlmostEqual(row['low'], float(expected['CI95_Lower']))
                self.assertAlmostEqual(row['high'], float(expected['CI95_Upper']))

    def test_all_verified_captures_are_replayable(self):
        for system in dashboard.SYSTEMS:
            for index in range(1, 9):
                with self.subTest(system=system, index=index):
                    run = dashboard.run_data(system, f'run-{index:02d}')
                    self.assertTrue(run['verified'])
                    self.assertFalse(run['live'])
                    self.assertGreater(run['duration'], 90)
                    self.assertEqual(run['summary']['System'], system)
