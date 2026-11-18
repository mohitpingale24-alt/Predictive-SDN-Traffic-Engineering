"""Training-feature windowing: lags are seconds, not rows.

Guards the closing-batch fix in cnt-ml/src/build_features.py: feature
windows are built per contiguous segment of one link's series, so a
window can never span a sampling gap or the seam where two captures
were concatenated — mirroring the runtime FeatureHistory, which
restarts on any gap (SDN_CONTIGUOUS_HISTORY defaults on).
"""
from pathlib import Path
import sys
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cnt-ml" / "src"))

from build_features import (attach_time_history, attach_timestamp_target,
                            contiguous_segments)


def build(df):
    """The main() pipeline, minus file IO: segment, target, history, drop."""
    processed = []
    for _link_id, group in df.groupby("link_id"):
        for segment in contiguous_segments(group):
            segment = attach_timestamp_target(segment)
            segment = attach_time_history(segment)
            processed.append(segment)
    return pd.concat(processed, ignore_index=True).dropna().reset_index(drop=True)


def series(stamps, utilization):
    return pd.DataFrame({
        "timestamp": list(stamps),
        "link_id": "S1-S2",
        "utilization": [float(utilization)] * len(stamps),
    })


class ContiguousSegmentTests(unittest.TestCase):
    def test_gap_free_series_is_one_segment(self):
        group = series(range(1000, 1060), 10.0)
        self.assertEqual(len(contiguous_segments(group)), 1)

    def test_windows_never_cross_a_concatenation_seam(self):
        # Run A all 10%, run B all 90%, joined with a 10 s seam gap —
        # the shape pool_telemetry.py produces from separate captures.
        run_a = series(range(1000, 1060), 10.0)
        run_b = series(range(1070, 1130), 90.0)
        out = build(pd.concat([run_a, run_b], ignore_index=True))
        run_b_rows = out[out["timestamp"] >= 1070]
        # B's first 10 s need history from across the seam: dropped,
        # never filled with A's 10% samples.
        self.assertTrue((run_b_rows["timestamp"] >= 1080).all())
        self.assertTrue((run_b_rows["util_lag_1"] == 90.0).all())
        self.assertTrue((run_b_rows["util_lag_10"] == 90.0).all())
        run_a_rows = out[out["timestamp"] < 1060]
        self.assertTrue((run_a_rows["util_lag_10"] == 10.0).all())

    def test_windows_never_span_a_sampling_gap(self):
        stamps = [t for t in range(2000, 2060) if t not in (2030, 2031, 2032)]
        out = build(series(stamps, 50.0))
        kept = set(out["timestamp"])
        # 2033's 1 s lag is a missing sample; 2042's 10 s window still
        # touches the gap; 2043 is the first fully clean row after it.
        self.assertNotIn(2033, kept)
        self.assertNotIn(2042, kept)
        self.assertIn(2043, kept)


if __name__ == "__main__":
    unittest.main()
