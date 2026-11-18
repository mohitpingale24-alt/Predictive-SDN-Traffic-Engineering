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


_PROTOCOL_NUMBERS = {"tcp": 6, "udp": 17, "icmp": 1}


def connection_match_fields(in_port=None, eth_src=None, eth_dst=None,
                            ip_src=None, ip_dst=None, protocol=None,
                            src_port=None, dst_port=None, dscp=None):
    """OFPMatch kwargs for exactly one connection, never a host pair.

    Access-switch flows (learned forwarding, ACL drops) must be scoped to
    the connection that was actually policy-checked: a flow keyed only on
    MAC addresses silently forwards — or blocks — every later connection
    between the same hosts without a policy decision. Fields left as None
    are omitted, so an IP-pair scope (slice isolation) is expressed by
    passing only the addresses, and a full 5-tuple scope by passing the
    protocol and ports as well.
    """
    fields = {}
    if in_port is not None:
        fields["in_port"] = in_port
    if eth_src is not None:
        fields["eth_src"] = eth_src
    if eth_dst is not None:
        fields["eth_dst"] = eth_dst
    if ip_src is None and ip_dst is None:
        return fields
    fields["eth_type"] = 0x0800
    if ip_src is not None:
        fields["ipv4_src"] = ip_src
    if ip_dst is not None:
        fields["ipv4_dst"] = ip_dst
    if dscp is not None:
        fields["ip_dscp"] = int(dscp)
    proto = None
    if isinstance(protocol, int):
        proto = protocol
    elif protocol is not None:
        proto = _PROTOCOL_NUMBERS.get(str(protocol).lower())
    if proto is not None:
        fields["ip_proto"] = proto
        if src_port is not None:
            if proto == 6:
                fields["tcp_src"] = int(src_port)
            elif proto == 17:
                fields["udp_src"] = int(src_port)
        if dst_port is not None:
            if proto == 6:
                fields["tcp_dst"] = int(dst_port)
            elif proto == 17:
                fields["udp_dst"] = int(dst_port)
    return fields
