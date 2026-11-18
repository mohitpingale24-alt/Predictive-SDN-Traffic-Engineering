"""Check transition measurements against known small packet cohorts."""
import csv
import json
from pathlib import Path
import tempfile
import unittest

from experiments.transition_probe import HEADER, encode_packet, decode_packet
from experiments.transition_metrics import window_metrics, committed_reroutes, summarize_transitions


def rows(path, data):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]))
        writer.writeheader()
        writer.writerows(data)


class TransitionMetricsTests(unittest.TestCase):
    def test_probe_binary_roundtrip_and_corruption(self):
        packet = encode_packet(3, 123, 1700000000123456789, 128)
        self.assertEqual(len(packet), 128)
        self.assertEqual(decode_packet(packet), (3, 123, 1700000000123456789))
        with self.assertRaises(ValueError):
            decode_packet(b"wrong")
        with self.assertRaises(ValueError):
            encode_packet(1, 1, 1, HEADER.size - 1)

    def test_packet_cohort_counts_late_delivery_once_and_missing_is_loss(self):
        tx = [dict(flow_id=1, sequence=i, tx_timestamp=10 + i * .5, payload_bytes=128) for i in range(1, 5)]
        # A queued packet delivered beyond window is still delivered; duplicates
        # do not hide the genuinely missing sequence3.
        rx = [dict(tx[i], rx_timestamp=tx[i]["tx_timestamp"] + .01) for i in (0, 1)]
        rx += [dict(tx[1], rx_timestamp=11.1), dict(tx[3], rx_timestamp=13.5)]
        measurement = window_metrics(tx, rx, [], [], 10, 12.1, [1])
        self.assertEqual(measurement["Probe_Packets_Queued"], 4)
        self.assertEqual(measurement["Probe_Packets_Delivered"], 3)
        self.assertEqual(measurement["Probe_Lost_Packets"], 1)
        self.assertEqual(measurement["Probe_Loss_pct"], 25)
        self.assertEqual(measurement["Probe_Receiver_Delivered_In_Window"], 2)
        self.assertEqual(measurement["Probe_Duplicate_Receives_In_Window"], 1)
        self.assertIsNone(measurement["P95_RTT_ms"])
        self.assertIsNone(measurement["Iperf_Receiver_Window_Goodput_Mbps"])

    def test_concurrent_goodput_is_sum_of_receiver_bytes_over_window_time(self):
        intervals = [dict(flow_id=flow, start_timestamp=0, end_timestamp=1, bytes=125000)
                     for flow in (1, 2, 3)]
        measure = window_metrics([], [], [], intervals, 0, 1, [1, 2, 3])
        self.assertEqual(measure["Iperf_Receiver_Window_Goodput_Mbps"], 3)
        self.assertIsNone(measure["Probe_Loss_pct"])
        self.assertFalse(measure["Window_Complete"])

    def test_interarrival_variation_subtracts_sender_spacing_and_uses_rtt(self):
        tx = [dict(flow_id=1, sequence=i, tx_timestamp=10 + i * .02, payload_bytes=128) for i in range(1, 5)]
        rx = [dict(row, rx_timestamp=row["tx_timestamp"] + .005 + index * .001) for index, row in enumerate(tx)]
        ping = [dict(flow_id=1, timestamp=10.05, rtt_ms=12), dict(flow_id=1, timestamp=10.07, rtt_ms=16)]
        measure = window_metrics(tx, rx, ping, [], 10, 11, [1])
        self.assertAlmostEqual(measure["Probe_Mean_Abs_Interarrival_Variation_ms"], 1)
        self.assertEqual(measure["Average_RTT_ms"], 14)
        self.assertEqual(measure["RTT_Samples"], 2)
        self.assertAlmostEqual(measure["P95_RTT_ms"], 15.8)

    def test_only_committed_reroutes_use_acknowledgment_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows(root / "control_events.csv", [dict(event="controller_start", timestamp=1, old_path="", new_path="A", reason="start"),
                                                dict(event="reroute", timestamp=10, old_path="A", new_path="B", reason="congestion")])
            rows(root / "transitions.csv", [dict(timestamp=10.03, transition_id="tx-1", stage="commit", status="committed", old_path="A", new_path="B", detail="barriers received"),
                                           dict(timestamp=12, transition_id="tx-2", stage="prepare", status="aborted", old_path="B", new_path="A", detail="disconnected")])
            result = committed_reroutes(root)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["timestamp"], 10.03)
            self.assertTrue(result[0]["commit_confirmed"])
            self.assertEqual(result[0]["transition_id"], "tx-1")


if __name__ == "__main__":
    unittest.main()
