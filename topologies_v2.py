#!/usr/bin/env python3
"""Parameterized topology builders: fat-tree and spine-leaf.

The wiring is generated from parameters, never enumerated by hand: a spec is
a plain dict of switches, hosts, and links (with capacities/delays) that both
Mininet construction and topology_graph.TopologyGraph consume, so the graph
the controller reasons about is the graph that was actually built.
"""


def fat_tree_spec(k=2, link_capacity_mbps=10.0, core_capacity_mbps=None,
                  delay_ms=1.0):
    """Classic k-ary fat-tree spec. k must be even; hosts = k^3/4."""
    if k < 2 or k % 2:
        raise ValueError("fat-tree k must be a positive even integer")
    core_capacity = core_capacity_mbps or link_capacity_mbps
    switches, hosts, links = [], [], []

    def link(a, b, capacity):
        links.append(dict(a=a, b=b, capacity_mbps=float(capacity),
                          delay_ms=float(delay_ms),
                          link_id="%s--%s" % (a, b)))

    half = k // 2
    cores = ["core-%d" % i for i in range(half * half)]
    switches.extend(cores)
    host_index = 0
    for pod in range(k):
        aggs = ["pod%d-agg%d" % (pod, i) for i in range(half)]
        edges = ["pod%d-edge%d" % (pod, i) for i in range(half)]
        switches.extend(aggs + edges)
        for agg_i, agg in enumerate(aggs):
            for core_i in range(agg_i * half, (agg_i + 1) * half):
                link(agg, cores[core_i], core_capacity)
        for edge in edges:
            for agg in aggs:
                link(edge, agg, link_capacity_mbps)
            for _ in range(half):
                host_index += 1
                host = "h%d" % host_index
                hosts.append(host)
                link(host, edge, link_capacity_mbps)
    return dict(name="fat-tree-%d" % k, switches=switches, hosts=hosts, links=links)


def spine_leaf_spec(spines=2, leaves=3, hosts_per_leaf=2,
                    link_capacity_mbps=10.0, uplink_capacity_mbps=None,
                    delay_ms=1.0):
    if spines < 1 or leaves < 1 or hosts_per_leaf < 1:
        raise ValueError("spines, leaves and hosts_per_leaf must be positive")
    uplink = uplink_capacity_mbps or link_capacity_mbps
    switches, hosts, links = [], [], []

    def link(a, b, capacity):
        links.append(dict(a=a, b=b, capacity_mbps=float(capacity),
                          delay_ms=float(delay_ms),
                          link_id="%s--%s" % (a, b)))

    spine_names = ["spine-%d" % i for i in range(spines)]
    switches.extend(spine_names)
    host_index = 0
    for leaf_i in range(leaves):
        leaf = "leaf-%d" % leaf_i
        switches.append(leaf)
        for spine in spine_names:
            link(leaf, spine, uplink)
        for _ in range(hosts_per_leaf):
            host_index += 1
            host = "h%d" % host_index
            hosts.append(host)
            link(host, leaf, link_capacity_mbps)
    return dict(name="spine-leaf-%dx%d" % (spines, leaves),
                switches=switches, hosts=hosts, links=links)


def build_mininet(spec, controller_ip="127.0.0.1", controller_port=6633):
    """Construct a Mininet network from a generated spec (Linux guest only)."""
    from mininet.net import Mininet
    from mininet.node import RemoteController, OVSSwitch
    from mininet.link import TCLink

    net = Mininet(controller=RemoteController, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True)
    controller = net.addController("c0", controller=RemoteController,
                                   ip=controller_ip, port=controller_port)
    nodes = {}
    for index, name in enumerate(spec["hosts"], 1):
        nodes[name] = net.addHost(name, ip="10.0.0.%d/24" % index)
    for name in spec["switches"]:
        nodes[name] = net.addSwitch(name.replace("-", ""), protocols="OpenFlow13",
                                    failMode="secure")
    for entry in spec["links"]:
        kwargs = {}
        if entry["a"] in spec["switches"] and entry["b"] in spec["switches"]:
            kwargs = dict(bw=entry["capacity_mbps"],
                          delay="%sms" % entry["delay_ms"])
        net.addLink(nodes[entry["a"]], nodes[entry["b"]], **kwargs)
    net.build()
    controller.start()
    for name in spec["switches"]:
        nodes[name].start([controller])
    return net
