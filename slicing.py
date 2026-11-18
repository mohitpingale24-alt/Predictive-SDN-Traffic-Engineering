"""Network slicing: VLAN-isolated tenant slices over one topology.

Pure Python. Slices are data, loaded from JSON: each slice has an id, a
VLAN id, its member hosts (IPs), and a bandwidth share of each link.
Validation guarantees the isolation invariant the dataplane relies on —
a host belongs to at most one slice, VLAN ids are unique and in range,
and shares cannot overcommit a link. The isolation decision itself is
conservative: traffic between two known hosts of different slices is
denied; unknown hosts are outside the slicing regime and pass.
"""

import json

MAX_VLAN_ID = 4094


class SliceManager:
    def __init__(self, slices):
        self._by_host = {}
        self._by_id = {}
        seen_vlans = set()
        total_share = 0.0
        for entry in slices:
            slice_id = str(entry["slice_id"])
            vlan_id = int(entry["vlan_id"])
            share = float(entry.get("bandwidth_share", 0.0))
            if slice_id in self._by_id:
                raise ValueError("Duplicate slice id: %s" % slice_id)
            if not 1 <= vlan_id <= MAX_VLAN_ID:
                raise ValueError("Slice %s: VLAN id out of range" % slice_id)
            if vlan_id in seen_vlans:
                raise ValueError("VLAN %d claimed by two slices" % vlan_id)
            if not 0 <= share <= 1:
                raise ValueError("Slice %s: bandwidth_share out of range" % slice_id)
            seen_vlans.add(vlan_id)
            total_share += share
            record = dict(slice_id=slice_id, vlan_id=vlan_id, share=share,
                          hosts=[str(h) for h in entry.get("hosts", [])])
            self._by_id[slice_id] = record
            for host in record["hosts"]:
                if host in self._by_host:
                    raise ValueError(
                        "Host %s in two slices (%s, %s); isolation impossible"
                        % (host, self._by_host[host], slice_id))
                self._by_host[host] = slice_id
        if total_share > 1.0 + 1e-9:
            raise ValueError("Slice bandwidth shares overcommit the link")

    def slice_for_host(self, host):
        return self._by_host.get(str(host))

    def slice(self, slice_id):
        return self._by_id.get(str(slice_id))

    def vlan_for_host(self, host):
        slice_id = self.slice_for_host(host)
        return None if slice_id is None else self._by_id[slice_id]["vlan_id"]

    def isolation_decision(self, src_host, dst_host):
        """'deny' only when both hosts are known and in different slices."""
        src = self.slice_for_host(src_host)
        dst = self.slice_for_host(dst_host)
        if src is not None and dst is not None and src != dst:
            return "deny"
        return "allow"

    def slice_bandwidth_mbps(self, slice_id, capacity_mbps):
        record = self._by_id[str(slice_id)]
        return record["share"] * float(capacity_mbps)

    def slices(self):
        return [dict(record) for record in self._by_id.values()]


def load_slices(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict):
        data = data.get("slices", [])
    return SliceManager(data)
