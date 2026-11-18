"""Link-failure tracking and recovery measurement.

Pure Python. The tracker knows nothing about which links exist: the caller
reports link down/up events, reroutes, and utilization samples with
timestamps, and every reported number (time to reroute, time to recover,
packet loss during the outage) is computed from those events. Nothing is
assumed about a topology or a path name.
"""


class FailureRecoveryTracker:
    def __init__(self, recovery_threshold_pct=85.0):
        self.recovery_threshold_pct = float(recovery_threshold_pct)
        self._down = {}
        self._events = []

    def link_down(self, link_id, timestamp, affected_paths=()):
        record = dict(link_id=str(link_id), down_at=float(timestamp),
                      affected_paths=[str(p) for p in affected_paths],
                      reroute_at=None, recovered_at=None,
                      loss_samples=[], util_samples=[])
        self._down[str(link_id)] = record
        self._events.append(dict(event="link_down", **record))
        return record

    def link_up(self, link_id, timestamp):
        record = self._down.pop(str(link_id), None)
        if record is None:
            return None
        record["up_at"] = float(timestamp)
        self._events.append(dict(event="link_up", link_id=str(link_id),
                                 timestamp=float(timestamp)))
        return record

    def note_reroute(self, timestamp, old_path=None, new_path=None):
        """Attach the first reroute after a failure to every open outage."""
        for record in self._down.values():
            if record["reroute_at"] is None and float(timestamp) >= record["down_at"]:
                record["reroute_at"] = float(timestamp)
                record["reroute_from"] = old_path
                record["reroute_to"] = new_path

    def note_sample(self, timestamp, utilization_pct=None, packet_loss_pct=None):
        """Feed live samples; recovery = utilization back under threshold."""
        for record in self._down.values():
            if packet_loss_pct is not None:
                record["loss_samples"].append((float(timestamp), float(packet_loss_pct)))
            if utilization_pct is not None:
                record["util_samples"].append((float(timestamp), float(utilization_pct)))
                if (record["recovered_at"] is None
                        and float(timestamp) > record["down_at"]
                        and float(utilization_pct) < self.recovery_threshold_pct):
                    record["recovered_at"] = float(timestamp)

    def is_down(self, link_id):
        return str(link_id) in self._down

    def open_failures(self):
        return [self.report(record) for record in self._down.values()]

    @staticmethod
    def report(record):
        down_at = record["down_at"]
        during = [loss for ts, loss in record["loss_samples"] if ts >= down_at]
        return dict(
            link_id=record["link_id"],
            affected_paths=list(record["affected_paths"]),
            down_at=down_at,
            up_at=record.get("up_at"),
            time_to_reroute_seconds=(None if record["reroute_at"] is None
                                     else round(record["reroute_at"] - down_at, 3)),
            time_to_recover_seconds=(None if record["recovered_at"] is None
                                     else round(record["recovered_at"] - down_at, 3)),
            mean_packet_loss_during_outage_pct=(
                round(sum(during) / len(during), 3) if during else None),
            max_packet_loss_during_outage_pct=(max(during) if during else None),
            reroute_from=record.get("reroute_from"),
            reroute_to=record.get("reroute_to"),
        )

    def completed_reports(self):
        return [self.report(r) for r in self._events if r.get("event") == "link_down"
                and r not in self._down.values()]
