"""OpenFlow adapter for acknowledged, aggregate path transitions.

No Ryu imports here: the controller supplies datapaths and message factories.
Selectors steer the shared traffic bundle, not independently optimized flows.
Transit rules remain installed for both paths; only obsolete ingress selectors
are retired. Barriers acknowledge ordered operations, not cross-switch atomicity.
"""


class OpenFlowRouteAdapter:
    def __init__(self, controller):
        self.controller = controller
        self.current_priority = 10
        self.next_priority = 20
        self.transactions = {}
        self.message_owners = {}

    @staticmethod
    def key(datapath, message):
        return (datapath.id, id(datapath), message.xid)

    def _datapaths(self, identifiers):
        result = []
        for identifier in identifiers:
            datapath = self.controller.datapaths.get(identifier)
            if datapath is None:
                raise RuntimeError('Required switch s%s is disconnected' % identifier)
            result.append(datapath)
        return result

    def _phase(self, requests):
        """Assign all IDs/ownership before sending any phase messages."""
        keys, outgoing = set(), []
        for datapath, messages in requests:
            barrier = datapath.ofproto_parser.OFPBarrierRequest(datapath)
            datapath.set_xid(barrier)
            owner = self.key(datapath, barrier)
            keys.add(owner)
            for message in messages:
                datapath.set_xid(message)
                self.message_owners[self.key(datapath, message)] = owner
            self.message_owners[owner] = owner
            outgoing.append((datapath, messages + [barrier]))
        for datapath, messages in outgoing:
            for message in messages:
                datapath.send_msg(message)
        return keys

    def _flow(self, datapath, priority, in_port, out_port, cookie=0):
        parser, ofp = datapath.ofproto_parser, datapath.ofproto
        actions = [parser.OFPActionOutput(out_port)]
        return parser.OFPFlowMod(
            datapath=datapath, cookie=cookie, command=ofp.OFPFC_ADD,
            priority=priority, match=parser.OFPMatch(in_port=in_port),
            instructions=[parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)],
        )

    def _delete(self, datapath, priority, cookie):
        parser, ofp = datapath.ofproto_parser, datapath.ofproto
        return parser.OFPFlowMod(
            datapath=datapath, command=ofp.OFPFC_DELETE_STRICT,
            cookie=cookie, cookie_mask=0xffffffffffffffff,
            priority=priority, match=parser.OFPMatch(in_port=1),
            out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
        )

    def prepare(self, path, transition_id):
        switches = self._datapaths((1, 4, 2 if path == 'A' else 3))
        self.next_priority += 1
        if self.next_priority >= 65000:
            raise RuntimeError('Ingress selector priority exhausted; restart controller')
        self.transactions[transition_id] = {
            'old_priority': self.current_priority,
            'old_cookie': getattr(self, 'current_cookie', 0),
            'new_priority': self.next_priority,
            'cookie': 0x53444e0000000000 | self.next_priority,
        }
        requests = []
        for datapath in switches:
            if datapath.id in (1, 4):
                messages = [self._flow(datapath, 10, port, 1) for port in (2, 3)]
            else:
                messages = [self._flow(datapath, 10, 1, 2), self._flow(datapath, 10, 2, 1)]
            requests.append((datapath, messages))
        return self._phase(requests)

    def activate(self, path, transition_id):
        transaction = self.transactions[transition_id]
        port = 2 if path == 'A' else 3
        return self._phase([
            (datapath, [self._flow(datapath, transaction['new_priority'], 1,
                                   port, transaction['cookie'])])
            for datapath in self._datapaths((1, 4))
        ])

    def retire(self, old_path, transition_id):
        transaction = self.transactions[transition_id]
        return self._phase([
            (datapath, [self._delete(datapath, transaction['old_priority'],
                                     transaction['old_cookie'])])
            for datapath in self._datapaths((1, 4))
        ])

    def rollback(self, old_path, new_path, transition_id):
        transaction = self.transactions[transition_id]
        # ADD restores the known old selector even if a switch reconnected;
        # delete only our staged replacement. Shared transit rules are retained.
        port = 2 if old_path == 'A' else 3
        return self._phase([
            (datapath, [self._flow(datapath, transaction['old_priority'], 1, port,
                                  transaction['old_cookie']),
                        self._delete(datapath, transaction['new_priority'],
                                     transaction['cookie'])])
            for datapath in self._datapaths((1, 4))
        ])

    def commit(self, old_path, new_path, transition_id, reason, signals):
        transaction = self.transactions[transition_id]
        self.controller._commit_path(old_path, new_path, reason, signals)
        self.current_priority = transaction['new_priority']
        self.current_cookie = transaction['cookie']

    def record(self, transition_id, stage, status, old_path, new_path, detail):
        self.controller._record_transition(transition_id, stage, status,
                                           old_path, new_path, detail)
