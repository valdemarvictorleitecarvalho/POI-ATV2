"""Amostra, uma vez por segundo, o uso de recursos da aplicação e da máquina."""

import time

import cgroup


def system_cpu():
    with open("/proc/stat") as handle:
        lines = handle.read().splitlines()
    values = [int(v) for v in lines[0].split()[1:]]
    blocked = next(int(l.split()[1]) for l in lines if l.startswith("procs_blocked"))
    return sum(values), values[4], blocked


def node_rss_mb(pids):
    total_kb = 0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/status") as handle:
                for line in handle:
                    if line.startswith("VmRSS:"):
                        total_kb += int(line.split()[1])
        except OSError:
            continue
    return total_kb / 1024


def sample(dev, cpus, stop, samples, interval=1.0):
    prev_app = cgroup.stats(dev)
    prev_sys = system_cpu()
    prev_t = time.perf_counter()
    while not stop.wait(interval):
        app = cgroup.stats(dev)
        sys_now = system_cpu()
        now = time.perf_counter()
        dt = now - prev_t
        total = sys_now[0] - prev_sys[0]
        samples.append({
            "cpu_pct": round(100 * (app[0] - prev_app[0]) / (dt * cpus), 2),
            "iowait_pct": round(100 * (sys_now[1] - prev_sys[1]) / total, 2) if total else 0,
            "blocked": sys_now[2],
            "disk_write_mbps": round((app[2] - prev_app[2]) / dt / 2**20, 2),
            "mem_used_mb": round(app[1] / 2**20, 1),
            "node_rss_mb": round(node_rss_mb(cgroup.pids()), 1),
        })
        prev_app, prev_sys, prev_t = app, sys_now, now
