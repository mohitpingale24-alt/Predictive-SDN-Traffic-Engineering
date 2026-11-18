"""ACL firewall rule engine over the classic 5-tuple.

Pure Python (ipaddress from the stdlib). Rules are data, loaded from a
JSON file: each rule has a priority (higher wins), source/destination
CIDRs, a protocol, optional port or port range, and an action. The
engine returns the winning rule's action and counts hits per rule, so
"which rule dropped this traffic?" is always answerable. The default
policy applies when no rule matches.
"""

import ipaddress
import json

_PROTOCOL_NUMBERS = {"tcp": 6, "udp": 17, "icmp": 1}
_PROTOCOL_NAMES = {6: "tcp", 17: "udp", 1: "icmp"}


def _parse_ports(value, index):
    if value is None or value == "any":
        return (0, 65535)
    text = str(value)
    if "-" in text:
        low, high = text.split("-", 1)
        low, high = int(low), int(high)
    else:
        low = high = int(text)
    if not 0 <= low <= high <= 65535:
        raise ValueError("Rule %d: invalid port range %r" % (index, value))
    return (low, high)


def _parse_protocol(value, index):
    if value is None or value == "any":
        return None
    if isinstance(value, int):
        return value
    text = str(value).lower()
    if text in _PROTOCOL_NUMBERS:
        return _PROTOCOL_NUMBERS[text]
    try:
        return int(text)
    except ValueError:
        raise ValueError("Rule %d: unknown protocol %r" % (index, value))


class AclEngine:
    def __init__(self, rules, default_policy="allow"):
        if default_policy not in ("allow", "deny"):
            raise ValueError("default_policy must be 'allow' or 'deny'")
        self.default_policy = default_policy
        self.rules = []
        for index, raw in enumerate(rules):
            action = str(raw.get("action", "")).lower()
            if action not in ("allow", "deny"):
                raise ValueError("Rule %d: action must be allow or deny" % index)
            try:
                src = ipaddress.ip_network(
                    "0.0.0.0/0" if raw.get("src") in (None, "any") else raw["src"],
                    strict=False)
                dst = ipaddress.ip_network(
                    "0.0.0.0/0" if raw.get("dst") in (None, "any") else raw["dst"],
                    strict=False)
            except ValueError as error:
                raise ValueError("Rule %d: %s" % (index, error))
            self.rules.append(dict(
                index=index, priority=int(raw.get("priority", 0)),
                src=src, dst=dst,
                protocol=_parse_protocol(raw.get("protocol"), index),
                src_ports=_parse_ports(raw.get("src_port"), index),
                dst_ports=_parse_ports(raw.get("dst_port"), index),
                action=action, hits=0))
        self.rules.sort(key=lambda rule: -rule["priority"])

    def evaluate(self, src_ip, dst_ip, protocol=None, src_port=None, dst_port=None):
        """Return (action, rule_index or None); counts the winning hit."""
        try:
            src = ipaddress.ip_address(src_ip)
            dst = ipaddress.ip_address(dst_ip)
        except ValueError:
            return (self.default_policy, None)
        proto = _parse_protocol(protocol, -1) if protocol is not None else None
        for rule in self.rules:
            if src not in rule["src"] or dst not in rule["dst"]:
                continue
            if rule["protocol"] is not None and proto is not None \
                    and rule["protocol"] != proto:
                continue
            if src_port is not None and not rule["src_ports"][0] <= int(src_port) <= rule["src_ports"][1]:
                continue
            if dst_port is not None and not rule["dst_ports"][0] <= int(dst_port) <= rule["dst_ports"][1]:
                continue
            rule["hits"] += 1
            return (rule["action"], rule["index"])
        return (self.default_policy, None)

    def hit_counts(self):
        return {rule["index"]: rule["hits"] for rule in self.rules}


def load_acl(path, default_policy="allow"):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        default_policy = data.get("default_policy", default_policy)
        data = data.get("rules", [])
    return AclEngine(data, default_policy=default_policy)


def protocol_name(number):
    return _PROTOCOL_NAMES.get(int(number), str(number))
