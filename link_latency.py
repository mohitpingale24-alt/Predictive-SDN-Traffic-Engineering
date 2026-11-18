"""Per-link latency estimation for delay-aware routing.

Pure Python. Callers feed timestamped latency observations for a link
(direct probes, or an end-to-end RTT attributed across a path's links);
the tracker keeps an exponentially weighted moving average per link and
forgets measurements older than the staleness bound, so routing never
optimizes against ancient history. Path latency is the sum of its
links, and the tracker emits MultiObjectiveScorer-ready candidate
dicts, which is where delay-aware routing actually happens.
"""


class LatencyTracker:
    def __init__(self, alpha=0.25, stale_after_seconds=30.0):
        if not 0 < float(alpha) <= 1:
            raise ValueError("alpha must lie in (0, 1]")
        self.alpha = float(alpha)
        self.stale_after_seconds = float(stale_after_seconds)
        self._links = {}

    def observe(self, link_id, latency_ms, timestamp):
        latency_ms = float(latency_ms)
        if latency_ms < 0:
            raise ValueError("latency must be non-negative")
        entry = self._links.get(str(link_id))
        if entry is None:
            self._links[str(link_id)] = dict(ewma=latency_ms, at=float(timestamp))
        else:
            entry["ewma"] = (self.alpha * latency_ms
                             + (1 - self.alpha) * entry["ewma"])
            entry["at"] = float(timestamp)
        return self._links[str(link_id)]["ewma"]

    def observe_path_rtt(self, link_ids, rtt_ms, timestamp):
        """Attribute an end-to-end RTT evenly across the path's links.

        An estimate, documented as such: without per-link probes the
        fairest attribution of a path measurement is an equal split.
        """
        link_ids = list(link_ids)
        if not link_ids:
            return
        per_link = float(rtt_ms) / len(link_ids)
        for link_id in link_ids:
            self.observe(link_id, per_link, timestamp)

    def latency_ms(self, link_id, now):
        entry = self._links.get(str(link_id))
        if entry is None or float(now) - entry["at"] > self.stale_after_seconds:
            return None
        return entry["ewma"]

    def path_latency_ms(self, link_ids, now):
        total = 0.0
        for link_id in link_ids:
            value = self.latency_ms(link_id, now)
            if value is None:
                return None
            total += value
        return total

    def scorer_candidates(self, utilization_by_path, links_by_path, now):
        """Build {path: {utilization, rtt_ms}} for MultiObjectiveScorer.

        rtt_ms is omitted for paths whose latency is unknown or stale;
        the scorer normalizes over whichever signals are present.
        """
        candidates = {}
        for path, utilization in utilization_by_path.items():
            signals = {"utilization": float(utilization)}
            latency = self.path_latency_ms(links_by_path.get(path, ()), now)
            if latency is not None:
                signals["rtt_ms"] = latency
            candidates[path] = signals
        return candidates
