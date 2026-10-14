#!/usr/bin/env python3
"""Teste de stress do backend IO-bound na VM do VirtualBox.

A carga e gerada neste processo, no hospedeiro. A VM so executa a aplicacao e
o coletor. O provisionamento variado e a banda de escrita em disco, aplicada
por um grupo de largura de banda do VirtualBox.

    export VM_SSH_PASSWORD=osboxes.org
    python3 run_stress_vm.py
"""

import json
import os
import shlex
import shutil
import statistics
import subprocess
import tempfile
import time
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parent
RESULTS_PATH = ROOT / "results.json"
ASKPASS = Path(tempfile.gettempdir()) / "ssh-askpass-osboxes.sh"

WINDOWS_VBOX = r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"
VBOXMANAGE = os.environ.get("VBOXMANAGE") or shutil.which("VBoxManage") or WINDOWS_VBOX

VM = "cav_vm"
SSH_TARGET = "osboxes@localhost"
SSH_PORT = "2222"
APP_HOST = "127.0.0.1"
APP_PORT = 3000
IO_PATH = "/io?sizeKb=256&syncs=4"
REQUEST_MB = 0.25

APP_DIR = "/home/osboxes/io-bound-backend"
DATA_DIR = f"{APP_DIR}/data"
NODE_BIN = "/home/osboxes/node-v22.18.0-linux-x64/bin/node"
MONITOR = "/home/osboxes/vm_monitor.py"
LOG = "/home/osboxes/io-bound.log"
METRICS = "/tmp/stress-metrics.jsonl"
BW_GROUP = "iolimit"

DURATION_S = 25
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]
SCENARIOS = [
    {"name": "5mbps-1vcpu-1gb", "write_mbps": 5, "cpus": 1, "memory_mb": 1024},
    {"name": "10mbps-1vcpu-1gb", "write_mbps": 10, "cpus": 1, "memory_mb": 1024},
    {"name": "20mbps-1vcpu-1gb", "write_mbps": 20, "cpus": 1, "memory_mb": 1024},
    {"name": "10mbps-2vcpu-2gb", "write_mbps": 10, "cpus": 2, "memory_mb": 2048},
]


# --------------------------------------------------------------- hospedeiro

def env_with_askpass():
    """No Windows nao ha SSH_ASKPASS: ali o acesso depende de chave SSH ja configurada."""
    password = os.environ.get("VM_SSH_PASSWORD", "")
    if not password:
        raise SystemExit("Defina VM_SSH_PASSWORD.")
    env = os.environ.copy()
    if os.name != "nt":
        ASKPASS.write_text(f"#!/bin/sh\necho {password}\n", encoding="utf-8")
        ASKPASS.chmod(0o700)
        env["SSH_ASKPASS"] = str(ASKPASS)
        env["SSH_ASKPASS_REQUIRE"] = "force"
        env["DISPLAY"] = env.get("DISPLAY", ":0")
    return env


def run(cmd, timeout=120, check=True, quiet=False):
    if not quiet:
        print("+", " ".join(cmd), flush=True)
    completed = subprocess.run(
        cmd, text=True, capture_output=True, timeout=timeout,
        env=env_with_askpass(), check=False,
    )
    if completed.stdout.strip() and not quiet:
        print(completed.stdout.strip(), flush=True)
    if completed.returncode != 0 and check:
        print(completed.stderr.strip(), flush=True)
        raise RuntimeError(f"comando falhou ({completed.returncode}): {' '.join(cmd)}")
    return completed


def ssh(remote, timeout=120, check=True, quiet=False):
    return run(
        ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8",
         "-p", SSH_PORT, SSH_TARGET, remote],
        timeout=timeout, check=check, quiet=quiet,
    )


def vm_info():
    completed = run([VBOXMANAGE, "showvminfo", VM, "--machinereadable"], timeout=30, quiet=True)
    info = {}
    for line in completed.stdout.splitlines():
        if "=" in line:
            key, raw = line.split("=", 1)
            info[key.strip('"')] = raw.strip().strip('"')
    return info


def wait_ssh(attempts=40):
    for _ in range(attempts):
        probe = ssh("echo up", timeout=15, check=False, quiet=True)
        if probe.returncode == 0 and "up" in probe.stdout:
            return
        time.sleep(3)
    raise RuntimeError("SSH da VM nao voltou.")


def poweroff_vm():
    password = os.environ["VM_SSH_PASSWORD"]
    ssh(f"echo {shlex.quote(password)} | sudo -S -p '' poweroff", timeout=25, check=False, quiet=True)
    for _ in range(40):
        state = vm_info().get("VMState")
        print("estado:", state, flush=True)
        if state == "poweroff":
            return
        time.sleep(3)
    raise RuntimeError("A VM nao desligou.")


# ------------------------------------------------- provisionamento de disco

def attached_disk():
    """(controlador, porta, device, caminho) do VDI anexado a VM."""
    for key, value in vm_info().items():
        parts = key.rsplit("-", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit() and value.endswith(".vdi"):
            return parts[0], parts[1], parts[2], value
    raise RuntimeError("Nenhum disco VDI anexado encontrado.")


def bandwidth_group_present():
    out = run([VBOXMANAGE, "bandwidthctl", VM, "list"], timeout=30, check=False, quiet=True).stdout
    return BW_GROUP in out


def set_bandwidth(mbps):
    run([VBOXMANAGE, "bandwidthctl", VM, "set", BW_GROUP, "--limit", f"{mbps}M"])


def ensure_bandwidth_group(mbps):
    """Cria o grupo e prende o disco a ele. Exige a VM desligada."""
    if bandwidth_group_present():
        set_bandwidth(mbps)
        return
    controller, port, device, medium = attached_disk()
    run([VBOXMANAGE, "bandwidthctl", VM, "add", BW_GROUP, "--type", "disk", "--limit", f"{mbps}M"])
    run([VBOXMANAGE, "storageattach", VM, "--storagectl", controller, "--port", port,
         "--device", device, "--type", "hdd", "--medium", medium, "--bandwidthgroup", BW_GROUP])


def ensure_hostiocache_off():
    """Sem isso o fsync para na memoria do hospedeiro e o teste mede RAM, nao disco."""
    controller = attached_disk()[0]
    run([VBOXMANAGE, "storagectl", VM, "--name", controller, "--hostiocache", "off"])


def port_forward_present():
    return "host port = 3000" in run([VBOXMANAGE, "showvminfo", VM], timeout=30, quiet=True).stdout


def ensure_port_forward():
    if port_forward_present():
        return
    state = vm_info().get("VMState")
    command = "controlvm" if state == "running" else "modifyvm"
    flag = "natpf1" if state == "running" else "--natpf1"
    run([VBOXMANAGE, command, VM, flag, "ioapp,tcp,,3000,,3000"])


def ensure_scenario(cpus, memory_mb, write_mbps):
    info = vm_info()
    same = (
        info.get("VMState") == "running"
        and int(info.get("cpus", "0")) == cpus
        and int(info.get("memory", "0")) == memory_mb
    )
    if same and bandwidth_group_present():
        print(f"VM ja esta com {cpus} vCPU e {memory_mb} MB; so o limite muda.", flush=True)
        ensure_port_forward()
        set_bandwidth(write_mbps)
        return
    if info.get("VMState") == "running":
        poweroff_vm()
    if not port_forward_present():
        run([VBOXMANAGE, "modifyvm", VM, "--natpf1", "ioapp,tcp,,3000,,3000"])
    run([VBOXMANAGE, "modifyvm", VM, "--cpus", str(cpus), "--memory", str(memory_mb)])
    ensure_hostiocache_off()
    ensure_bandwidth_group(write_mbps)
    run([VBOXMANAGE, "startvm", VM, "--type", "headless"], timeout=90)
    wait_ssh()
    set_bandwidth(write_mbps)


# ---------------------------------------------------------------- aplicacao

def http_status(path):
    try:
        connection = HTTPConnection(APP_HOST, APP_PORT, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        response.read()
        return response.status
    except OSError:
        return 0


def start_app():
    remote = f"""
pkill -f 'dist/mai[n].js' || true
sleep 0.5
mkdir -p {DATA_DIR}
rm -f {DATA_DIR}/*.bin 2>/dev/null || true
: > {LOG}
cd {APP_DIR}
DATA_DIR={DATA_DIR} setsid "{NODE_BIN}" "{APP_DIR}/dist/main.js" > {LOG} 2>&1 < /dev/null &
echo started
"""
    ssh(remote, timeout=30)
    deadline = time.time() + 40
    while time.time() < deadline:
        if http_status("/health") == 200:
            log = ssh(f"grep -a 'iniciando' {LOG} | tail -1", check=False, quiet=True)
            facts = ssh(
                f"nproc; awk '/MemTotal/ {{print $2}}' /proc/meminfo; "
                f"findmnt -no FSTYPE --target {DATA_DIR}; uname -r; {NODE_BIN} -v",
                quiet=True,
            )
            lines = [line for line in facts.stdout.splitlines() if line.strip()]
            if lines[2] == "tmpfs":
                raise RuntimeError(f"{DATA_DIR} esta em tmpfs: a gravacao nao chega ao disco.")
            return {
                "workers_log": log.stdout.strip(),
                "nproc": int(lines[0]),
                "mem_total_kb": int(lines[1]),
                "data_fstype": lines[2],
                "kernel": lines[3],
                "node": lines[4],
            }
        time.sleep(1)
    ssh(f"tail -40 {LOG}", check=False)
    raise RuntimeError("A aplicacao nao respondeu em /health.")


def warmup(workers):
    for _ in range(max(4, workers * 4)):
        try:
            connection = HTTPConnection(APP_HOST, APP_PORT, timeout=60)
            connection.request("GET", IO_PATH)
            connection.getresponse().read()
        except Exception as error:
            print("aquecimento falhou:", error, flush=True)


# -------------------------------------------------------------------- carga

def load_for(concurrency, duration_s):
    stop_at = time.perf_counter() + duration_s
    buckets = [[] for _ in range(concurrency)]

    def worker(bucket):
        connection = HTTPConnection(APP_HOST, APP_PORT, timeout=120)
        while time.perf_counter() < stop_at:
            started = time.perf_counter()
            status, server_ms, sync_ms = 0, None, None
            try:
                connection.request("GET", IO_PATH)
                response = connection.getresponse()
                raw = response.read()
                status = response.status
                if status == 200:
                    body = json.loads(raw)
                    server_ms, sync_ms = float(body["elapsedMs"]), float(body["syncMs"])
            except Exception:
                try:
                    connection.close()
                except Exception:
                    pass
                connection = HTTPConnection(APP_HOST, APP_PORT, timeout=120)
            bucket.append((time.perf_counter() - started, status, server_ms, sync_ms))

    threads = [Thread(target=worker, args=(bucket,)) for bucket in buckets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [item for bucket in buckets for item in bucket]


# ---------------------------------------------------------------- coletor

def start_monitor():
    ssh(f"rm -f {METRICS}; pkill -f 'vm_monito[r].py' || true", check=False, quiet=True)
    ssh(
        f"setsid python3 {MONITOR} {METRICS} {DATA_DIR} >/tmp/vm-monitor.out 2>&1 </dev/null & "
        f"echo monitor-started",
        quiet=True,
    )
    time.sleep(1.2)


def stop_monitor():
    ssh("pkill -f 'vm_monito[r].py' || true", check=False, quiet=True)
    completed = ssh(f"cat {METRICS}", timeout=30, check=False, quiet=True)
    samples = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "cpu_pct" in record:
            samples.append(record)
    return samples


# ------------------------------------------------------------------ resumos

def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def summarize_requests(records, wall):
    ok = [item for item in records if item[1] == 200]
    latencies = [item[0] * 1000 for item in ok]
    server = [item[2] for item in ok if item[2] is not None]
    syncs = [item[3] for item in ok if item[3] is not None]
    mean = lambda values: round(statistics.fmean(values), 2) if values else 0
    summary = {
        "requests_total": len(records),
        "requests_ok": len(ok),
        "requests_error": len(records) - len(ok),
        "throughput_rps": round(len(ok) / wall, 2),
        "latency_ms_avg": mean(latencies),
        "latency_ms_p50": round(percentile(latencies, 0.50), 2),
        "latency_ms_p95": round(percentile(latencies, 0.95), 2),
        "latency_ms_max": round(max(latencies), 2) if latencies else 0,
        "server_elapsed_ms_avg": mean(server),
        "server_sync_ms_avg": mean(syncs),
    }
    elapsed, sync = summary["server_elapsed_ms_avg"], summary["server_sync_ms_avg"]
    summary["sync_ratio"] = round(sync / elapsed, 3) if elapsed else 0
    return summary


def summarize_samples(samples):
    if not samples:
        return {}
    avg = lambda key: round(statistics.fmean(item[key] for item in samples), 2)
    peak = lambda key: round(max(item[key] for item in samples), 2)
    return {
        "samples": len(samples),
        "cpu_pct_avg": avg("cpu_pct"),
        "cpu_pct_max": peak("cpu_pct"),
        "iowait_pct_avg": avg("iowait_pct"),
        "steal_pct_avg": avg("steal_pct"),
        "blocked_avg": avg("blocked"),
        "disk_write_mbps_avg": avg("disk_write_mbps"),
        "disk_write_mbps_max": peak("disk_write_mbps"),
        "mem_used_mb_avg": avg("mem_used_mb"),
        "mem_used_mb_max": peak("mem_used_mb"),
        "node_rss_mb_avg": avg("node_rss_mb"),
    }


# -------------------------------------------------------------------- main

def main():
    previous = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else {}
    results = {"environment": previous.get("environment", {}), "scenarios": previous.get("scenarios", [])}
    done = {item["scenario"] for item in results["scenarios"]}

    try:
        for scenario in SCENARIOS:
            if scenario["name"] in done:
                print("pulando", scenario["name"], flush=True)
                continue
            print(f"\n=== {scenario['name']} ===", flush=True)
            ensure_scenario(scenario["cpus"], scenario["memory_mb"], scenario["write_mbps"])
            observed = start_app()
            print(observed["workers_log"], flush=True)
            if not results["environment"]:
                results["environment"] = {
                    "vm": VM,
                    "virtualbox": run([VBOXMANAGE, "--version"], timeout=30, quiet=True).stdout.strip(),
                    "kernel": observed["kernel"],
                    "node": observed["node"],
                    "data_fstype": observed["data_fstype"],
                    "request_mb": REQUEST_MB,
                }
            warmup(observed["nproc"])
            levels = []
            for concurrency in CONCURRENCY_LEVELS:
                print(f"concorrencia {concurrency}", flush=True)
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
            results["scenarios"].append({"scenario": scenario["name"], **scenario, **observed, "levels": levels})
            RESULTS_PATH.write_text(json.dumps(results, indent=2))
            done.add(scenario["name"])

        print("\nRestaurando a VM para 1 vCPU, 1024 MB e sem limite de banda.", flush=True)
        ensure_scenario(1, 1024, 1000)
        start_app()
        print("Para remover o grupo de banda, com a VM desligada:", flush=True)
        print(f"  VBoxManage bandwidthctl {VM} remove {BW_GROUP}", flush=True)
    finally:
        if ASKPASS.exists():
            ASKPASS.unlink()


if __name__ == "__main__":
    main()
