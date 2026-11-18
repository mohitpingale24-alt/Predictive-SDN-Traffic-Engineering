import csv
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "cnt-ml", "src"))
sys.path.insert(0, os.path.join(ROOT, "experiments"))

from link_map import (canonical_reporters, link_capacity_mbps,  # noqa: E402
                      links_in_map, load_link_map)
from pool_telemetry import pool_rows  # noqa: E402
import gnn_model  # noqa: E402


class LinkMapTests(unittest.TestCase):
    def test_default_map_covers_core_and_aggregation_links(self):
        self.assertEqual(links_in_map(load_link_map()),
                         ["S1-S2", "S1-S3", "S2-S4", "S3-S4", "S5-S1", "S6-S4"])

    def test_one_canonical_reporter_per_link_smallest_endpoint(self):
        link_map = load_link_map()
        reporters = canonical_reporters(link_map)
        self.assertEqual(len(reporters), 6)
        self.assertIn((1, 2), reporters)      # S1-S2 from s1, not s2
        self.assertIn((2, 2), reporters)      # S2-S4 from s2, not s4
        self.assertNotIn((2, 1), reporters)
        self.assertNotIn((4, 2), reporters)

    def test_json_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"9:1": {"link_id": "S9-S8", "path": None}}, handle)
            link_map = load_link_map(path)
        self.assertEqual(links_in_map(link_map), ["S9-S8"])
        self.assertEqual(canonical_reporters(link_map), {(9, 1)})

    def test_capacity_from_path_else_default(self):
        caps = {"A": 100.0, "B": 40.0}
        self.assertEqual(link_capacity_mbps({"path": "B"}, caps, 10.0), 40.0)
        self.assertEqual(link_capacity_mbps({"path": None}, caps, 10.0), 10.0)
        self.assertEqual(link_capacity_mbps({"path": "Z"}, caps, 10.0), 10.0)


def _write_capture(directory, start, seconds=5):
    os.makedirs(directory)
    links = ["S1-S2", "S2-S4", "S1-S3", "S3-S4"]
    with open(os.path.join(directory, "telemetry_all_links.csv"), "w",
              newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "link_id", "utilization", "throughput",
                         "delay", "packet_loss", "active_flows"])
        for t in range(start, start + seconds):
            for i, link in enumerate(links):
                writer.writerow([t, link, 10.0 + i, 1.0, "", 0.0, 0])


class PoolingTests(unittest.TestCase):
    def test_pool_offsets_runs_without_collisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "run-01")
            second = os.path.join(tmp, "run-02")
            _write_capture(first, 1000)
            _write_capture(second, 1000)  # same wall-clock stamps
            rows, counts = pool_rows([first, second])
        self.assertEqual(counts[first], 20)
        self.assertEqual(counts[second], 20)
        stamps = sorted({row["timestamp"] for row in rows})
        self.assertEqual(len(stamps), 10)  # 5 + 5 distinct seconds, no overlap
        self.assertEqual(stamps, sorted(stamps))

    def test_pooled_four_link_series_builds_real_line_graph(self):
        with tempfile.TemporaryDirectory() as tmp:
            dirs = []
            for index in range(6):
                directory = os.path.join(tmp, "run-%02d" % index)
                _write_capture(directory, 5000, seconds=30)
                dirs.append(directory)
            rows, _ = pool_rows(dirs)
            pooled_path = os.path.join(tmp, "pooled.csv")
            with open(pooled_path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            X, _Y, link_ids = gnn_model.build_dataset(pooled_path,
                                                      horizon_steps=5)
        self.assertEqual(link_ids, ["S1-S2", "S1-S3", "S2-S4", "S3-S4"])
        adjacency = gnn_model.line_graph_adjacency(link_ids)
        by_id = {link: i for i, link in enumerate(link_ids)}
        # Line graph of the diamond: S1-S2 touches S1-S3 (at s1) and S2-S4 (at s2)
        self.assertEqual(adjacency[by_id["S1-S2"]][by_id["S1-S3"]], 1.0)
        self.assertEqual(adjacency[by_id["S1-S2"]][by_id["S2-S4"]], 1.0)
        self.assertEqual(adjacency[by_id["S1-S2"]][by_id["S3-S4"]], 0.0)
        self.assertGreater(X.shape[0], 100)


if __name__ == "__main__":
    unittest.main()
