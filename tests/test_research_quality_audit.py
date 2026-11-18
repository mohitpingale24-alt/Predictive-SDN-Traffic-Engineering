"""Reject incomplete OpenFlow evidence before declaring a capture verified."""
import unittest

from experiments.run_research_experiment import validate_controller_log, validate_transition_audit


def record(identifier, stage, status):
    return dict(transition_id=str(identifier), stage=stage, status=status)


def complete(identifier=1):
    events = (
        ("transaction", "started"), ("preparing", "started"),
        ("preparing", "confirmed"), ("activating", "started"),
        ("activating", "confirmed"), ("commit", "committed"),
        ("draining", "started"), ("draining", "confirmed"),
        ("cleaning", "started"), ("cleaning", "confirmed"),
        ("transaction", "complete"),
    )
    return [record(identifier, stage, status) for stage, status in events]


class ControllerLogAuditTests(unittest.TestCase):
    def test_transport_and_audit_error_markers_are_case_insensitive(self):
        for marker in ("OPENFLOW ERROR type=3 code=0", "transition AUDIT failed: disk full",
                       "Error Sending packet", "Prediction FAILED", "Traceback (Most Recent Call Last)"):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "Controller error"):
                validate_controller_log("Switch connected\n" + marker + "\n")

    def test_ordinary_safe_transition_logs_pass(self):
        validate_controller_log("Switch connected: s1\nTransition 1 commit committed: both ingress changes acknowledged\n")


class TransitionAuditTests(unittest.TestCase):
    def setUp(self):
        self.ready = [record(0, "controller", "ready")]

    def test_ready_without_actual_transitions_is_valid_low_load_capture(self):
        self.assertEqual(validate_transition_audit(self.ready),
                         dict(completed_transactions=0, aborted_transitions=0))

    def test_complete_acknowledged_transactions_pass(self):
        result = validate_transition_audit(self.ready + complete(1) + complete(2))
        self.assertEqual(result["completed_transactions"], 2)

    def test_preparing_draining_and_cleaning_transactions_are_incomplete(self):
        for stop in (2, 7, 9, 10):
            with self.subTest(stop=stop), self.assertRaisesRegex(ValueError, "Incomplete"):
                validate_transition_audit(self.ready + complete()[:stop])

    def test_completed_marker_cannot_replace_missing_commit_ack(self):
        events = [row for row in complete() if (row["stage"], row["status"]) != ("commit", "committed")]
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            validate_transition_audit(self.ready + events)

    def test_duplicate_starts_and_orphan_completions_are_rejected(self):
        cases = (complete() + [record(1, "transaction", "started")],
                 [record(1, "transaction", "complete")])
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "Incomplete|duplicate"):
                validate_transition_audit(self.ready + rows)

    def test_failed_rolled_back_and_committed_degraded_captures_are_excluded(self):
        for status in ("failed", "aborted", "degraded", "indeterminate"):
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "transition"):
                validate_transition_audit(self.ready + complete() + [record(1, "transaction", status)])

    def test_completion_before_commit_and_activity_after_completion_are_rejected(self):
        wrong_order = [record(1, "transaction", "started"),
                       record(1, "transaction", "complete"), record(1, "commit", "committed")]
        for rows in (wrong_order, complete() + [record(1, "draining", "started")]):
            with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, "ordering"):
                validate_transition_audit(self.ready + rows)

    def test_missing_ready_malformed_identifiers_and_bad_reserved_id_are_rejected(self):
        for rows in (complete(), [], [record("bad", "transaction", "started")],
                     [record(0, "transaction", "started")],
                     self.ready + [record(-1, "transaction", "started")]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_transition_audit(rows)


if __name__ == "__main__":
    unittest.main()
