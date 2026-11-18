#!/usr/bin/env python3
"""Run one reproducible Linux Mininet/iperf3 controller experiment.

Run this script once for each system. It creates a new output directory and
never reuses or deletes an existing run.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import threading
import time
from quality_gate import check_run


SYSTEM_CONFIG = {
	"static": {"control_mode": "static", "model": "persistence"},
	"reactive": {"control_mode": "reactive", "model": "persistence"},
	"predictive-baseline": {"control_mode": "predictive", "model": "persistence"},
	"predictive-ml": {"control_mode": "predictive", "model": "gradient_boosting"},
}

WORKLOAD = [
	{"name": "warmup", "rate_mbps": 3, "duration_seconds": 15},
	{"name": "ramp", "rate_mbps": 7, "duration_seconds": 15},
	{"name": "near_threshold", "rate_mbps": 9, "duration_seconds": 15},
	{"name": "congestion", "rate_mbps": 13, "duration_seconds": 25},
	{"name": "recovery", "rate_mbps": 5, "duration_seconds": 20},
]


def command_version(command):
	result = subprocess.run(command, capture_output=True, text=True, check=False)
	text = (result.stdout or result.stderr).strip()
	return text.splitlines()[0] if text else "unavailable"


def sha256_file(path):
	digest = hashlib.sha256()
	with open(path, "rb") as handle:
		for chunk in iter(lambda: handle.read(1024 * 1024), b""):
			digest.update(chunk)
	return digest.hexdigest()


def git_value(project_root, *arguments):
	result = subprocess.run(
		["git", "-C", str(project_root), *arguments],
		capture_output=True, text=True, check=False,
	)
	return result.stdout.strip() if result.returncode == 0 else "unavailable"


def repository_provenance(project_root):
	source_files = [
		"path_switch_controller.py", "telemetry_runtime.py", "topology.py",
		"experiments/run_experiment.py", "experiments/prepare_real_dataset.py",
		"cnt-ml/models/xgb_link_predictor.joblib",
		"cnt-ml/models/feature_list.json",
		"cnt-ml/models/gbdt_portable.json", "cnt-ml/models/gbdt_parity_fixture.json",
		"cnt-ml/src/predict.py", "cnt-ml/src/portable_gbdt.py", "experiments/quality_gate.py",
		"cnt-ml/results/metrics_comparison.csv",
		"cnt-ml/results/congestion_metrics.csv",
	]
	return {
		"commit": git_value(project_root, "rev-parse", "HEAD"),
		"branch": git_value(project_root, "rev-parse", "--abbrev-ref", "HEAD"),
		"status_porcelain": git_value(project_root, "status", "--porcelain=v1", "--untracked-files=all").splitlines(),
		"source_sha256": {
			name: sha256_file(project_root / name)
			for name in source_files if (project_root / name).exists()
		},
	}


def host_environment():
	os_release = {}
	for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
		if "=" in line:
			key, value = line.split("=", 1)
			os_release[key] = value.strip('"')
	return {
		"host_os": os_release.get("PRETTY_NAME", platform.platform()),
		"kernel": platform.release(),
		"architecture": platform.machine(),
		"python": platform.python_version(),
		"mininet": command_version(["mn", "--version"]),
		"open_vswitch": command_version(["ovs-vswitchd", "--version"]),
		"ryu": command_version(["ryu-manager", "--version"]),
		"iperf3": command_version(["iperf3", "--version"]),
		"traffic_control": command_version(["tc", "-V"]),
	}


def parse_args():
	parser = argparse.ArgumentParser()
	parser.add_argument("--system", choices=sorted(SYSTEM_CONFIG), required=True)
	parser.add_argument("--output-dir", required=True)
	parser.add_argument("--ml-model", choices=("gradient_boosting", "linear_regression"), default="gradient_boosting")
	parser.add_argument("--controller-port", type=int, default=6633)
	parser.add_argument("--path-a-capacity", type=float, default=10.0)
	parser.add_argument("--path-b-capacity", type=float, default=20.0)
	parser.add_argument("--settle-seconds", type=float, default=5.0)
	parser.add_argument("--repetitions", type=int, default=1)
	return parser.parse_args()


def require_runtime():
	if os.name != "posix" or not sys.platform.startswith("linux"):
		raise SystemExit("This experiment requires Linux with Mininet and Open vSwitch.")
	if os.geteuid() != 0:
		raise SystemExit("Run the experiment with sudo so Mininet can create namespaces.")
	for binary in ("iperf3", "ping", "ryu-manager", "ovs-vsctl"):
		if not shutil.which(binary):
			raise SystemExit(f"Required command not found: {binary}")


def controller_cpu_monitor(pid, output_path, stop_event):
	previous = None
	with open(output_path, "w", newline="", encoding="utf-8") as handle:
		writer = csv.DictWriter(handle, fieldnames=["timestamp", "cpu_percent", "memory_percent"])
		writer.writeheader()
		while not stop_event.wait(1.0):
			result = subprocess.run(
				["ps", "-p", str(pid), "-o", "%cpu=,%mem="],
				capture_output=True, text=True, check=False,
			)
			parts = result.stdout.strip().split()
			if len(parts) == 2:
				try:
					fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
					ticks = int(fields[11]) + int(fields[12])
					now = time.monotonic()
					cpu = 100 * (ticks - previous[1]) / os.sysconf("SC_CLK_TCK") / (now - previous[0]) if previous else None
					previous = (now, ticks)
				except (OSError, IndexError, ValueError):
					continue
				if cpu is None:
					continue
				writer.writerow({
					"timestamp": time.time(),
					"cpu_percent": cpu,
					"memory_percent": parts[1],
				})
				handle.flush()


def build_network(controller_port, path_a_capacity, path_b_capacity):
	from mininet.link import TCLink
	from mininet.net import Mininet
	from mininet.node import OVSSwitch, RemoteController

	net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
	controller = net.addController(
		"c0", controller=RemoteController, ip="127.0.0.1", port=controller_port,
	)
	h1 = net.addHost("h1", ip="10.0.0.1/24")
	h2 = net.addHost("h2", ip="10.0.0.2/24")
	s1 = net.addSwitch("s1", protocols="OpenFlow13", failMode="secure")
	s2 = net.addSwitch("s2", protocols="OpenFlow13", failMode="secure")
	s3 = net.addSwitch("s3", protocols="OpenFlow13", failMode="secure")
	s4 = net.addSwitch("s4", protocols="OpenFlow13", failMode="secure")
	net.addLink(h1, s1)
	net.addLink(h2, s4)
	# Path A is the constrained primary route. Path B has spare capacity but a
	# slightly higher propagation delay, making rerouting an observable tradeoff.
	net.addLink(s1, s2, bw=path_a_capacity, delay="2ms", max_queue_size=100, use_htb=True)
	net.addLink(s2, s4, bw=path_a_capacity, delay="2ms", max_queue_size=100, use_htb=True)
	net.addLink(s1, s3, bw=path_b_capacity, delay="4ms", max_queue_size=100, use_htb=True)
	net.addLink(s3, s4, bw=path_b_capacity, delay="4ms", max_queue_size=100, use_htb=True)
	net.build()
	controller.start()
	for switch in (s1, s2, s3, s4):
		switch.start([controller])
	return net, h1, h2


def controller_environment(project_root, output_dir, args):
	config = dict(SYSTEM_CONFIG[args.system])
	if args.system == "predictive-ml":
		config["model"] = args.ml_model
	environment = os.environ.copy()
	environment.update({
		"SDN_CONTROL_MODE": config["control_mode"],
		"SDN_ACTIVE_MODEL": config["model"],
		"SDN_PATH_A_CAPACITY_MBPS": str(args.path_a_capacity),
		"SDN_PATH_B_CAPACITY_MBPS": str(args.path_b_capacity),
		"SDN_TELEMETRY_CSV": str(output_dir / "telemetry.csv"),
		"SDN_PREDICTIONS_CSV": str(output_dir / "predictions.csv"),
		"SDN_CONTROL_EVENTS_CSV": str(output_dir / "control_events.csv"),
		"SDN_PING_LOG": str(output_dir / "ping.log"),
		"PYTHONUNBUFFERED": "1",
	})
	return environment, config


def run():
	args = parse_args()
	require_runtime()
	if args.repetitions < 1:
		raise SystemExit("--repetitions must be at least 1")
	project_root = Path(__file__).resolve().parents[1]
	output_dir = Path(args.output_dir).resolve()
	output_dir.mkdir(parents=True, exist_ok=False)
	environment, config = controller_environment(project_root, output_dir, args)
	preflight = subprocess.run([sys.executable, str(project_root / "experiments/preflight_model.py")],
	                          env=environment, capture_output=True, text=True, check=True)
	(output_dir / "model_preflight.json").write_text(preflight.stdout)
	metadata = {
		"experiment_id": output_dir.name,
		"created_at_iso": datetime.now(timezone.utc).isoformat(),
		"command_line": [sys.executable, *sys.argv],
		"system": args.system,
		"control_mode": config["control_mode"],
		"model": config["model"],
		"path_a_capacity_mbps": args.path_a_capacity,
		"path_b_capacity_mbps": args.path_b_capacity,
		"congestion_threshold": 85.0,
		"recovery_threshold": 70.0,
		"sampling_interval_seconds": 1.0,
		"prediction_horizon_seconds": 5,
		"history_samples_required": 11,
		"environment": host_environment(),
		"repository": repository_provenance(project_root),
		"topology": {
			"hosts": ["h1", "h2"],
			"switches": ["s1", "s2", "s3", "s4"],
			"path_a": {"links": ["s1-s2", "s2-s4"], "capacity_mbps": args.path_a_capacity, "delay_per_link": "2ms", "max_queue_packets": 100, "qdisc": "htb"},
			"path_b": {"links": ["s1-s3", "s3-s4"], "capacity_mbps": args.path_b_capacity, "delay_per_link": "4ms", "max_queue_packets": 100, "qdisc": "htb"},
		},
		"controller": {
			"mode": config["control_mode"], "model": config["model"],
			"congestion_threshold_percent": 85.0, "recovery_threshold_percent": 70.0,
			"minimum_improvement_percent": 5.0, "consecutive_signals": 3,
			"cooldown_seconds": 15.0, "prediction_horizon_seconds": 5,
			"history_samples_required": 11, "openflow_port": args.controller_port,
		},
		"traffic": {
			"protocol": "UDP", "generator": "iperf3", "client": "h1", "server": "h2",
			"iperf_arguments": "-u -b <rate>M -t <duration> -J --get-server-output", "ping_arguments": "-D -i 1",
			"workload": WORKLOAD, "repetitions": args.repetitions,
		},
		"artifacts": {
			"source_telemetry": "telemetry.csv",
			"candidate_dataset": "telemetry_real_candidate.csv",
			"candidate_provenance": "telemetry_real_candidate.csv.provenance.json",
			"sampling_gap_report": "sampling_gaps.json",
		},
		"workload": WORKLOAD,
		"repetitions": args.repetitions,
		"created_at": time.time(),
	}
	with open(output_dir / "metadata.json", "w", encoding="utf-8") as handle:
		json.dump(metadata, handle, indent=2)

	controller_log = open(output_dir / "controller.log", "w", encoding="utf-8")
	controller = subprocess.Popen(
		[
			"ryu-manager", "--ofp-tcp-listen-port", str(args.controller_port),
			str(project_root / "path_switch_controller.py"),
		],
		cwd=project_root, env=environment, stdout=controller_log,
		stderr=subprocess.STDOUT, text=True,
	)
	cpu_stop = threading.Event()
	cpu_thread = threading.Thread(
		target=controller_cpu_monitor,
		args=(controller.pid, output_dir / "controller_resources.csv", cpu_stop),
		daemon=True,
	)
	cpu_thread.start()
	net = h1 = h2 = server = ping = None
	server_log = ping_log = None
	try:
		time.sleep(args.settle_seconds)
		if controller.poll() is not None:
			raise RuntimeError("Ryu controller exited before Mininet started; inspect controller.log")
		net, h1, h2 = build_network(
			args.controller_port, args.path_a_capacity, args.path_b_capacity,
		)
		time.sleep(args.settle_seconds)
		if net.ping([h1, h2], timeout="2") != 0:
			raise RuntimeError("Initial h1-h2 connectivity check failed")

		server_log = open(output_dir / "iperf_server.log", "w", encoding="utf-8")
		server = h2.popen(["iperf3", "-s", "-J"], stdout=server_log, stderr=subprocess.STDOUT)
		ping_log = open(output_dir / "ping.log", "w", encoding="utf-8")
		ping = h1.popen(["ping", "-D", "-i", "1", h2.IP()], stdout=ping_log, stderr=subprocess.STDOUT)
		time.sleep(1)
		metadata["workload_started_at"] = time.time()
		with open(output_dir / "metadata.json", "w", encoding="utf-8") as handle:
			json.dump(metadata, handle, indent=2)

		for repetition in range(1, args.repetitions + 1):
			for index, segment in enumerate(WORKLOAD, 1):
				segment_path = output_dir / f"iperf_r{repetition:02d}_{index:02d}_{segment['name']}.json"
				with open(segment_path, "w", encoding="utf-8") as result_handle:
					client = h1.popen(
						[
							"iperf3", "-c", h2.IP(), "-u", "-b",
							f"{segment['rate_mbps']}M", "-t",
							str(segment["duration_seconds"]), "-J", "--get-server-output",
						],
						stdout=result_handle, stderr=subprocess.STDOUT,
					)
					if client.wait(timeout=segment["duration_seconds"] + 30) != 0:
						raise RuntimeError(
							f"iperf3 segment failed: repetition={repetition} name={segment['name']}"
						)
		time.sleep(args.settle_seconds)
		metadata["finished_at"] = time.time()
		metadata["finished_at_iso"] = datetime.now(timezone.utc).isoformat()
		if controller.poll() is not None:
			raise RuntimeError("Controller exited during experiment")
		metadata["quality_gate"] = check_run(output_dir, metadata)
		metadata["status"] = "complete"
	except Exception as error:
		metadata["finished_at"] = time.time()
		metadata["finished_at_iso"] = datetime.now(timezone.utc).isoformat()
		metadata["status"] = "failed"
		metadata["error"] = f"{type(error).__name__}: {error}"
		raise
	finally:
		for process in (ping, server):
			if process is not None and process.poll() is None:
				process.terminate()
		if net is not None:
			net.stop()
		for handle in (ping_log, server_log):
			if handle is not None:
				handle.close()
		cpu_stop.set()
		cpu_thread.join(timeout=3)
		if controller.poll() is None:
			controller.terminate()
			try:
				controller.wait(timeout=5)
			except subprocess.TimeoutExpired:
				controller.kill()
		controller_log.close()
		with open(output_dir / "metadata.json", "w", encoding="utf-8") as handle:
			json.dump(metadata, handle, indent=2)

	print(f"Completed {args.system} experiment -> {output_dir}")


if __name__ == "__main__":
	run()
