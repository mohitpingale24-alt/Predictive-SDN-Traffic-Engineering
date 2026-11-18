
import os
import sys
import time

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, DEAD_DISPATCHER, MAIN_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib import hub
from ryu.lib.packet import packet, ethernet

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
ML_SRC = os.path.join(PROJECT_ROOT, 'cnt-ml', 'src')
if ML_SRC not in sys.path:
    sys.path.insert(0, ML_SRC)

from predict import get_model_info, predict_future_utilization
from confidence import ConfidenceEstimator
from confidence_policy import select_signals
from openflow_routes import OpenFlowRouteAdapter
from route_transition import SafeRouteTransition
from telemetry_runtime import (
    CONTROL_EVENT_FIELDS,
    PREDICTION_FIELDS,
    FeatureHistory,
    PortRateTracker,
    RecentRTT,
    ReroutePolicy,
    StructuredCsvRecorder,
    TelemetryCsvRecorder,
    project_path_signals,
)

PATH_FILE = '/tmp/active_path'
SWITCH_NAMES = {1: 's1', 2: 's2', 3: 's3', 4: 's4', 5: 's5', 6: 's6'}
S1_PATH_PORT = {'A': 2, 'B': 3}
S4_PATH_PORT = {'A': 2, 'B': 3}
S1_PORT_LINK = {2: ('A', 'S1-S2'), 3: ('B', 'S1-S3')}
MONITOR_INTERVAL_SECONDS = float(os.getenv('SDN_MONITOR_INTERVAL_SECONDS', '1'))
LINK_CAPACITY_MBPS = float(os.getenv('SDN_LINK_CAPACITY_MBPS', '10'))
PATH_CAPACITY_MBPS = {
    'A': float(os.getenv('SDN_PATH_A_CAPACITY_MBPS', str(LINK_CAPACITY_MBPS))),
    'B': float(os.getenv('SDN_PATH_B_CAPACITY_MBPS', str(LINK_CAPACITY_MBPS))),
}
CONTROL_MODE = os.getenv('SDN_CONTROL_MODE', 'predictive').strip().lower()
SAFE_REROUTE = os.getenv('SDN_SAFE_REROUTE', '0') == '1'
CONFIDENCE_GATE = os.getenv('SDN_CONFIDENCE_GATE', '0') == '1'
DECISION_FIELDS = ['timestamp', 'active_path', 'source', 'prediction', 'lower', 'upper',
                   'in_domain', 'confidence_gate_pass', 'reason', 'signal_a', 'signal_b',
                   'alternative_safe', 'candidate', 'candidate_count', 'transition_state']
CONFIDENCE_FIELDS = ['timestamp', 'path', 'prediction', 'lower', 'upper', 'width',
                     'nominal_coverage', 'in_domain', 'confidence_gate_pass', 'gate_reason']
TRANSITION_FIELDS = ['timestamp', 'transition_id', 'stage', 'status', 'old_path', 'new_path', 'detail']
TELEMETRY_PATH = os.getenv(
    'SDN_TELEMETRY_CSV',
    os.path.join(PROJECT_ROOT, 'cnt-ml', 'data', 'raw', 'telemetry_live.csv'),
)
PREDICTIONS_PATH = os.getenv(
    'SDN_PREDICTIONS_CSV',
    os.path.join(PROJECT_ROOT, 'cnt-ml', 'results', 'live_predictions.csv'),
)
CONTROL_EVENTS_PATH = os.getenv(
    'SDN_CONTROL_EVENTS_CSV',
    os.path.join(PROJECT_ROOT, 'cnt-ml', 'results', 'control_events.csv'),
)


class PathSwitchController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(PathSwitchController, self).__init__(*args, **kwargs)
        self.active_path = 'A'
        self.datapaths = {}
        self.flow_counts = {}
        self.latest_utilizations = {}
        self.latest_utilization_times = {}
        self.latest_predictions = {}
        self.latest_prediction_times = {}
        self.last_control_signals = {}
        self.latest_intervals = {}
        self.last_decision_source = None
        self.last_policy_sample_time = None
        self.mac_to_port = {}
        self.rate_tracker = PortRateTracker()
        self.feature_history = FeatureHistory(require_contiguous=os.getenv('SDN_CONTIGUOUS_HISTORY', '0') == '1')
        self.rtt = RecentRTT(os.getenv('SDN_PING_LOG'))
        self.telemetry_recorder = TelemetryCsvRecorder(TELEMETRY_PATH)
        self.prediction_recorder = StructuredCsvRecorder(PREDICTIONS_PATH, PREDICTION_FIELDS)
        self.control_event_recorder = StructuredCsvRecorder(CONTROL_EVENTS_PATH, CONTROL_EVENT_FIELDS)
        output = os.path.dirname(CONTROL_EVENTS_PATH)
        self.confidence_recorder = StructuredCsvRecorder(os.getenv('SDN_CONFIDENCE_CSV', os.path.join(output, 'confidence.csv')), CONFIDENCE_FIELDS)
        self.decision_recorder = StructuredCsvRecorder(os.getenv('SDN_DECISIONS_CSV', os.path.join(output, 'decisions.csv')), DECISION_FIELDS)
        self.transition_recorder = StructuredCsvRecorder(os.getenv('SDN_TRANSITIONS_CSV', os.path.join(output, 'transitions.csv')), TRANSITION_FIELDS)
        self.confidence = ConfidenceEstimator() if CONFIDENCE_GATE else None
        self.route_adapter = OpenFlowRouteAdapter(self)
        self.route_transition = SafeRouteTransition(self.route_adapter,
            timeout_seconds=float(os.getenv('SDN_BARRIER_TIMEOUT_SECONDS', '3')),
            drain_seconds=float(os.getenv('SDN_DRAIN_SECONDS', '0.5')))
        if SAFE_REROUTE:
            self._record_transition(0, 'controller', 'ready', 'A', 'A',
                                    'Shared-route make-before-break enabled; not cross-switch atomic')
        if CONTROL_MODE not in {'static', 'reactive', 'predictive'}:
            raise ValueError("SDN_CONTROL_MODE must be static, reactive, or predictive")
        self.reroute_policy = ReroutePolicy(
            congestion_threshold=float(os.getenv('SDN_CONGESTION_THRESHOLD', '85')),
            recovery_threshold=float(os.getenv('SDN_RECOVERY_THRESHOLD', '70')),
            minimum_improvement=float(os.getenv('SDN_MINIMUM_IMPROVEMENT', '5')),
            consecutive_samples=int(os.getenv('SDN_CONSECUTIVE_PREDICTIONS', '3')),
            cooldown_seconds=float(os.getenv('SDN_REROUTE_COOLDOWN_SECONDS', '15')),
            alternative_ceiling=85.0 if (SAFE_REROUTE or CONFIDENCE_GATE) else None,
            commit_on_decision=not SAFE_REROUTE,
        )
        with open(PATH_FILE, 'w') as f:
            f.write(self.active_path)
        model_info = get_model_info()
        self.active_model = model_info['active_model']
        self.logger.info(
            "Control mode=%s model=%s configured=%s horizon=%ss telemetry=%s",
            CONTROL_MODE, model_info['active_model'], model_info['configured_model'],
            model_info['prediction_horizon_seconds'], TELEMETRY_PATH,
        )
        self.control_event_recorder.append({
            'timestamp': time.time(), 'event': 'controller_start',
            'control_mode': CONTROL_MODE, 'old_path': '', 'new_path': self.active_path,
            'signal_a': '', 'signal_b': '',
            'reason': 'development default uses persistence unless SDN_ACTIVE_MODEL is set',
        })
        self.logger.info("Delay uses causal ping RTT, maximum age 1.5 seconds; uncovered learned inference is skipped")
        self.path_file_thread = hub.spawn(self._monitor_path_file)
        self.telemetry_thread = hub.spawn(self._monitor_telemetry)
        if SAFE_REROUTE:
            self.transition_thread = hub.spawn(self._monitor_transition)

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

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if self.datapaths.get(datapath.id) is datapath:
                self.datapaths.pop(datapath.id, None)
                if SAFE_REROUTE:
                    self.route_transition.disconnect(datapath.id)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, MAIN_DISPATCHER)
    def barrier_reply_handler(self, ev):
        if SAFE_REROUTE:
            self.route_transition.barrier(self.route_adapter.key(ev.msg.datapath, ev.msg))

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error_msg_handler(self, ev):
        message = ev.msg
        detail = 'OpenFlow error type=%s code=%s xid=%s' % (message.type, message.code, message.xid)
        self.logger.error(detail)
        if SAFE_REROUTE:
            key = self.route_adapter.key(message.datapath, message)
            owner = self.route_adapter.message_owners.get(key)
            if owner is not None:
                self.route_transition.error(owner, detail)

    def _monitor_transition(self):
        while True:
            self.route_transition.tick()
            if self.route_transition.last_record_error:
                self.logger.error('Transition audit failed: %s', self.route_transition.last_record_error)
                if self.route_transition.busy:
                    self.route_transition.disconnect(1)
            hub.sleep(0.05)

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
                self.switch_path(requested, reason='manual path file')

    def _monitor_telemetry(self):
        while True:
            for datapath in list(self.datapaths.values()):
                self._request_stats(datapath)
            hub.sleep(MONITOR_INTERVAL_SECONDS)

    def _request_stats(self, datapath):
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        datapath.send_msg(parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY))
        datapath.send_msg(parser.OFPFlowStatsRequest(datapath))

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_reply_handler(self, ev):
        if ev.msg.datapath.id != 1:
            return
        counts = {2: 0, 3: 0}
        for flow in ev.msg.body:
            if getattr(flow, 'priority', 0) <= 0:
                continue
            for instruction in getattr(flow, 'instructions', []):
                for action in getattr(instruction, 'actions', []):
                    port = getattr(action, 'port', None)
                    if port in counts:
                        counts[port] += 1
        self.flow_counts.update(counts)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply_handler(self, ev):
        if ev.msg.datapath.id != 1:
            return
        now = time.time()
        for stat in ev.msg.body:
            if stat.port_no not in S1_PORT_LINK:
                continue
            path, link_id = S1_PORT_LINK[stat.port_no]
            sample = self.rate_tracker.observe(
                timestamp=now,
                link_id=link_id,
                capacity_mbps=PATH_CAPACITY_MBPS[path],
                rx_bytes=stat.rx_bytes,
                tx_bytes=stat.tx_bytes,
                rx_packets=stat.rx_packets,
                tx_packets=stat.tx_packets,
                rx_dropped=stat.rx_dropped,
                tx_dropped=stat.tx_dropped,
                active_flows=self.flow_counts.get(stat.port_no, 0),
            )
            if sample is None:
                continue
            rtt = self.rtt.at(now)
            sample['delay'] = rtt if rtt is not None else ''
            self.telemetry_recorder.append(sample)
            self.latest_utilizations[path] = sample['utilization']
            self.latest_utilization_times[path] = now
            features = self.feature_history.update(sample)
            history_count = self.feature_history.count(link_id)
            if features is None:
                if CONFIDENCE_GATE or SAFE_REROUTE:
                    self._invalidate_prediction(path)
                self.prediction_recorder.append({
                    'timestamp': now, 'control_mode': CONTROL_MODE,
                    'active_path': self.active_path, 'path': path, 'link_id': link_id,
                    'history_count': history_count,
                    'prediction_status': 'skipped_insufficient_history',
                    'current_utilization': sample['utilization'],
                    'signal_utilization': '', 'predicted_utilization': '',
                    'inference_time_ms': '',
                })
                self.logger.info(
                    "Telemetry %s history_count=%s prediction=SKIPPED (requires 11 samples)",
                    link_id, history_count,
                )
                continue
            if CONTROL_MODE != 'predictive':
                self.prediction_recorder.append({
                    'timestamp': now, 'control_mode': CONTROL_MODE,
                    'active_path': self.active_path, 'path': path, 'link_id': link_id,
                    'history_count': history_count,
                    'prediction_status': 'observed_signal_after_warmup',
                    'current_utilization': sample['utilization'],
                    'signal_utilization': sample['utilization'],
                    'predicted_utilization': sample['utilization'],
                    'inference_time_ms': 0.0,
                })
                continue
            if rtt is None and self.active_model != 'persistence':
                if CONFIDENCE_GATE or SAFE_REROUTE:
                    self._invalidate_prediction(path)
                self.prediction_recorder.append({
                    'timestamp': now, 'control_mode': CONTROL_MODE,
                    'active_path': self.active_path, 'path': path, 'link_id': link_id,
                    'history_count': history_count, 'prediction_status': 'skipped_missing_rtt',
                    'current_utilization': sample['utilization'],
                    'signal_utilization': '', 'predicted_utilization': '',
                    'inference_time_ms': '',
                })
                continue
            try:
                inference_start = time.perf_counter()
                prediction = predict_future_utilization(features)
                inference_time_ms = (time.perf_counter() - inference_start) * 1000
            except Exception as error:
                if CONFIDENCE_GATE or SAFE_REROUTE:
                    self._invalidate_prediction(path)
                self.logger.exception("Prediction failed for %s: %s", link_id, error)
                continue
            self.latest_predictions[path] = prediction
            self.latest_prediction_times[path] = now
            if CONFIDENCE_GATE:
                interval = self.confidence.interval(prediction, features,
                    threshold=self.reroute_policy.congestion_threshold)
                self.latest_intervals[path] = interval
                self.confidence_recorder.append({
                    'timestamp': now, 'path': path, 'prediction': prediction,
                    **{field: interval[field] for field in CONFIDENCE_FIELDS
                       if field not in ('timestamp', 'path', 'prediction')},
                })
            self.prediction_recorder.append({
                'timestamp': now, 'control_mode': CONTROL_MODE,
                'active_path': self.active_path, 'path': path, 'link_id': link_id,
                'history_count': history_count,
                'prediction_status': 'predicted',
                'current_utilization': sample['utilization'],
                'signal_utilization': prediction,
                'predicted_utilization': prediction,
                'inference_time_ms': inference_time_ms,
            })
            self.logger.info(
                "Telemetry %s current=%.2f%% predicted_t+5=%.2f%%",
                link_id, sample['utilization'], prediction,
            )
        self._evaluate_reroute(now)

    def _invalidate_prediction(self, path):
        self.latest_predictions.pop(path, None)
        self.latest_prediction_times.pop(path, None)
        self.latest_intervals.pop(path, None)
        # An unavailable forecast must not count toward a learned streak.
        # Fresh measured fallback remains usable during an RTT/inference gap.
        if not CONFIDENCE_GATE or self.last_decision_source != 'measured_fallback':
            self.reroute_policy.reset()

    def _evaluate_reroute(self, now):
        if CONTROL_MODE == 'static':
            return
        if SAFE_REROUTE or CONFIDENCE_GATE:
            if (self.last_policy_sample_time is not None
                    and now - self.last_policy_sample_time > MONITOR_INTERVAL_SECONDS * 1.8):
                self.reroute_policy.reset()
            self.last_policy_sample_time = now
        if SAFE_REROUTE and self.route_transition.busy:
            return
        if CONFIDENCE_GATE:
            freshness = max(3.0, MONITOR_INTERVAL_SECONDS * 3)
            if (not {'A', 'B'}.issubset(self.latest_utilizations)
                    or any(now - self.latest_utilization_times[p] > freshness for p in ('A', 'B'))):
                self.reroute_policy.reset()
                return
            intervals = dict(self.latest_intervals)
            for path in ('A', 'B'):
                if now - self.latest_prediction_times.get(path, 0) > freshness:
                    intervals.pop(path, None)
            decision = select_signals(self.active_path, self.latest_utilizations,
                self.latest_predictions, intervals, PATH_CAPACITY_MBPS,
                threshold=self.reroute_policy.congestion_threshold)
            if decision['source'] != self.last_decision_source:
                self.reroute_policy.reset()
                self.last_decision_source = decision['source']
            projected = decision['signals']
            new_path = None
            if projected is not None:
                self.last_control_signals = projected
                new_path = self.reroute_policy.evaluate(self.active_path, projected, now)
            else:
                self.reroute_policy.reset()
            interval = intervals.get(self.active_path, {})
            self.decision_recorder.append({
                'timestamp': now, 'active_path': self.active_path,
                'source': decision['source'], 'prediction': self.latest_predictions.get(self.active_path, ''),
                'lower': interval.get('lower', ''), 'upper': interval.get('upper', ''),
                'in_domain': interval.get('in_domain', False),
                'confidence_gate_pass': interval.get('confidence_gate_pass', False),
                'reason': decision['reason'],
                'signal_a': projected.get('A', '') if projected else '',
                'signal_b': projected.get('B', '') if projected else '',
                'alternative_safe': decision['alternative_safe'], 'candidate': new_path or '',
                'candidate_count': self.reroute_policy._candidate_count,
                'transition_state': self.route_transition.state if SAFE_REROUTE else 'direct',
            })
            if new_path:
                self.switch_path(new_path, reason='%s: %s' % (decision['source'], decision['reason']))
            return
        if CONTROL_MODE == 'reactive':
            signals = self.latest_utilizations
            signal_times = self.latest_utilization_times
        else:
            signals = self.latest_predictions
            signal_times = self.latest_prediction_times
        if not {'A', 'B'}.issubset(signals):
            if SAFE_REROUTE:
                self.reroute_policy.reset()
            return
        freshness_limit = max(3.0, MONITOR_INTERVAL_SECONDS * 3)
        if any(now - signal_times[path] > freshness_limit for path in ('A', 'B')):
            if SAFE_REROUTE:
                self.reroute_policy.reset()
            return
        projected_signals = project_path_signals(self.active_path, signals, PATH_CAPACITY_MBPS)
        self.last_control_signals = projected_signals
        new_path = self.reroute_policy.evaluate(self.active_path, projected_signals, now)
        if SAFE_REROUTE:
            self.decision_recorder.append({
                'timestamp': now, 'active_path': self.active_path,
                'source': CONTROL_MODE, 'prediction': signals[self.active_path],
                'lower': '', 'upper': '', 'in_domain': '', 'confidence_gate_pass': '',
                'reason': 'point signal; confidence ablation disabled',
                'signal_a': projected_signals['A'], 'signal_b': projected_signals['B'],
                'alternative_safe': projected_signals['B' if self.active_path == 'A' else 'A'] < 85,
                'candidate': new_path or '', 'candidate_count': self.reroute_policy._candidate_count,
                'transition_state': self.route_transition.state,
            })
        if new_path is not None:
            reason = (
                "%s projected policy A=%.2f%% B=%.2f%%"
                % (CONTROL_MODE, projected_signals['A'], projected_signals['B'])
            )
            self.switch_path(new_path, reason=reason)

    def switch_path(self, new_path, reason='controller request'):
        if new_path not in ('A', 'B'):
            raise ValueError("new_path must be A or B")
        if new_path == self.active_path:
            return
        old_path = self.active_path
        if SAFE_REROUTE:
            return self.route_transition.start(old_path, new_path, time.time(), reason,
                                               dict(self.last_control_signals))
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
        self._commit_path(old_path, new_path, reason, self.last_control_signals)

    def _commit_path(self, old_path, new_path, reason, signals):
        self.active_path = new_path
        if SAFE_REROUTE:
            self.reroute_policy.commit(time.time())
        with open(PATH_FILE, 'w') as f:
            f.write(new_path)
        self.control_event_recorder.append({
            'timestamp': time.time(), 'event': 'reroute',
            'control_mode': CONTROL_MODE, 'old_path': old_path, 'new_path': new_path,
            'signal_a': signals.get('A', ''),
            'signal_b': signals.get('B', ''),
            'reason': reason,
        })
        self.logger.info("Active path is now %s (%s)", new_path, reason)

    def _record_transition(self, transition_id, stage, status, old_path, new_path, detail):
        self.transition_recorder.append({
            'timestamp': time.time(), 'transition_id': transition_id,
            'stage': stage, 'status': status, 'old_path': old_path,
            'new_path': new_path, 'detail': detail,
        })
        self.logger.info('Transition %s %s %s: %s', transition_id, stage, status, detail)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        msg = ev.msg
        dpid = msg.datapath.id
        in_port = msg.match['in_port']
        if dpid in (5, 6):
            datapath = msg.datapath
            eth = packet.Packet(msg.data).get_protocol(ethernet.ethernet)
            if eth is None or eth.ethertype == 0x88cc:
                return
            table = self.mac_to_port.setdefault(dpid, {})
            table[eth.src] = in_port
            out_port = table.get(eth.dst, datapath.ofproto.OFPP_FLOOD)
            if out_port == in_port:
                return
            actions = self._actions_output(datapath, out_port)
            if out_port != datapath.ofproto.OFPP_FLOOD:
                self.add_flow(datapath, 10, self._match(datapath, in_port=in_port, eth_dst=eth.dst), actions)
            data = msg.data if msg.buffer_id == datapath.ofproto.OFP_NO_BUFFER else None
            datapath.send_msg(datapath.ofproto_parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
                actions=actions, data=data))
            return
        self.logger.info("Packet reached controller (unmatched): dpid=%s in_port=%s", dpid, in_port)
        
