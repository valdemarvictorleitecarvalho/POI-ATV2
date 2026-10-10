#!/usr/bin/env python3
"""Teste de stress local do backend IO-bound.

Cada cenário provisiona a aplicação com cgroups (CPUs, memória e banda de
escrita em disco). A carga é gerada neste processo, fora do grupo.
Precisa de root: sudo env "PATH=$PATH" python3 run_stress.py
"""

import json
import os
import platform
import shutil
import signal
import statistics
import subprocess
import time
from http.client import HTTPConnection
from pathlib import Path
from threading import Event, Thread

import cgroup
import monitor

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT.parent
DATA_DIR = APP_DIR / "data"
RESULTS_PATH = ROOT / "results.json"
LOG_PATH = ROOT / "app.log"
HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", 3000))
IO_PATH = "/io?sizeKb=256&syncs=4"
DURATION_S = int(os.environ.get("DURATION_S", 25))
CONCURRENCY_LEVELS = [1, 2, 4, 8, 16]
SCENARIOS = [
    {"name": "5mbps-1cpu-1gb", "write_mbps": 5, "cpus": 1, "memory_mb": 1024},
    {"name": "10mbps-1cpu-1gb", "write_mbps": 10, "cpus": 1, "memory_mb": 1024},
    {"name": "20mbps-1cpu-1gb", "write_mbps": 20, "cpus": 1, "memory_mb": 1024},
    {"name": "10mbps-2cpu-2gb", "write_mbps": 10, "cpus": 2, "memory_mb": 2048},
]


def environment():
    cpu_model = next(
        (l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")),
        platform.processor(),
    )
    mem_kb = next(int(l.split()[1]) for l in open("/proc/meminfo") if l.startswith("MemTotal"))
    dev, disk = cgroup.disk_of(DATA_DIR)
    return {
        "cpu_model": cpu_model,
        "cpus_total": os.cpu_count(),
        "mem_total_mb": mem_kb // 1024,
        "kernel": platform.release(),
        "cgroup": "v2" if cgroup.is_v2() else "v1",
        "disk": disk,
        "device": dev,
        "node": subprocess.run([node_bin(), "-v"], capture_output=True, text=True).stdout.strip(),
    }


def node_bin():
    return os.environ.get("NODE_BIN") or shutil.which("node") or "node"


def http_get(path, timeout=5):
    connection = HTTPConnection(HOST, PORT, timeout=timeout)
    connection.request("GET", path)
    response = connection.getresponse()
    return response.status, response.read()


def start_app():
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    DATA_DIR.mkdir()
    env = {**os.environ, "PORT": str(PORT), "DATA_DIR": str(DATA_DIR)}
    env.pop("WEB_CONCURRENCY", None)
    log = open(LOG_PATH, "w")
    process = subprocess.Popen(
        [node_bin(), "dist/main.js"],
        cwd=APP_DIR,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        preexec_fn=cgroup.join,
        start_new_session=True,
    )
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if http_get("/health")[0] == 200:
                time.sleep(1)
                line = next((l for l in LOG_PATH.read_text().splitlines() if "iniciando" in l), "")
                return process, line.strip()
        except OSError:
            pass
        time.sleep(0.5)
    raise RuntimeError("A aplicação não respondeu em /health:\n" + LOG_PATH.read_text())


def stop_app(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        os.killpg(process.pid, signal.SIGKILL)


def load_for(concurrency, duration_s):
    stop_at = time.perf_counter() + duration_s
    buckets = [[] for _ in range(concurrency)]

    def worker(bucket):
        connection = HTTPConnection(HOST, PORT, timeout=120)
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
                connection.close()
                connection = HTTPConnection(HOST, PORT, timeout=120)
            bucket.append((time.perf_counter() - started, status, server_ms, sync_ms))

    threads = [Thread(target=worker, args=(b,)) for b in buckets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [item for bucket in buckets for item in bucket]


def percentile(values, fraction):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))]


def summarize_requests(records, wall):
    ok = [r for r in records if r[1] == 200]
    lat = [r[0] * 1000 for r in ok]
    mean = lambda xs: round(statistics.fmean(xs), 2) if xs else 0
    return {
        "requests_total": len(records),
        "requests_ok": len(ok),
        "requests_error": len(records) - len(ok),
        "throughput_rps": round(len(ok) / wall, 2),
        "latency_ms_avg": mean(lat),
        "latency_ms_p50": round(percentile(lat, 0.50), 2),
        "latency_ms_p95": round(percentile(lat, 0.95), 2),
        "latency_ms_max": round(max(lat), 2) if lat else 0,
        "server_elapsed_ms_avg": mean([r[2] for r in ok]),
        "server_sync_ms_avg": mean([r[3] for r in ok]),
    }


def summarize_samples(samples):
    if not samples:
        return {}
    avg = lambda k: round(statistics.fmean(s[k] for s in samples), 2)
    peak = lambda k: round(max(s[k] for s in samples), 2)
    return {
        "samples": len(samples),
        "cpu_pct_avg": avg("cpu_pct"),
        "cpu_pct_max": peak("cpu_pct"),
        "iowait_pct_avg": avg("iowait_pct"),
        "blocked_avg": avg("blocked"),
        "disk_write_mbps_avg": avg("disk_write_mbps"),
        "mem_used_mb_avg": avg("mem_used_mb"),
        "mem_used_mb_max": peak("mem_used_mb"),
        "node_rss_mb_avg": avg("node_rss_mb"),
    }


def run_level(concurrency, dev, cpus):
    stop, samples = Event(), []
    sampler = Thread(target=monitor.sample, args=(dev, cpus, stop, samples))
    sampler.start()
    started = time.perf_counter()
    records = load_for(concurrency, DURATION_S)
    wall = time.perf_counter() - started
    stop.set()
    sampler.join()
    return {
        "concurrency": concurrency,
        "wall_s": round(wall, 2),
        **summarize_requests(records, wall),
        **summarize_samples(samples),
    }


def warmup():
    for _ in range(8):
        http_get(IO_PATH, timeout=60)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Execute como root: sudo env "PATH=$PATH" python3 run_stress.py')
    if not (APP_DIR / "dist" / "main.js").exists():
        raise SystemExit("Compile antes: npm install && npm run build")

    DATA_DIR.mkdir(exist_ok=True)
    previous = json.loads(RESULTS_PATH.read_text()) if RESULTS_PATH.exists() else {}
    results = {"environment": environment(), "scenarios": previous.get("scenarios", [])}
    done = {s["scenario"] for s in results["scenarios"]}
    print(json.dumps(results["environment"]), flush=True)

    try:
        for scenario in SCENARIOS:
            if scenario["name"] in done:
                print("pulando", scenario["name"], flush=True)
                continue
            print(f"\n=== {scenario['name']} ===", flush=True)
            group = cgroup.create(scenario["cpus"], scenario["memory_mb"], scenario["write_mbps"], DATA_DIR)
            process, workers_log = start_app()
            print(workers_log, flush=True)
            try:
                warmup()
                levels = []
                for concurrency in CONCURRENCY_LEVELS:
                    time.sleep(2)
                    level = run_level(concurrency, group["device"], scenario["cpus"])
                    print(json.dumps(level), flush=True)
                    levels.append(level)
            finally:
                stop_app(process)
            results["scenarios"].append(
                {"scenario": scenario["name"], **scenario, **group, "workers_log": workers_log, "levels": levels}
            )
            RESULTS_PATH.write_text(json.dumps(results, indent=2))
    finally:
        cgroup.destroy()
        shutil.rmtree(DATA_DIR, ignore_errors=True)


if __name__ == "__main__":
    main()
