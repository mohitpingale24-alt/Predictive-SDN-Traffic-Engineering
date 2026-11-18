"""Controlled traffic protocols for the isolated research extension.

The original four-system campaign is deliberately not imported or modified.
Each full protocol has 90 seconds of offered UDP application traffic. Rates
are aggregate rates across concurrent flows, before packet/header overhead.
"""
from copy import deepcopy
import math


PROTOCOL_VERSION = "safe-te-v1"
SYSTEM_CONFIG = {
    "static": dict(control_mode="static", model="persistence", confidence_gate=False, safe_reroute=False),
    "reactive": dict(control_mode="reactive", model="persistence", confidence_gate=False, safe_reroute=False),
    "predictive-baseline": dict(control_mode="predictive", model="persistence", confidence_gate=False, safe_reroute=False),
    "predictive-ml": dict(control_mode="predictive", model="gradient_boosting", confidence_gate=False, safe_reroute=False),
    "predictive-safe": dict(control_mode="predictive", model="gradient_boosting", confidence_gate=True, safe_reroute=True),
    "predictive-confidence": dict(control_mode="predictive", model="gradient_boosting", confidence_gate=True, safe_reroute=False),
    "predictive-safe-only": dict(control_mode="predictive", model="gradient_boosting", confidence_gate=False, safe_reroute=True),
}
PRIMARY_SYSTEMS = tuple(SYSTEM_CONFIG)[:5]
ABLATION_SYSTEMS = tuple(SYSTEM_CONFIG)[5:]


def _segment(name, rate, duration):
    return dict(name=name, rate_mbps=float(rate), duration_seconds=int(duration))


SCENARIOS = {
    "normal": dict(topology="diamond4", workload=[
        _segment("warmup", 3, 15), _segment("normal", 5, 60), _segment("recovery", 3, 15),
    ]),
    "ramp": dict(topology="diamond4", workload=[
        _segment("warmup", 3, 15), _segment("ramp_5", 5, 10),
        _segment("ramp_7", 7, 10), _segment("near_threshold", 9, 10),
        _segment("congestion", 13, 20), _segment("recovery", 5, 25),
    ]),
    "burst": dict(topology="diamond4", workload=[
        _segment("warmup", 3, 15), _segment("normal", 5, 15),
        _segment("burst_1", 15, 10), _segment("between_bursts", 5, 15),
        _segment("burst_2", 15, 10), _segment("recovery", 3, 25),
    ]),
    "sustained": dict(topology="diamond4", workload=[
        _segment("warmup", 3, 15), _segment("normal", 5, 15),
        _segment("sustained_congestion", 13, 40), _segment("recovery", 3, 20),
    ]),
    "near-threshold": dict(topology="diamond4", workload=[
        _segment("warmup", 3, 15),
        *[_segment("threshold_%02d" % (index + 1), rate, 10)
          for index, rate in enumerate((8.0, 8.6, 8.3, 8.7, 8.4, 8.8))],
        _segment("recovery", 3, 15),
    ]),
    "multi-flow": dict(topology="access6", workload=[
        _segment("warmup", 3, 15), _segment("concurrent_normal", 6, 35),
        _segment("shared_congestion", 12, 20), _segment("recovery", 3, 20),
    ]),
    "alternative-stress": dict(topology="diamond4", path_b_capacity_mbps=8.0, workload=[
        _segment("warmup", 3, 15), _segment("normal", 5, 15),
        _segment("unsafe_alternative", 13, 40), _segment("recovery", 3, 20),
    ]),
}
TOPOLOGIES = ("diamond4", "access6")


def scenario_definition(name, topology=None, path_a_capacity=None,
                        path_b_capacity=None, duration_scale=1.0):
    """Return a new JSON-compatible configuration; no shared values mutate.

    Scaling is for explicit smoke runs. A scaled run is marked as smoke and
    must not be grouped with full protocol repetitions in research results.
    """
    if name not in SCENARIOS:
        raise ValueError("Unknown research scenario: " + str(name))
    definition = deepcopy(SCENARIOS[name])
    chosen_topology = topology or definition["topology"]
    if chosen_topology not in TOPOLOGIES:
        raise ValueError("Unknown research topology: " + str(chosen_topology))
    for capacity in (path_a_capacity, path_b_capacity, duration_scale):
        if capacity is not None and (not math.isfinite(float(capacity)) or float(capacity) <= 0):
            raise ValueError("Capacities and duration scale must be finite and positive")
    scale = float(duration_scale)
    if scale > 10:
        raise ValueError("Duration scale must be at most 10")
    definition.update(
        name=name, topology=chosen_topology, protocol_version=PROTOCOL_VERSION,
        duration_scale=scale, smoke=scale != 1.0,
        path_a_capacity_mbps=float(path_a_capacity if path_a_capacity is not None else 10.0),
        path_b_capacity_mbps=float(path_b_capacity if path_b_capacity is not None else definition.get("path_b_capacity_mbps", 20.0)),
    )
    for segment in definition["workload"]:
        segment["duration_seconds"] = max(1, round(segment["duration_seconds"] * scale))
    pairs = (("h1", "h2"),) if chosen_topology == "diamond4" else (("h1", "h2"), ("h3", "h4"), ("h5", "h6"))
    weights = (1.0,) if len(pairs) == 1 else (3 / 12, 4 / 12, 5 / 12)
    definition["flows"] = [
        dict(flow_id=index, source=source, receiver=receiver, rate_fraction=weights[index - 1],
             iperf_port=5201, probe_port=56000)
        for index, (source, receiver) in enumerate(pairs, 1)
    ]
    definition["duration_seconds"] = sum(segment["duration_seconds"] for segment in definition["workload"])
    return definition


def system_definition(system):
    if system not in SYSTEM_CONFIG:
        raise ValueError("Unknown research system: " + str(system))
    return deepcopy(SYSTEM_CONFIG[system])


def campaign_plan(repetitions=3, include_ablations=False, scenarios=None):
    """A matched workload and repetition index for each system.

    The caller chooses when/how to execute this plan; it starts no processes.
    Alternating system order by repetition reduces fixed ordering bias.
    """
    if isinstance(repetitions, bool) or not isinstance(repetitions, int) or repetitions < 1:
        raise ValueError("Repetitions must be a positive integer")
    names = list(SCENARIOS if scenarios is None else scenarios)
    for name in names:
        scenario_definition(name)
    systems = PRIMARY_SYSTEMS + (ABLATION_SYSTEMS if include_ablations else ())
    plan = []
    for name in names:
        for repetition in range(1, repetitions + 1):
            ordered = systems if repetition % 2 else tuple(reversed(systems))
            for system in ordered:
                plan.append(dict(system=system, scenario=name, repetition=repetition,
                                 topology=SCENARIOS[name]["topology"]))
    return plan
