#!/usr/bin/env python3
"""Amostra CPU e memória da VM uma vez por segundo. Roda dentro do convidado."""

import glob
import json
import sys
import time


def read_cpu():
    with open("/proc/stat", encoding="utf-8") as handle:
        parts = handle.readline().split()
    values = [int(item) for item in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    steal = values[7] if len(values) > 7 else 0
    return sum(values), idle, steal


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
    previous = read_cpu()
    with open(output_path, "w", encoding="utf-8") as handle:
        while True:
            time.sleep(1)
            current = read_cpu()
            total_delta = current[0] - previous[0]
            idle_delta = current[1] - previous[1]
            steal_delta = current[2] - previous[2]
            previous = current
            if total_delta <= 0:
                continue
            guest = 100.0 * (total_delta - idle_delta - steal_delta) / total_delta
            steal = 100.0 * steal_delta / total_delta
            record = {
                "cpu_pct": round(guest, 2),
                "steal_pct": round(steal, 2),
                "mem_used_mb": round(mem_used_mb(), 1),
                "node_rss_mb": round(node_rss_mb(), 1),
            }
            handle.write(json.dumps(record) + "\n")
            handle.flush()


if __name__ == "__main__":
    main()
