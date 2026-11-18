# Predictive SDN Network Experiment Results

Independent repetitions per system: **8**

Values are mean [95% confidence interval].

| Metric | Static | Reactive | Predictive Persistence | Predictive GB |
| --- | ---: | ---: | ---: | ---: |
| Average latency (ms) | 15.4089 [13.6351, 17.1826] | 11.8039 [11.7556, 11.8522] | 11.8319 [11.7897, 11.8742] | 18.7768 [15.4652, 22.0884] |
| P95 latency (ms) | 76.8606 [31.9976, 121.7236] | 16.6450 [16.5859, 16.7041] | 16.7000 [16.5981, 16.8019] | 100.8219 [61.3451, 140.2986] |
| Throughput (Mbps) | 7.3999 [7.3999, 7.4000] | 7.3999 [7.3999, 7.4000] | 7.3999 [7.3999, 7.4000] | 7.3999 [7.3999, 7.4000] |
| Packet loss (%) | 4.9932 [4.9782, 5.0083] | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 4.9951 [4.9799, 5.0102] |
| Jitter (ms) | 0.0959 [0.0932, 0.0985] | 0.0656 [0.0128, 0.1184] | 0.0416 [0.0364, 0.0468] | 0.0906 [0.0839, 0.0973] |
| Congestion duration (s) | 39.0000 [39.0000, 39.0000] | 3.0000 [3.0000, 3.0000] | 3.0000 [3.0000, 3.0000] | 39.0000 [39.0000, 39.0000] |
| Congestion events | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] |
| Reroutes | 0.0000 [0.0000, 0.0000] | 2.0000 [2.0000, 2.0000] | 2.0000 [2.0000, 2.0000] | 0.0000 [0.0000, 0.0000] |
| Successful reroutes | 0.0000 [0.0000, 0.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 0.0000 [0.0000, 0.0000] |
| False reroutes | 0.0000 [0.0000, 0.0000] | 1.0000 [1.0000, 1.0000] | 1.0000 [1.0000, 1.0000] | 0.0000 [0.0000, 0.0000] |
| Congestion avoidance rate | N/A | 0.5000 [0.5000, 0.5000] | 0.5000 [0.5000, 0.5000] | N/A |
| Prediction to reroute (ms) | N/A | 2005.6754 [2004.8955, 2006.4553] | 2005.0332 [2004.2748, 2005.7916] | N/A |
| Inference time (ms) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | 0.0076 [0.0071, 0.0082] | N/A |
| Controller CPU (%) | 2.4423 [2.4163, 2.4683] | 2.4585 [2.4332, 2.4838] | 2.4553 [2.4286, 2.4820] | 3.4783 [3.4018, 3.5548] |

Full sample standard deviations and confidence bounds are in `network_statistics.csv`; individual observations are in `network_metrics_per_run.csv`.

Congestion Avoidance Rate uses the paired static run as the counterfactual. A reroute is counted as successful when the static run crosses 85% within five seconds at the same workload-relative time and the rerouted run remains below 85% for the following ten seconds.

False Reroute Rate is the fraction of predictive reroutes for which the paired static run does not cross 85% within that five-second counterfactual window.
