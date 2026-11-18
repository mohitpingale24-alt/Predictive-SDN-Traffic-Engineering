"""Barrier-confirmed route transactions, independent of Ryu and model inference.

Adapters queue all commands in a phase and return their barrier keys. They map
each FlowMod error to the barrier key that owns it. Keys are
``(datapath_id, connection_identity, xid)`` so replies from an old connection
cannot complete a transaction on a replacement connection.

The adapter must leave the old ingress selectors installed until retirement.
Rollback removes the new selectors and confirms both old ingress switches.
Callbacks may arrive during adapter calls; those callbacks are replayed once
the phase's returned keys have been registered.
"""

import math
import time


class SafeRouteTransition:
    PHASES = {"preparing", "activating", "rolling_back", "cleaning"}

    def __init__(self, adapter, clock=time.monotonic, timeout_seconds=3,
                 drain_seconds=.5):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite")
        if not math.isfinite(drain_seconds) or drain_seconds < 0:
            raise ValueError("drain_seconds must be nonnegative and finite")
        self.adapter = adapter
        self.clock = clock
        self.timeout_seconds = float(timeout_seconds)
        self.drain_seconds = float(drain_seconds)
        self.state = "idle"
        self.transition_id = None
        self.committed = False
        self.last_error = None
        self.last_record_error = None
        self._counter = 0
        self._old = self._new = None
        self._reason = None
        self._signals = {}
        self._deadline = None
        self._phase_keys = set()
        self._pending = set()
        self._participants = set()
        self._activation_dpids = set()
        self._issuing = False
        self._early = []

    @property
    def busy(self):
        return self.state != "idle"

    def start(self, old_path, new_path, now_wall, reason, signals):
        if self.busy or old_path == new_path:
            return False
        if not isinstance(old_path, str) or not old_path or not isinstance(new_path, str) or not new_path:
            raise ValueError("old_path and new_path must be nonempty route names")
        if not math.isfinite(float(now_wall)):
            raise ValueError("now_wall must be finite")
        self._counter += 1
        self.transition_id = self._counter
        self._old, self._new = old_path, new_path
        self._reason, self._signals = str(reason), dict(signals)
        self.committed = False
        self.last_error = self.last_record_error = None
        self._participants = set()
        self._activation_dpids = set()
        self._record("transaction", "started", "requested_at=%.6f" % float(now_wall))
        self._issue("preparing", self.adapter.prepare, self._new)
        return True

    def _record(self, stage, status, detail=""):
        try:
            self.adapter.record(self.transition_id, stage, status,
                                self._old, self._new, str(detail))
        except Exception as error:
            # A recorder exception must not interrupt a rollback in progress.
            # Expose it for the controller's logger/quality gate to report.
            self.last_record_error = str(error)

    @staticmethod
    def _validated_keys(keys):
        if not isinstance(keys, (set, frozenset)):
            raise ValueError("adapter phase must return a set of barrier keys")
        if not keys:
            raise ValueError("adapter phase must register at least one barrier")
        for key in keys:
            if not isinstance(key, tuple) or len(key) != 3:
                raise ValueError("barrier keys must be (dpid, connection_identity, xid)")
        return set(keys)

    def _issue(self, stage, method, *arguments):
        self.state = stage
        self._phase_keys = self._pending = set()
        self._deadline = self.clock() + self.timeout_seconds
        self._record(stage, "started")
        self._issuing = True
        self._early = []
        try:
            keys = self._validated_keys(method(*arguments, self.transition_id))
            self._phase_keys = set(keys)
            self._pending = set(keys)
            dpids = {key[0] for key in keys}
            self._participants.update(dpids)
            if stage == "activating":
                self._activation_dpids = dpids
            if stage == "rolling_back" and not self._activation_dpids.issubset(dpids):
                raise RuntimeError("rollback did not cover every activated ingress switch")
        except Exception as error:
            self._issuing = False
            self._early = []
            self._fail("%s adapter failed: %s" % (stage, error))
            return
        self._issuing = False
        early, self._early = self._early, []
        for callback, arguments in early:
            getattr(self, callback)(*arguments)
        if self.state == stage and not self._pending:
            self._phase_complete()

    def barrier(self, key):
        if self._issuing:
            self._early.append(("barrier", (key,)))
            return True
        if self.state not in self.PHASES or key not in self._pending:
            return False
        self._pending.remove(key)
        if not self._pending:
            self._phase_complete()
        return True

    def error(self, key, detail):
        if self._issuing:
            self._early.append(("error", (key, detail)))
            return True
        if not self.busy or key not in self._phase_keys:
            return False
        self._fail("OpenFlow error: %s" % detail)
        return True

    def disconnect(self, dpid):
        if self._issuing:
            self._early.append(("disconnect", (dpid,)))
            return True
        if not self.busy or dpid not in self._participants:
            return False
        self._fail("switch %s disconnected" % dpid)
        return True

    def tick(self):
        if not self.busy or self.state == "indeterminate" or self._issuing:
            return
        if self.clock() < self._deadline:
            return
        if self.state == "draining":
            self._record("draining", "confirmed", "old rules retained through drain interval")
            self._issue("cleaning", self.adapter.retire, self._old)
        else:
            self._fail("%s barrier timeout" % self.state)

    def _phase_complete(self):
        stage = self.state
        self._record(stage, "confirmed")
        if stage == "preparing":
            self._issue("activating", self.adapter.activate, self._new)
        elif stage == "activating":
            # Ingress barrier replies establish the network commit. A later
            # publication/logging failure cannot safely be treated as if the
            # acknowledged route change never happened.
            self.committed = True
            self._record("commit", "committed", "both ingress changes acknowledged")
            try:
                self.adapter.commit(self._old, self._new, self.transition_id,
                                    self._reason, dict(self._signals))
            except Exception as error:
                self._fail("commit callback failed: %s" % error)
                return
            if self.state != "activating":
                return
            self.state = "draining"
            self._deadline = self.clock() + self.drain_seconds
            self._record("draining", "started")
        elif stage == "rolling_back":
            self._record("transaction", "aborted", self.last_error or "old ingress restored")
            self._idle()
        elif stage == "cleaning":
            self._record("transaction", "complete")
            self._idle()

    def _fail(self, detail):
        self.last_error = str(detail)
        self._record(self.state, "failed", detail)
        if self.committed:
            self._record("transaction", "degraded", "new route committed; " + str(detail))
            self._indeterminate()
        elif self.state == "rolling_back":
            self._record("transaction", "indeterminate", "rollback unconfirmed; " + str(detail))
            self._indeterminate()
        else:
            self._issue("rolling_back", self.adapter.rollback, self._old, self._new)

    def _indeterminate(self):
        self.state = "indeterminate"
        self._pending = set()
        self._deadline = None

    def _idle(self):
        self.state = "idle"
        self._pending = self._phase_keys = set()
        self._deadline = None
        self._participants = set()
