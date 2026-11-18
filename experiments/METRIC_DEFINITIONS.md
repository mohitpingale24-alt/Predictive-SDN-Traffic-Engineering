# Final network metric definitions

The unit of replication is a fresh Mininet network and controller session. Packet samples and one-second telemetry rows are not independent experimental repetitions. Each system uses eight sessions with the same five workload stages, link capacities, topology and routing policy. Runs execute in the requested fixed system order. This controls configuration but does not randomize time-of-run effects.

## User-visible network performance

- Average and P95 latency: mean and linearly interpolated 95th percentile of successful ping round-trip observations during the monitoring interval, including the short pre/post-workload margins. This is end-to-end RTT, not one-way delay and not per-link delay. Lost probes are absent from the latency distribution.
- Throughput: total receiver application bytes multiplied by eight, divided by total receiver measurement seconds, in decimal Mbps. This is delivered UDP goodput rather than offered sender rate. Inter-segment setup gaps are excluded.
- Packet loss: 100 times total receiver lost UDP packets divided by total receiver expected UDP packets. It is packet-weighted across segments, not a mean of segment percentages.
- Jitter: duration-weighted mean of receiver end-of-segment iperf jitter estimates. It is not a percentile or a global per-packet jitter calculation.
- Controller CPU: one-second process user-plus-system CPU tick differences divided by elapsed monotonic time. 100 percent denotes one logical core. Mean includes startup and monitoring, not only the congestion stage.

## Routing behavior

Congestion means sampled active-link utilization at or above 85 percent. Congestion duration sums the nominal/median sampling interval for high samples. Events count transitions from below to above the threshold. The estimate has approximately one-second resolution and does not infer unseen values inside sampling gaps. Active-path attribution follows controller decision timestamps, not hardware acknowledgement times.

Total reroutes includes both A-to-B congestion transitions and B-to-A recovery. Recovery reroutes are reported separately and never counted as false congestion actions.

A successful congestion reroute is an A-to-B transition whose matched static session is congested within the next five workload-relative seconds and whose own sampled active path remains below 85 percent for the next ten seconds. This is observed relief against a time-aligned reference, not proof of OpenFlow acknowledgement or strict preemption. Congestion Relief Rate divides these successes by the number of A-to-B transitions.

A false congestion reroute is an A-to-B transition without a static congestion observation in that five-second reference window. False Reroute Rate divides that count by the A-to-B count. With no congestion reroutes, action rates are not applicable, not zero.

Congestion Avoidance Rate is the fraction of contiguous high-utilization episodes in the paired static run for which the comparison run has observations but no high samples over the same workload-relative interval. It is a strict episode-level reference proxy: relieving congestion after it begins does not count as avoiding that episode. Timing jitter and threshold oscillation can affect the count. It is not a causal estimate of prevented real-world incidents.

Prediction-to-reroute latency measures time from the beginning of the final uninterrupted above-threshold signal streak to the A-to-B controller decision, within a 15-second lookback. It excludes switch installation/acknowledgement latency and B-to-A recovery. It applies only to predictive systems. Inference timing measures the complete prediction API call, including input conversion. Static and Reactive inference fields are not applicable.

## Statistical interpretation

For each metric, report the per-run value, arithmetic mean, sample standard deviation and two-sided 95 percent Student t interval. For eight runs the critical value is 2.365 with seven degrees of freedom. Intervals describe repeatability under this emulator and workload. Fixed ordering, one topology, one source-destination pair, a deterministic workload and few congestion episodes limit external validity. A zero-width interval means identical observed run values, not certainty about all networks.

The selected Gradient Boosting artifact uses no tree split on packet loss or active flows. The training packet-loss field used segment receiver loss whereas live telemetry uses OpenFlow drops. That semantic mismatch cannot change this frozen tree ensemble's output because those features are unused, but it must be repaired before evaluating a new model that uses them. Delay has small but nonzero importance: live inference uses the latest causal RTT at most 1.5 seconds old, while calibration used nearest covered RTT within 1.5 seconds. This alignment difference remains a disclosed limitation.
