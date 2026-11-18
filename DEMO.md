# Predictive SDN implementation demonstration

## Recorded comparison on this Mac

Campaign 02 has passed all 32 capture checks. Start `start_project.command`; the local dashboard opens automatically. In **Live lab**, choose Predictive GB and press **Start experiment** to generate fresh traffic in the isolated Linux VM. Watch the active route and utilization update; final metrics appear only after verification. **Replay** selects preserved campaign captures, and **Compare** shows the original controlled results. Starting a live session never overwrites the campaign.

For an optional terminal replay, run from the project root:

```bash
.venv-ml/bin/python experiments/replay_demo.py --campaign final-evaluation-artifacts/campaign-02
```

This is explicitly labelled recorded replay. It reads actual telemetry, model outputs and reroute events, checks the capture quality, and prints the measured network behavior. It neither creates traffic nor simulates a live controller.

## Live demonstration inside Linux

Use the prepared Ubuntu VM or a Linux Mininet host. Activate the verified controller environment, then run each command separately from the Linux repository checkout. Each output directory must be new.

```bash
export PATH="/home/mohit/sdn-venv/bin:$PATH"
sudo --preserve-env=PATH python3 experiments/run_experiment.py --system static --output-dir /tmp/sdn-demo-static
sudo --preserve-env=PATH python3 experiments/run_experiment.py --system reactive --output-dir /tmp/sdn-demo-reactive
sudo --preserve-env=PATH python3 experiments/run_experiment.py --system predictive-baseline --output-dir /tmp/sdn-demo-persistence
sudo --preserve-env=PATH python3 experiments/run_experiment.py --system predictive-ml --output-dir /tmp/sdn-demo-gb
```

Each demonstration takes approximately two minutes. Stop at the first failure and inspect its controller log and metadata. Do not start another Mininet experiment while the evaluation campaign is running.

## What to explain during the demonstration

1. Show the four-switch diamond in `experiments/run_experiment.py`. Path A has 10 Mbps capacity; Path B has 20 Mbps but higher propagation delay.
2. Show `telemetry_runtime.py` and the seven-field raw CSV. Explain byte-counter rates, the 11-sample history requirement and measured RTT coverage.
3. Show the Gradient Boosting model metadata and a prediction row. The API forecasts utilization at the exact five-second horizon used for training labels.
4. Show `path_switch_controller.py` and its reroute event. The controller requires three consecutive signals and uses projected alternative load, hysteresis and cooldown.
5. Show receiver goodput, RTT, loss and congestion duration for all four systems. Explain any tradeoff instead of asserting that ML must win.

The research implementation is a Ryu/OpenFlow controller, Mininet testbed, offline training/evaluation pipeline and local GUI with live experiment controls and recorded replay. The live mode runs a real emulated network, not a production deployment. GRU is an offline regression comparison, not a fifth live controller.
