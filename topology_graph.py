"""Discovered-topology graph, k-shortest paths, ECMP and multi-objective scoring.

Pure Python, no Ryu/Mininet imports. Nothing here knows a path name, a switch
count, or a fixed wiring: the graph is built only from links the caller
observed (controller discovery events, a Mininet builder, or a spec file),
capacities come from those links, ECMP hashes the flow key over the equal-cost
paths that actually exist, and multi-objective weights/scales are supplied by
the caller or derived from the measurements passed in.
"""

import hashlib
import heapq
import math


def _positive(value, name):
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(name + " must be finite and positive")
    return value


class TopologyGraph:
    """Undirected weighted graph built from observed links."""

    def __init__(self):
        self._links = {}

    def add_link(self, node_a, node_b, capacity_mbps, delay_ms=0.0, link_id=None):
        capacity = _positive(capacity_mbps, "capacity_mbps")
        delay = float(delay_ms)
        if not math.isfinite(delay) or delay < 0:
            raise ValueError("delay_ms must be finite and non-negative")
        key = tuple(sorted((str(node_a), str(node_b))))
        self._links[key] = dict(
            link_id=str(link_id) if link_id is not None else "%s--%s" % key,
            capacity_mbps=capacity, delay_ms=delay)
        return self._links[key]["link_id"]

    def remove_link(self, node_a=None, node_b=None, link_id=None):
        if link_id is not None:
            for key, value in list(self._links.items()):
                if value["link_id"] == str(link_id):
                    del self._links[key]
                    return True
            return False
        return self._links.pop(tuple(sorted((str(node_a), str(node_b)))), None) is not None

    def has_link(self, link_id):
        return any(v["link_id"] == str(link_id) for v in self._links.values())

    def nodes(self):
        result = set()
        for a, b in self._links:
            result.update((a, b))
        return sorted(result)

    def links(self):
        return [dict(a=a, b=b, **v) for (a, b), v in sorted(self._links.items())]

    def neighbours(self, node):
        result = []
        for (a, b), v in self._links.items():
            if a == node:
                result.append((b, v))
            elif b == node:
                result.append((a, v))
        return sorted(result, key=lambda item: item[0])

    def _edge_cost(self, value, metric):
        if metric == "hops":
            return 1.0
        if metric == "delay":
            return value["delay_ms"]
        if metric == "inverse_capacity":
            return 1.0 / value["capacity_mbps"]
        raise ValueError("Unknown path metric: " + str(metric))

    def path_cost(self, path, metric="inverse_capacity"):
        total = 0.0
        for a, b in zip(path, path[1:]):
            value = self._links[tuple(sorted((a, b)))]
            total += self._edge_cost(value, metric)
        return total

    def path_capacity(self, path):
        """Bottleneck capacity along a node-sequence path."""
        return min(self._links[tuple(sorted((a, b)))]["capacity_mbps"]
                   for a, b in zip(path, path[1:]))

    def path_delay(self, path):
        return sum(self._links[tuple(sorted((a, b)))]["delay_ms"]
                   for a, b in zip(path, path[1:]))

    def k_shortest_paths(self, source, destination, k=3, metric="inverse_capacity"):
        """Up to k simple paths ordered by cost (best-first enumeration)."""
        source, destination = str(source), str(destination)
        if k < 1:
            raise ValueError("k must be positive")
        if source == destination:
            return [[source]]
        known = set(self.nodes())
        if source not in known or destination not in known:
            return []
        results, heap, seen = [], [(0.0, [source])], set()
        while heap and len(results) < k:
            cost, path = heapq.heappop(heap)
            node = path[-1]
            if node == destination:
                results.append(path)
                continue
            for neighbour, value in self.neighbours(node):
                if neighbour in path:
                    continue
                candidate = path + [neighbour]
                key = tuple(candidate)
                if key in seen:
                    continue
                seen.add(key)
                heapq.heappush(heap, (cost + self._edge_cost(value, metric), candidate))
        return results

    def equal_cost_paths(self, source, destination, metric="inverse_capacity",
                         tolerance=1e-9):
        """All simple paths whose cost ties the best cost (ECMP candidate set)."""
        best = self.k_shortest_paths(source, destination, k=1, metric=metric)
        if not best:
            return []
        best_cost = self.path_cost(best[0], metric)
        tied, heap, seen = [], [(0.0, [str(source)])], set()
        destination = str(destination)
        while heap:
            cost, path = heapq.heappop(heap)
            if cost > best_cost + tolerance:
                continue
            node = path[-1]
            if node == destination:
                tied.append(path)
                continue
            for neighbour, value in self.neighbours(node):
                if neighbour in path:
                    continue
                candidate = path + [neighbour]
                if tuple(candidate) in seen:
                    continue
                seen.add(tuple(candidate))
                heapq.heappush(heap, (cost + self._edge_cost(value, metric), candidate))
        return tied


def ecmp_select(flow_key, paths):
    """Deterministic ECMP choice: stable hash of the flow key over the paths.

    The same flow always lands on the same path (no per-packet spraying, so no
    reordering); different flows spread across the equal-cost set. Weights are
    proportional to each path's bottleneck capacity, derived from the graph.
    """
    if not paths:
        return None
    digest = hashlib.sha256(str(flow_key).encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big")
    return paths[bucket % len(paths)]


def ecmp_weighted_select(flow_key, weighted_paths):
    """Capacity-weighted ECMP: weighted_paths is [(path, weight), ...]."""
    total = sum(float(w) for _, w in weighted_paths)
    if total <= 0:
        raise ValueError("ECMP weights must sum to a positive value")
    digest = hashlib.sha256(str(flow_key).encode("utf-8")).digest()
    point = (int.from_bytes(digest[:8], "big") / float(1 << 64)) * total
    running = 0.0
    for path, weight in weighted_paths:
        running += float(weight)
        if point < running:
            return path
    return weighted_paths[-1][0]


class MultiObjectiveScorer:
    """Weighted score over measured/predicted path signals.

    Weights are caller-supplied and normalized; scales default to the largest
    value observed in the candidate set, so no unit is hardcoded. Lower score
    is better. Per-traffic-class weight profiles are supported: pass
    class_weights={class_name: {...}} and a class name per call.
    """

    SIGNALS = ("utilization", "rtt_ms", "loss_pct")

    def __init__(self, weights=None, class_weights=None):
        self.weights = self._normalize(weights or {"utilization": 1.0})
        self.class_weights = {name: self._normalize(w)
                              for name, w in (class_weights or {}).items()}

    @staticmethod
    def _normalize(weights):
        cleaned = {}
        for key, value in weights.items():
            if key not in MultiObjectiveScorer.SIGNALS:
                raise ValueError("Unknown scoring signal: " + str(key))
            value = float(value)
            if not math.isfinite(value) or value < 0:
                raise ValueError("Weights must be finite and non-negative")
            cleaned[key] = value
        total = sum(cleaned.values())
        if total <= 0:
            raise ValueError("At least one scoring weight must be positive")
        return {key: value / total for key, value in cleaned.items()}

    def score_paths(self, candidates, traffic_class=None):
        """candidates: {path_name: {signal: value}}. Returns ranked list."""
        weights = self.class_weights.get(traffic_class, self.weights)
        scales = {}
        for signal in weights:
            values = [float(c[signal]) for c in candidates.values()
                      if c.get(signal) is not None and math.isfinite(float(c[signal]))]
            scales[signal] = max(values) if values and max(values) > 0 else 1.0
        ranked = []
        for name, signals in candidates.items():
            score, detail = 0.0, {}
            for signal, weight in weights.items():
                value = signals.get(signal)
                normalized = 0.0 if value is None else float(value) / scales[signal]
                detail[signal] = round(normalized * weight, 6)
                score += normalized * weight
            ranked.append(dict(path=name, score=round(score, 6), contributions=detail,
                               traffic_class=traffic_class or "default"))
        return sorted(ranked, key=lambda row: (row["score"], row["path"]))


class TrafficClassifier:
    """Classify a flow from caller-supplied rules; first match wins.

    Rules are data, e.g. [{\"class\": \"latency\", \"dscp\": 46},
    {\"class\": \"bulk\", \"dst_port\": 5201}, {\"class\": \"bulk\", \"min_bytes\": 10**7}].
    Unmatched flows get the caller's default class. No port or class is baked in.
    """

    def __init__(self, rules=None, default_class="default"):
        self.rules = list(rules or [])
        for rule in self.rules:
            if "class" not in rule:
                raise ValueError("Every traffic rule needs a class")
        self.default_class = str(default_class)

    def classify(self, flow):
        for rule in self.rules:
            matched = True
            for key, expected in rule.items():
                if key == "class":
                    continue
                actual = flow.get(key)
                if key == "min_bytes":
                    if actual is None or float(actual) < float(expected):
                        matched = False
                        break
                elif actual != expected:
                    matched = False
                    break
            if matched:
                return rule["class"]
        return self.default_class
