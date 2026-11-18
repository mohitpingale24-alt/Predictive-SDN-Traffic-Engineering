"""Port-to-link mapping for all-link telemetry, kept as data.

The controller historically recorded only s1's two uplinks, which made
multi-link analysis (and any graph model) impossible: the recorded
"graph" was two links. This module names every core link in the
experiment topologies and says which switch port observes it.

The map is *configuration*, not logic: it can be replaced wholesale by
pointing SDN_LINK_MAP_PATH at a JSON file of the form
{"1:2": {"link_id": "S1-S2", "path": "A"}, ...}. Capacities are never
stored here — they are derived from the path capacities the controller
was configured with, falling back to the configured default link
capacity for links that belong to no single path (aggregation links).
"""

import json

# (dpid, port) -> link. Mirrors the wiring the experiment runner builds:
# s1-s2 (A first hop), s2-s4 (A second hop), s1-s3 (B first hop),
# s3-s4 (B second hop), and the access6 aggregation links s5-s1, s6-s4.
DEFAULT_LINK_MAP = {
    (1, 2): dict(link_id="S1-S2", path="A"),
    (1, 3): dict(link_id="S1-S3", path="B"),
    (2, 1): dict(link_id="S1-S2", path="A"),
    (2, 2): dict(link_id="S2-S4", path="A"),
    (3, 1): dict(link_id="S1-S3", path="B"),
    (3, 2): dict(link_id="S3-S4", path="B"),
    (4, 2): dict(link_id="S2-S4", path="A"),
    (4, 3): dict(link_id="S3-S4", path="B"),
    (5, 4): dict(link_id="S5-S1", path=None),
    (6, 4): dict(link_id="S6-S4", path=None),
}


def load_link_map(path=None):
    """Default map, or the JSON map at ``path`` when one is supplied."""
    if path is None:
        return dict(DEFAULT_LINK_MAP)
    with open(path, encoding="utf-8") as handle:
        raw = json.load(handle)
    result = {}
    for key, value in raw.items():
        dpid, port = key.split(":")
        result[(int(dpid), int(port))] = dict(link_id=str(value["link_id"]),
                                              path=value.get("path"))
    if not result:
        raise ValueError("Link map JSON contained no entries")
    return result


def canonical_reporters(link_map):
    """One reporting (dpid, port) per link: the smallest endpoint.

    A link is visible from both ends; recording both would double every
    sample and corrupt rate computation. The canonical endpoint is
    derived from the map itself, not chosen per link by hand.
    """
    best = {}
    for endpoint, info in link_map.items():
        link_id = info["link_id"]
        if link_id not in best or endpoint < best[link_id]:
            best[link_id] = endpoint
    return set(best.values())


def link_capacity_mbps(info, path_capacities, default_capacity):
    """Capacity for a link: its path's capacity, else the default."""
    path = info.get("path")
    if path is not None and path in path_capacities:
        return float(path_capacities[path])
    return float(default_capacity)


def links_in_map(link_map):
    return sorted({info["link_id"] for info in link_map.values()})
