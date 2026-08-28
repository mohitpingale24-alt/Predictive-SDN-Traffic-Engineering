#!/usr/bin/env python3
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub

PATH_FILE = '/tmp/active_path'
SWITCH_NAMES = {1: 's1', 2: 's2', 3: 's3', 4: 's4'}
S1_PATH_PORT = {'A': 2, 'B': 3}
S4_PATH_PORT = {'A': 2, 'B': 3}


class PathSwitchController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PathSwitchController, self).__init__(*args, **kwargs)
        self.active_path = 'A'
        self.datapaths = {}
        with open(PATH_FILE, 'w') as f:
            f.write(self.active_path)
        self.monitor_thread = hub.spawn(self._monitor_path_file)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        dpid = datapath.id
        self.datapaths[dpid] = datapath
        name = SWITCH_NAMES.get(dpid, dpid)
        self.logger.info("Switch connected: %s (dpid=%s)", name, dpid)
        self.add_flow(datapath, 0, self._match(datapath), self._actions_output(datapath, datapath.ofproto.OFPP_CONTROLLER))
        if dpid == 1:
            self._install_s1_flows(datapath)
        elif dpid == 4:
            self._install_s4_flows(datapath)
        elif dpid in (2, 3):
            self._install_passthrough_flows(datapath)

    def _install_s1_flows(self, datapath):
        out_port = S1_PATH_PORT[self.active_path]
        self.add_flow(datapath, 10, self._match(datapath, in_port=1), self._actions_output(datapath, out_port))
        self.add_flow(datapath, 10, self._match(datapath, in_port=2), self._actions_output(datapath, 1))
        self.add_flow(datapath, 10, self._match(datapath, in_port=3), self._actions_output(datapath, 1))

    def _install_s4_flows(self, datapath):
        out_port = S4_PATH_PORT[self.active_path]
        self.add_flow(datapath, 10, self._match(datapath, in_port=1), self._actions_output(datapath, out_port))
        self.add_flow(datapath, 10, self._match(datapath, in_port=2), self._actions_output(datapath, 1))
        self.add_flow(datapath, 10, self._match(datapath, in_port=3), self._actions_output(datapath, 1))

    def _install_passthrough_flows(self, datapath):
        self.add_flow(datapath, 10, self._match(datapath, in_port=1), self._actions_output(datapath, 2))
        self.add_flow(datapath, 10, self._match(datapath, in_port=2), self._actions_output(datapath, 1))

    def _match(self, datapath, **kwargs):
        parser = datapath.ofproto_parser
        return parser.OFPMatch(**kwargs)

    def _actions_output(self, datapath, port):
        parser = datapath.ofproto_parser
        return [parser.OFPActionOutput(port)]

    def add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, priority=priority, match=match, instructions=inst)
        datapath.send_msg(mod)

    def modify_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=datapath, command=ofproto.OFPFC_MODIFY_STRICT, priority=priority, match=match, instructions=inst)
        datapath.send_msg(mod)

    def _monitor_path_file(self):
        while True:
            hub.sleep(2)
            try:
                with open(PATH_FILE, 'r') as f:
                    requested = f.read().strip().upper()
            except FileNotFoundError:
                continue
            if requested in ('A', 'B') and requested != self.active_path:
                self.logger.info("Path change requested: %s -> %s", self.active_path, requested)
                self.switch_path(requested)

    def switch_path(self, new_path):
        self.active_path = new_path
        s1 = self.datapaths.get(1)
        s4 = self.datapaths.get(4)
        if s1:
            out_port = S1_PATH_PORT[new_path]
            self.modify_flow(s1, 10, self._match(s1, in_port=1), self._actions_output(s1, out_port))
            self.logger.info("s1 updated: h1 traffic now -> port %s (%s)", out_port, 's2' if out_port == 2 else 's3')
        if s4:
            out_port = S4_PATH_PORT[new_path]
            self.modify_flow(s4, 10, self._match(s4, in_port=1), self._actions_output(s4, out_port))
            self.logger.info("s4 updated: h2 traffic now -> port %s (%s)", out_port, 's2' if out_port == 2 else 's3')
        self.logger.info("*** Active path is now: %s ***", new_path)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        in_port = msg.match['in_port']
        self.logger.info("Packet reached controller (unmatched): dpid=%s in_port=%s", dpid, in_port)
        
