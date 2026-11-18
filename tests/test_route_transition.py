import unittest

from route_transition import SafeRouteTransition


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Adapter:
    def __init__(self):
        self.calls = []
        self.records = []
        self.commits = []
        self.keys = {
            "prepare": {(2, "connection-2", 11), (4, "connection-4", 12)},
            "activate": {(1, "connection-1", 21), (4, "connection-4", 22)},
            "retire": {(1, "connection-1", 31), (4, "connection-4", 32)},
            "rollback": {(1, "connection-1", 41), (4, "connection-4", 42)},
        }
        self.early = None

    def phase(self, name, *args):
        self.calls.append((name, args))
        if self.early:
            self.early(name, self.keys[name])
        return set(self.keys[name])

    def prepare(self, path, tid):
        return self.phase("prepare", path, tid)

    def activate(self, path, tid):
        return self.phase("activate", path, tid)

    def retire(self, path, tid):
        return self.phase("retire", path, tid)

    def rollback(self, old, new, tid):
        return self.phase("rollback", old, new, tid)

    def commit(self, *args):
        self.commits.append(args)

    def record(self, *args):
        self.records.append(args)


class SafeRouteTransitionTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.adapter = Adapter()
        self.machine = SafeRouteTransition(self.adapter, clock=self.clock)

    def start(self):
        return self.machine.start("A", "B", 1000.0, "forecast", {"A": 90, "B": 45})

    def acknowledge(self, phase):
        for key in sorted(self.adapter.keys[phase]):
            self.machine.barrier(key)

    def commit(self):
        self.start()
        self.acknowledge("prepare")
        self.acknowledge("activate")

    def test_both_phases_confirm_before_publish_and_old_rules_drain(self):
        self.assertTrue(self.start())
        self.assertTrue(self.machine.busy)
        self.assertFalse(self.start())
        self.assertEqual(self.machine.transition_id, 1)
        # Future-phase replies and wrong connection identities do not count.
        self.assertFalse(self.machine.barrier((1, "connection-1", 21)))
        self.assertFalse(self.machine.barrier((2, "previous-connection", 11)))
        first, second = sorted(self.adapter.keys["prepare"])
        self.machine.barrier(second)
        self.assertFalse(self.machine.barrier(second))
        self.assertEqual(self.machine.state, "preparing")
        self.machine.barrier(first)
        self.assertEqual(self.machine.state, "activating")
        first, second = sorted(self.adapter.keys["activate"])
        self.machine.barrier(first)
        self.assertEqual(self.adapter.commits, [])
        self.machine.barrier(second)
        self.assertEqual(len(self.adapter.commits), 1)
        self.assertTrue(self.machine.committed)
        self.assertEqual(self.machine.state, "draining")
        self.clock.now = .49
        self.machine.tick()
        self.assertEqual(self.machine.state, "draining")
        self.clock.now = .5
        self.machine.tick()
        self.assertEqual(self.machine.state, "cleaning")
        self.acknowledge("retire")
        self.assertEqual(self.machine.state, "idle")
        self.assertFalse(self.machine.busy)
        self.assertEqual([call[0] for call in self.adapter.calls], ["prepare", "activate", "retire"])
        self.assertTrue(self.start())
        self.assertEqual(self.machine.transition_id, 2)

    def test_prepare_error_rolls_back_without_publishing(self):
        self.start()
        failed = next(iter(self.adapter.keys["prepare"]))
        self.assertTrue(self.machine.error(failed, "invalid output port"))
        self.assertEqual(self.machine.state, "rolling_back")
        self.assertFalse(self.machine.barrier(failed))
        self.acknowledge("rollback")
        self.assertFalse(self.machine.busy)
        self.assertFalse(self.machine.committed)
        self.assertEqual(self.adapter.commits, [])
        self.assertNotIn("activate", [call[0] for call in self.adapter.calls])

    def test_activation_timeout_restores_all_ingress_even_with_partial_ack(self):
        self.start()
        self.acknowledge("prepare")
        self.machine.barrier(next(iter(self.adapter.keys["activate"])))
        self.clock.now = 3
        self.machine.tick()
        self.assertEqual(self.machine.state, "rolling_back")
        self.assertEqual(self.adapter.commits, [])
        self.acknowledge("rollback")
        self.assertEqual(self.machine.state, "idle")

    def test_unconfirmed_rollback_locks_new_decisions(self):
        self.start()
        self.acknowledge("prepare")
        self.machine.error(next(iter(self.adapter.keys["activate"])), "flow rejected")
        self.machine.barrier(next(iter(self.adapter.keys["rollback"])))
        self.clock.now = 3
        self.machine.tick()
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertTrue(self.machine.busy)
        self.assertFalse(self.start())
        self.assertEqual(self.adapter.commits, [])

    def test_partial_rollback_coverage_is_indeterminate(self):
        self.adapter.keys["rollback"] = {(1, "connection-1", 41)}
        self.start()
        self.acknowledge("prepare")
        self.machine.error((4, "connection-4", 22), "flow rejected")
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertIn("every activated ingress", self.machine.last_error)

    def test_disconnect_during_activation_does_not_publish_partial_commit(self):
        self.start()
        self.acknowledge("prepare")
        self.machine.barrier((1, "connection-1", 21))
        self.adapter.keys["rollback"] = {(1, "connection-1", 41)}
        self.machine.disconnect(4)
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertEqual(self.adapter.commits, [])

    def test_cleanup_timeout_preserves_committed_route_and_locks_decisions(self):
        self.commit()
        self.clock.now = .5
        self.machine.tick()
        self.clock.now = 3.5
        self.machine.tick()
        self.assertTrue(self.machine.committed)
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertNotIn("rollback", [call[0] for call in self.adapter.calls])
        self.assertTrue(any(row[2] == "degraded" for row in self.adapter.records))
        self.assertFalse(any(row[2] == "aborted" for row in self.adapter.records))

    def test_disconnect_after_commit_preserves_commit_and_locks_decisions(self):
        self.commit()
        self.assertFalse(self.machine.disconnect(99))
        self.machine.disconnect(4)
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertTrue(self.machine.committed)
        self.assertEqual(len(self.adapter.commits), 1)

    def test_callbacks_during_adapter_call_are_replayed_after_registration(self):
        def early(name, keys):
            if name in ("prepare", "activate"):
                for key in keys:
                    self.machine.barrier(key)
        self.adapter.early = early
        self.start()
        self.assertEqual(self.machine.state, "draining")
        self.assertEqual(len(self.adapter.commits), 1)

    def test_error_during_adapter_call_cannot_be_masked_by_barrier_reply(self):
        def early(name, keys):
            if name == "prepare":
                first = next(iter(keys))
                self.machine.error(first, "preparation rejected")
                for key in keys:
                    self.machine.barrier(key)
        self.adapter.early = early
        self.start()
        self.assertEqual(self.machine.state, "rolling_back")
        self.assertEqual(self.adapter.commits, [])

    def test_prepare_timeout_and_rollback_error_remain_uncommitted(self):
        self.start()
        self.clock.now = 3
        self.machine.tick()
        self.assertEqual(self.machine.state, "rolling_back")
        self.machine.error(next(iter(self.adapter.keys["rollback"])), "rollback rejected")
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertFalse(self.machine.committed)

    def test_commit_publication_error_does_not_rollback_acknowledged_ingress(self):
        def broken_commit(*args):
            self.adapter.commits.append(args)
            raise OSError("event log disk failure")
        self.adapter.commit = broken_commit
        self.commit()
        self.assertTrue(self.machine.committed)
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertNotIn("rollback", [call[0] for call in self.adapter.calls])
        self.assertTrue(any(row[1:3] == ("commit", "committed") for row in self.adapter.records))
        self.assertTrue(any(row[2] == "degraded" for row in self.adapter.records))

    def test_disconnect_during_commit_callback_cannot_unlock_failed_transaction(self):
        self.adapter.commit = lambda *args: self.machine.disconnect(4)
        self.commit()
        self.assertEqual(self.machine.state, "indeterminate")
        self.assertTrue(self.machine.committed)
        self.clock.now = 20
        self.machine.tick()
        self.assertEqual(self.machine.state, "indeterminate")

    def test_empty_barrier_set_cannot_confirm_preparation(self):
        self.adapter.keys["prepare"] = set()
        self.start()
        self.assertEqual(self.machine.state, "rolling_back")
        self.assertEqual(self.adapter.commits, [])
        self.assertNotIn("activate", [call[0] for call in self.adapter.calls])


if __name__ == "__main__":
    unittest.main()
