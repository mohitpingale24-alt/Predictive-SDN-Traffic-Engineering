"""DiffServ QoS policy: DSCP classes, meter bands and queues.

Pure Python. The policy is data: traffic classes with their DSCP values,
their share of link capacity, and their queue/meter ids. Meter rates are
*derived* from the link capacity the caller supplies (share x capacity),
never stored as absolute numbers, so the same policy is correct on a
10 Mbps teaching link and a 10 Gbps one. Replace the whole policy by
pointing SDN_QOS_POLICY_PATH at a JSON file of the same shape.
"""

import json

# DiffServ code points follow RFC 4594 service-class guidance:
# EF (46) voice, AF41 (34) multimedia conferencing, AF21 (18) low-latency
# data, CS1 (8) low-priority data, 0 best effort.
DEFAULT_QOS_POLICY = {
    "classes": [
        dict(name="voice", dscp=[46], share=0.30, queue_id=3, meter_id=1),
        dict(name="video", dscp=[34], share=0.30, queue_id=2, meter_id=2),
        dict(name="interactive", dscp=[18], share=0.20, queue_id=1, meter_id=3),
        dict(name="bulk", dscp=[8], share=0.10, queue_id=0, meter_id=4),
        dict(name="default", dscp=[0], share=None, queue_id=0, meter_id=None),
    ],
    "default_class": "default",
}

_TRAFFIC_CLASS_TO_QOS = {
    "voice": "voice", "video": "video", "interactive": "interactive",
    "bulk": "bulk", "default": "default",
}


def load_qos_policy(path=None):
    if path is None:
        policy = json.loads(json.dumps(DEFAULT_QOS_POLICY))
    else:
        with open(path, encoding="utf-8") as handle:
            policy = json.load(handle)
    _validate(policy)
    return policy


def _validate(policy):
    names = set()
    dscps = set()
    total_share = 0.0
    for entry in policy["classes"]:
        name = entry["name"]
        if name in names:
            raise ValueError("Duplicate QoS class: %s" % name)
        names.add(name)
        for dscp in entry["dscp"]:
            if not 0 <= int(dscp) <= 63:
                raise ValueError("DSCP out of range: %s" % dscp)
            if int(dscp) in dscps:
                raise ValueError("DSCP %s claimed by two classes" % dscp)
            dscps.add(int(dscp))
        share = entry.get("share")
        if share is not None:
            if not 0 < float(share) <= 1:
                raise ValueError("Class share must lie in (0, 1]: %s" % name)
            total_share += float(share)
    if total_share > 1.0 + 1e-9:
        raise ValueError("Class shares sum to more than the link capacity")
    if policy["default_class"] not in names:
        raise ValueError("default_class names no defined class")


def class_for_dscp(policy, dscp):
    dscp = int(dscp)
    for entry in policy["classes"]:
        if dscp in entry["dscp"]:
            return entry
    return class_by_name(policy, policy["default_class"])


def class_by_name(policy, name):
    for entry in policy["classes"]:
        if entry["name"] == name:
            return entry
    raise KeyError("Unknown QoS class: %s" % name)


def class_for_traffic(policy, traffic_class):
    """Map a TrafficClassifier label onto a QoS class."""
    return class_by_name(policy, _TRAFFIC_CLASS_TO_QOS.get(
        str(traffic_class), policy["default_class"]))


def meter_bands(policy, capacity_mbps):
    """OpenFlow meter bands derived from class shares x link capacity.

    Returns one dict per metered class: meter_id, class name, rate in
    kbit/s, and a burst allowance of 100 ms worth of the rate.
    """
    bands = []
    for entry in policy["classes"]:
        if entry.get("share") is None or entry.get("meter_id") is None:
            continue
        rate_kbps = int(round(float(entry["share"]) * float(capacity_mbps) * 1000.0))
        bands.append(dict(meter_id=int(entry["meter_id"]), class_name=entry["name"],
                          rate_kbps=rate_kbps,
                          burst_kbit=max(1, rate_kbps // 10)))
    return bands


def queue_for_dscp(policy, dscp):
    return int(class_for_dscp(policy, dscp)["queue_id"])
