#!/usr/bin/env python3
"""Teste de stress do backend CPU-bound.

A carga é gerada neste processo, no hospedeiro. A VM só executa a aplicação
e o coletor de CPU/memória.
"""

import json
import os
import shlex
import statistics
import subprocess
import time
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "results.json"
ASKPASS = Path("/tmp/ssh-askpass-osboxes.sh")
VM = "cav_vm"
SSH_TARGET = "osboxes@localhost"
APP_URL_HOST = "127.0.0.1"
APP_PORT = 3000
COMPUTE_PATH = "/compute?limit=1000000"
DURATION_S = 25
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]
SCENARIOS = [
    {"name": "1vcpu-1gb", "cpus": 1, "memory_mb": 1024},
    {"name": "2vcpu-1gb", "cpus": 2, "memory_mb": 1024},
    {"name": "4vcpu-1gb", "cpus": 4, "memory_mb": 1024},
    {"name": "2vcpu-2gb", "cpus": 2, "memory_mb": 2048},
]


def env_with_askpass():
    password = os.environ.get("VM_SSH_PASSWORD", "")
    if not password:
        raise SystemExit("Defina VM_SSH_PASSWORD.")
    ASKPASS.write_text(f"#!/bin/sh\necho {password}\n", encoding="utf-8")
    ASKPASS.chmod(0o700)
    env = os.environ.copy()
    env["SSH_ASKPASS"] = str(ASKPASS)
    env["SSH_ASKPASS_REQUIRE"] = "force"
    env["DISPLAY"] = env.get("DISPLAY", ":0")
    return env


def run(cmd, timeout=120, check=True, quiet=False):
    if not quiet:
        print("+", " ".join(cmd), flush=True)
    completed = subprocess.run(
        cmd,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env_with_askpass(),
        check=False,
    )
    if completed.stdout.strip() and not quiet:
        print(completed.stdout.strip(), flush=True)
    if completed.returncode != 0 and check:
        print(completed.stderr.strip(), flush=True)
        raise RuntimeError(f"comando falhou ({completed.returncode}): {' '.join(cmd)}")
    return completed


def ssh(remote, timeout=120, check=True, quiet=False):
    return run(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            "ConnectTimeout=8",
            "-p",
            "2222",
            SSH_TARGET,
            remote,
        ],
        timeout=timeout,
        check=check,
        quiet=quiet,
    )


def vm_info():
    completed = run(
        ["VBoxManage", "showvminfo", VM, "--machinereadable"],
        timeout=30,
        quiet=True,
    )
    info = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        info[key] = raw.strip().strip('"')
    return info


def wait_ssh(attempts=40):
    for _ in range(attempts):
        probe = ssh("echo up", timeout=15, check=False)
        if probe.returncode == 0 and "up" in probe.stdout:
            return
        time.sleep(3)
    raise RuntimeError("SSH da VM não voltou.")


def port_forward_present():
    info = run(["VBoxManage", "showvminfo", VM], timeout=30, quiet=True).stdout
    return "host port = 3000" in info


def ensure_port_forward():
    if port_forward_present():
        return
    state = vm_info().get("VMState")
    command = "controlvm" if state == "running" else "modifyvm"
    flag = "natpf1" if state == "running" else "--natpf1"
    run(["VBoxManage", command, VM, flag, "cpuapp,tcp,,3000,,3000"])


def poweroff_vm():
    password = os.environ["VM_SSH_PASSWORD"]
    remote = f"echo {shlex.quote(password)} | sudo -S -p '' poweroff"
    ssh(remote, timeout=25, check=False, quiet=True)
    for _ in range(40):
        state = vm_info().get("VMState")
        print("estado:", state, flush=True)
        if state == "poweroff":
            return
        time.sleep(3)
    raise RuntimeError("A VM não desligou.")


def ensure_scenario(cpus, memory_mb):
    info = vm_info()
    same = (
        info.get("VMState") == "running"
        and int(info.get("cpus", "0")) == cpus
        and int(info.get("memory", "0")) == memory_mb
    )
    if same:
        print(f"VM já está com {cpus} vCPU e {memory_mb} MB.", flush=True)
        ensure_port_forward()
        return
    if info.get("VMState") == "running":
        poweroff_vm()
    if not port_forward_present():
        run(["VBoxManage", "modifyvm", VM, "--natpf1", "cpuapp,tcp,,3000,,3000"])
    run(["VBoxManage", "modifyvm", VM, "--cpus", str(cpus), "--memory", str(memory_mb)])
    run(["VBoxManage", "startvm", VM, "--type", "headless"], timeout=60)
    wait_ssh()


def start_app():
    # O texto do ssh não pode conter "dist/main.js": o pkill encerraria o próprio shell.
    remote = r"""
pkill -f 'dist/mai[n].js' || true
sleep 0.5
: > /home/osboxes/cpu-bound.log
bin=/home/osboxes/node-v22.18.0-linux-x64/bin/node
dir=/home/osboxes/cpu-bound-backend/dist
setsid "$bin" "$dir/main.js" > /home/osboxes/cpu-bound.log 2>&1 < /dev/null &
echo started
"""
    ssh(remote, timeout=30)
    deadline = time.time() + 40
    while time.time() < deadline:
        if http_status("/health") == 200:
            log = ssh("grep -a 'iniciando' /home/osboxes/cpu-bound.log | tail -1", check=False)
            facts = ssh("nproc; awk '/MemTotal/ {print $2}' /proc/meminfo", check=True)
            lines = [line for line in facts.stdout.splitlines() if line.strip()]
            return {
                "workers_log": log.stdout.strip(),
                "nproc": int(lines[0]),
                "mem_total_kb": int(lines[1]),
            }
        time.sleep(1)
    ssh("tail -40 /home/osboxes/cpu-bound.log", check=False)
    raise RuntimeError("A aplicação não respondeu em /health.")


def http_status(path):
    try:
        connection = HTTPConnection(APP_URL_HOST, APP_PORT, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return response.status
    except OSError:
        return 0


def one_request():
    started = time.perf_counter()
    connection = HTTPConnection(APP_URL_HOST, APP_PORT, timeout=120)
    connection.request("GET", COMPUTE_PATH)
    response = connection.getresponse()
    body = response.read()
    elapsed = time.perf_counter() - started
    payload = json.loads(body.decode("utf-8"))
    return response.status, elapsed, float(payload.get("elapsedMs", 0))


def load_for(concurrency, duration_s):
    stop_at = time.perf_counter() + duration_s
    buckets = [[] for _ in range(concurrency)]

    def worker(bucket):
        connection = HTTPConnection(APP_URL_HOST, APP_PORT, timeout=120)
        while time.perf_counter() < stop_at:
            started = time.perf_counter()
            status = 0
            server_ms = None
            try:
                connection.request("GET", COMPUTE_PATH)
                response = connection.getresponse()
                raw = response.read()
                status = response.status
                if status == 200:
                    server_ms = float(json.loads(raw.decode("utf-8"))["elapsedMs"])
            except Exception:
                status = 0
                try:
                    connection.close()
                except Exception:
                    pass
                connection = HTTPConnection(APP_URL_HOST, APP_PORT, timeout=120)
            bucket.append((time.perf_counter() - started, status, server_ms))

    threads = [Thread(target=worker, args=(buckets[index],)) for index in range(concurrency)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [item for bucket in buckets for item in bucket]


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def start_monitor():
    ssh("rm -f /tmp/stress-metrics.jsonl; pkill -f 'guest_monito[r].py' || true", check=False)
    ssh(
        "setsid python3 /home/osboxes/guest_monitor.py /tmp/stress-metrics.jsonl "
        ">/tmp/guest-monitor.out 2>&1 </dev/null & echo monitor-started"
    )
    time.sleep(1.2)


def stop_monitor():
    ssh("pkill -f 'guest_monito[r].py' || true", check=False)
    completed = ssh("cat /tmp/stress-metrics.jsonl", timeout=30, check=False)
    samples = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            samples.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return samples


def summarize_samples(samples):
    if not samples:
        return {}

    def avg(key):
        return round(statistics.fmean(item[key] for item in samples), 2)

    def peak(key):
        return round(max(item[key] for item in samples), 2)

    return {
        "samples": len(samples),
        "cpu_pct_avg": avg("cpu_pct"),
        "cpu_pct_max": peak("cpu_pct"),
        "steal_pct_avg": avg("steal_pct"),
        "mem_used_mb_avg": avg("mem_used_mb"),
        "mem_used_mb_max": peak("mem_used_mb"),
        "node_rss_mb_avg": avg("node_rss_mb"),
        "node_rss_mb_max": peak("node_rss_mb"),
    }


def summarize_requests(records, duration_s):
    ok = [item for item in records if item[1] == 200]
    latencies = [item[0] * 1000 for item in ok]
    server = [item[2] for item in ok if item[2] is not None]
    wall = duration_s
    return {
        "requests_total": len(records),
        "requests_ok": len(ok),
        "requests_error": len(records) - len(ok),
        "throughput_rps": round(len(ok) / wall, 2),
        "latency_ms_avg": round(statistics.fmean(latencies), 2) if latencies else 0,
        "latency_ms_p50": round(percentile(latencies, 0.50), 2),
        "latency_ms_p95": round(percentile(latencies, 0.95), 2),
        "latency_ms_max": round(max(latencies), 2) if latencies else 0,
        "server_elapsed_ms_avg": round(statistics.fmean(server), 2) if server else 0,
    }


def warmup(nproc):
    burst = max(2, nproc * 2)
    print(f"aquecimento: {burst} requisições", flush=True)
    for _ in range(burst):
        try:
            one_request()
        except Exception as error:
            print("aquecimento falhou:", error, flush=True)


def main():
    previous = []
    if RESULTS_PATH.exists():
        previous = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    done = {item["scenario"] for item in previous}
    results = list(previous)

    try:
        for scenario in SCENARIOS:
            if scenario["name"] in done:
                print("pulando", scenario["name"], flush=True)
                continue
            print(f"\n=== {scenario['name']} ===", flush=True)
            ensure_scenario(scenario["cpus"], scenario["memory_mb"])
            observed = start_app()
            warmup(observed["nproc"])
            levels = []
            for concurrency in CONCURRENCY_LEVELS:
                print(f"concorrência {concurrency}", flush=True)
                time.sleep(2)
                start_monitor()
                started = time.perf_counter()
                records = load_for(concurrency, DURATION_S)
                wall = time.perf_counter() - started
                samples = stop_monitor()
                level = {
                    "concurrency": concurrency,
                    "wall_s": round(wall, 2),
                    **summarize_requests(records, wall),
                    **summarize_samples(samples),
                }
                print(json.dumps(level), flush=True)
                levels.append(level)
            results.append({"scenario": scenario["name"], **scenario, **observed, "levels": levels})
            RESULTS_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
            done.add(scenario["name"])
        print("Restaurando a VM para 1 vCPU e 1024 MB.", flush=True)
        ensure_scenario(1, 1024)
        start_app()
    finally:
        if ASKPASS.exists():
            ASKPASS.unlink()


if __name__ == "__main__":
    main()
