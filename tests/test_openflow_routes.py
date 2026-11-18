"""Wire-command checks for the Ryu-independent route adapter."""

from types import SimpleNamespace
import unittest

from openflow_routes import OpenFlowRouteAdapter


class Parser:
    @staticmethod
    def OFPBarrierRequest(datapath):
        return SimpleNamespace(kind="barrier", datapath=datapath, xid=None)

    @staticmethod
    def OFPFlowMod(**kwargs):
        return SimpleNamespace(kind="flow", xid=None, **kwargs)

    @staticmethod
    def OFPMatch(**kwargs):
        return dict(kwargs)

    @staticmethod
    def OFPActionOutput(port):
        return SimpleNamespace(port=port)

    @staticmethod
    def OFPInstructionActions(action_type, actions):
        return SimpleNamespace(type=action_type, actions=actions)


class Datapath:
    def __init__(self, dpid):
        self.id = dpid
        self.ofproto_parser = Parser
        self.ofproto = SimpleNamespace(
            OFPFC_ADD=0, OFPFC_DELETE_STRICT=4, OFPIT_APPLY_ACTIONS=4,
            OFPP_ANY=0xffffffff, OFPG_ANY=0xffffffff,
        )
        self.sent = []
        self.next_xid = 100
        self.on_send = None

    def set_xid(self, msg):
        self.next_xid += 1
        msg.xid = self.next_xid

    def send_msg(self, msg):
        if self.on_send:
            self.on_send(msg)
        self.sent.append(msg)


class Controller:
    def __init__(self):
        self.datapaths = {i: Datapath(i) for i in (1, 2, 3, 4)}
        self.commits = []
        self.records = []

    def _commit_path(self, *args):
        self.commits.append(args)

    def _record_transition(self, *args):
        self.records.append(args)


class OpenFlowRouteAdapterTests(unittest.TestCase):
    def setUp(self):
        self.controller = Controller()
        self.adapter = OpenFlowRouteAdapter(self.controller)

    def flows(self, dpid):
        return [msg for msg in self.controller.datapaths[dpid].sent if msg.kind == "flow"]

    def clear(self):
        for dp in self.controller.datapaths.values():
            dp.sent = []

    def test_preparation_preserves_ingress_and_registers_every_owner_before_sending(self):
        observed = []
        def check_owners(msg):
            # Every command in this phase has an error owner before the first
            # send, including commands destined for other switches.
            observed.append(msg)
            self.assertEqual(len(self.adapter.message_owners), 9)
            owner = self.adapter.message_owners[self.adapter.key(msg.datapath, msg)]
            self.assertIn(owner, self.adapter.message_owners)
        for dp in self.controller.datapaths.values():
            dp.on_send = check_owners
        keys = self.adapter.prepare("B", 1)
        self.assertEqual({key[0] for key in keys}, {1, 3, 4})
        self.assertEqual(len(observed), 9)
        self.assertEqual(self.flows(2), [])
        for dpid in (1, 4):
            self.assertEqual([msg.match for msg in self.flows(dpid)], [{"in_port": 2}, {"in_port": 3}])
            self.assertTrue(all(msg.instructions[0].actions[0].port == 1 for msg in self.flows(dpid)))
        self.assertEqual([msg.match for msg in self.flows(3)], [{"in_port": 1}, {"in_port": 2}])
        for dpid in (1, 3, 4):
            dp = self.controller.datapaths[dpid]
            self.assertEqual(dp.sent[-1].kind, "barrier")
            self.assertEqual(keys & {self.adapter.key(dp, dp.sent[-1])}, {self.adapter.key(dp, dp.sent[-1])})

    def test_activation_adds_higher_priority_selectors_with_transaction_cookie(self):
        self.adapter.prepare("B", 1)
        self.clear()
        keys = self.adapter.activate("B", 1)
        tx = self.adapter.transactions[1]
        self.assertEqual({key[0] for key in keys}, {1, 4})
        self.assertEqual(self.adapter.current_priority, 10)
        for dpid in (1, 4):
            msg, = self.flows(dpid)
            self.assertEqual(msg.command, self.controller.datapaths[dpid].ofproto.OFPFC_ADD)
            self.assertEqual(msg.match, {"in_port": 1})
            self.assertGreater(msg.priority, tx["old_priority"])
            self.assertEqual(msg.priority, tx["new_priority"])
            self.assertEqual(msg.cookie, tx["cookie"])
            self.assertEqual(msg.instructions[0].actions[0].port, 3)
        self.assertEqual(self.flows(2), [])
        self.assertEqual(self.flows(3), [])

    def test_rollback_restores_old_selectors_and_deletes_only_new_cookie_priority(self):
        self.adapter.prepare("B", 1)
        self.adapter.activate("B", 1)
        self.clear()
        keys = self.adapter.rollback("A", "B", 1)
        tx = self.adapter.transactions[1]
        self.assertEqual({key[0] for key in keys}, {1, 4})
        for dpid in (1, 4):
            old, delete = self.flows(dpid)
            self.assertEqual(old.command, 0)
            self.assertEqual(old.priority, tx["old_priority"])
            self.assertEqual(old.cookie, tx["old_cookie"])
            self.assertEqual(old.instructions[0].actions[0].port, 2)
            self.assertEqual(delete.command, 4)
            self.assertEqual(delete.match, {"in_port": 1})
            self.assertEqual(delete.priority, tx["new_priority"])
            self.assertEqual(delete.cookie, tx["cookie"])
            self.assertEqual(delete.cookie_mask, 0xffffffffffffffff)
            self.assertEqual(delete.out_port, 0xffffffff)
            self.assertEqual(delete.out_group, 0xffffffff)
        self.assertEqual(self.flows(2), [])
        self.assertEqual(self.flows(3), [])
        self.assertEqual(self.controller.commits, [])

    def test_retirement_deletes_only_old_ingress_and_never_transit(self):
        self.adapter.prepare("B", 1)
        self.adapter.activate("B", 1)
        self.adapter.commit("A", "B", 1, "test", {"A": 90, "B": 45})
        self.clear()
        self.adapter.retire("A", 1)
        for dpid in (1, 4):
            msg, = self.flows(dpid)
            self.assertEqual((msg.command, msg.priority, msg.cookie), (4, 10, 0))
            self.assertEqual(msg.cookie_mask, 0xffffffffffffffff)
            self.assertEqual(msg.match, {"in_port": 1})
        self.assertEqual(self.flows(2), [])
        self.assertEqual(self.flows(3), [])
        committed = self.adapter.current_priority
        self.adapter.prepare("A", 2)
        self.assertEqual(self.adapter.transactions[2]["old_priority"], committed)
        self.assertGreater(self.adapter.transactions[2]["new_priority"], committed)

    def test_missing_required_edges_fail_before_any_phase_messages_are_sent(self):
        self.adapter.prepare("B", 1)
        self.clear()
        del self.controller.datapaths[4]
        for method, args in ((self.adapter.activate, ("B", 1)),
                             (self.adapter.rollback, ("A", "B", 1)),
                             (self.adapter.retire, ("A", 1))):
            with self.assertRaisesRegex(RuntimeError, "s4 is disconnected"):
                method(*args)
            self.assertEqual(self.flows(1), [])

    def test_missing_target_transit_fails_preparation_before_sending(self):
        del self.controller.datapaths[3]
        with self.assertRaisesRegex(RuntimeError, "s3 is disconnected"):
            self.adapter.prepare("B", 1)
        self.assertEqual(self.flows(1), [])
        self.assertEqual(self.flows(4), [])

    def test_reconnected_datapath_has_distinct_barrier_and_error_identity(self):
        old_keys = self.adapter.prepare("B", 1)
        old_owner = next(key for key in old_keys if key[0] == 4)
        self.controller.datapaths[4] = Datapath(4)
        keys = self.adapter.activate("B", 1)
        new_owner = next(key for key in keys if key[0] == 4)
        self.assertNotEqual(old_owner[1], new_owner[1])
        self.assertNotEqual(old_owner, new_owner)


if __name__ == "__main__":
    unittest.main()
