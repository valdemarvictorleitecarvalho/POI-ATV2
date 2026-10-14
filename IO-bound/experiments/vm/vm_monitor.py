#!/usr/bin/env python3
"""Amostra CPU, iowait, disco e memoria da VM uma vez por segundo.

Roda dentro do convidado:
    python3 vm_monitor.py /tmp/stress-metrics.jsonl /home/osboxes/io-bound-backend/data

A primeira linha do arquivo identifica o dispositivo observado; as demais sao amostras.
"""

import glob
import json
import os
import sys
import time
from pathlib import Path


def disk_name(path):
    """Nome do disco que contem o diretorio, como aparece em /proc/diskstats."""
    st = os.stat(path)
    node = Path(os.path.realpath(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}"))
    if (node / "partition").exists():
        node = node.parent
    return node.name


def read_cpu():
    with open("/proc/stat", encoding="utf-8") as handle:
        lines = handle.read().splitlines()
    values = [int(item) for item in lines[0].split()[1:]]
    blocked = next((int(l.split()[1]) for l in lines if l.startswith("procs_blocked")), 0)
    iowait = values[4] if len(values) > 4 else 0
    steal = values[7] if len(values) > 7 else 0
    return sum(values), values[3] + iowait, iowait, steal, blocked


def sectors_written(device):
    with open("/proc/diskstats", encoding="utf-8") as handle:
        for line in handle:
            parts = line.split()
            if len(parts) > 9 and parts[2] == device:
                return int(parts[9])
    return 0


def mem_used_mb():
    info = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            key, raw = line.split(":", 1)
            info[key] = int(raw.strip().split()[0])
    return (info["MemTotal"] - info["MemAvailable"]) / 1024


def node_rss_mb():
    total_kb = 0
    for status_path in glob.glob("/proc/[0-9]*/status"):
        name = ""
        rss_kb = 0
        try:
            with open(status_path, encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("Name:"):
                        name = line.split()[1]
                    elif line.startswith("VmRSS:"):
                        rss_kb = int(line.split()[1])
        except OSError:
            continue
        if name == "node":
            total_kb += rss_kb
    return total_kb / 1024


def main():
    output_path = sys.argv[1]
    device = disk_name(sys.argv[2] if len(sys.argv) > 2 else ".")
    previous = read_cpu()
    prev_sectors = sectors_written(device)
    prev_time = time.perf_counter()

    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"device": device}) + "\n")
        handle.flush()
        while True:
            time.sleep(1)
            current = read_cpu()
            sectors = sectors_written(device)
            now = time.perf_counter()
            total = current[0] - previous[0]
            elapsed = now - prev_time
            if total <= 0 or elapsed <= 0:
                previous, prev_sectors, prev_time = current, sectors, now
                continue
            record = {
                "cpu_pct": round(100.0 * (total - (current[1] - previous[1]) - (current[3] - previous[3])) / total, 2),
                "iowait_pct": round(100.0 * (current[2] - previous[2]) / total, 2),
                "steal_pct": round(100.0 * (current[3] - previous[3]) / total, 2),
                "blocked": current[4],
                "disk_write_mbps": round((sectors - prev_sectors) * 512 / elapsed / 2**20, 2),
                "mem_used_mb": round(mem_used_mb(), 1),
                "node_rss_mb": round(node_rss_mb(), 1),
            }
            handle.write(json.dumps(record) + "\n")
            handle.flush()
            previous, prev_sectors, prev_time = current, sectors, now


if __name__ == "__main__":
    main()
