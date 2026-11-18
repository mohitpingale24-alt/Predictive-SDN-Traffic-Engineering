"""Surge / elephant-flow anomaly detection and mitigation decisions.

Pure Python. Detection is statistical, not threshold-on-a-magic-number: each
source's rate is compared with its own rolling mean and standard deviation,
and an elephant is a source whose share of the total observed traffic exceeds
the caller-supplied share. A mitigation decision is a data record (action,
source, reason, expiry) — installing or removing the actual OpenFlow rule is
the controller's job, and mitigations expire unless refreshed.
"""

import math
import statistics
from collections import defaultdict, deque


class SurgeDetector:
    def __init__(self, window=30, z_threshold=4.0, elephant_share=0.5,
                 min_samples=10):
        if int(window) < 5 or int(min_samples) < 3 or float(z_threshold) <= 0:
            raise ValueError("Invalid detector window or thresholds")
        if not 0 < float(elephant_share) <= 1:
            raise ValueError("elephant_share must lie in (0, 1]")
        self.window = int(window)
        self.z_threshold = float(z_threshold)
        self.elephant_share = float(elephant_share)
        self.min_samples = int(min_samples)
        self._rates = defaultdict(lambda: deque(maxlen=self.window))
        self._last = {}
        self._active = {}

    def observe(self, source, rate_mbps, timestamp):
        """Return a mitigation dict when this sample is anomalous, else None."""
        source = str(source)
        rate = float(rate_mbps)
        if not math.isfinite(rate) or rate < 0:
            return None
        history = self._rates[source]
        verdict = None
        if len(history) >= self.min_samples:
            mean = statistics.fmean(history)
            stdev = statistics.pstdev(history) if len(history) > 1 else 0.0
            if stdev > 0 and rate > mean + self.z_threshold * stdev:
                verdict = dict(kind="surge", z_score=round((rate - mean) / stdev, 3),
                               baseline_mean=round(mean, 3), baseline_stdev=round(stdev, 3))
            # An elephant is a share of *concurrent* traffic, so it needs at
            # least two observed sources; a lone source is always 100%.
            others = [value for key, value in self._last.items() if key != source]
            if verdict is None and others:
                total = rate + sum(others)
                if total > 0 and rate / total >= self.elephant_share and rate > mean:
                    verdict = dict(kind="elephant_flow", share=round(rate / total, 3),
                                   baseline_mean=round(mean, 3), baseline_stdev=round(stdev, 3))
        history.append(rate)
        self._last[source] = rate
        if verdict is None:
            return None
        mitigation = dict(action="rate_limit", source=source,
                          rate_mbps=verdict["baseline_mean"],
                          started_at=float(timestamp), **verdict)
        self._active[source] = mitigation
        return mitigation

    def active_mitigations(self, now, ttl_seconds=60.0):
        """Mitigations still in force; expired ones are dropped, not kept."""
        expired = [source for source, m in self._active.items()
                   if float(now) - m["started_at"] > float(ttl_seconds)]
        for source in expired:
            del self._active[source]
        return [dict(m) for m in self._active.values()]

    def clear(self, source):
        return self._active.pop(str(source), None) is not None
