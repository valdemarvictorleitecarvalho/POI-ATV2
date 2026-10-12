#!/usr/bin/env python3
"""Amostra CPU, memória e swap da VM uma vez por segundo. Roda dentro do convidado.

Uso: python3 guest_monitor.py saida.jsonl
"""

import glob
import json
import sys
import time


def read_cpu():
    with open("/proc/stat", encoding="utf-8") as handle:
        parts = handle.readline().split()
    values = [int(item) for item in parts[1:]]
    idle = values[3]
    iowait = values[4] if len(values) > 4 else 0
    steal = values[7] if len(values) > 7 else 0
    return sum(values), idle + iowait, iowait, steal


def read_meminfo():
    info = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            key, raw = line.split(":", 1)
            info[key] = int(raw.strip().split()[0])
    return info


def read_vmstat():
    wanted = {"pswpin": 0, "pswpout": 0, "oom_kill": 0}
    with open("/proc/vmstat", encoding="utf-8") as handle:
        for line in handle:
            key, _, raw = line.partition(" ")
            if key in wanted:
                wanted[key] = int(raw)
    return wanted


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
    previous_cpu = read_cpu()
    previous_vm = read_vmstat()
    with open(output_path, "w", encoding="utf-8") as handle:
        while True:
            time.sleep(1)
            cpu = read_cpu()
            vm = read_vmstat()
            total_delta = cpu[0] - previous_cpu[0]
            idle_delta = cpu[1] - previous_cpu[1]
            iowait_delta = cpu[2] - previous_cpu[2]
            steal_delta = cpu[3] - previous_cpu[3]
            vm_delta = {key: vm[key] - previous_vm[key] for key in vm}
            previous_cpu, previous_vm = cpu, vm
            if total_delta <= 0:
                continue
            info = read_meminfo()
            record = {
                # CPU ocupada: tudo que não é ocioso, nem espera de IO, nem roubo do hospedeiro
                "cpu_pct": round(100.0 * (total_delta - idle_delta - steal_delta) / total_delta, 2),
                "iowait_pct": round(100.0 * iowait_delta / total_delta, 2),
                "steal_pct": round(100.0 * steal_delta / total_delta, 2),
                "mem_used_mb": round((info["MemTotal"] - info["MemAvailable"]) / 1024, 1),
                "mem_available_mb": round(info["MemAvailable"] / 1024, 1),
                "swap_used_mb": round((info.get("SwapTotal", 0) - info.get("SwapFree", 0)) / 1024, 1),
                "swap_in_pages": vm_delta["pswpin"],
                "swap_out_pages": vm_delta["pswpout"],
                "oom_kills": vm_delta["oom_kill"],
                "node_rss_mb": round(node_rss_mb(), 1),
            }
            handle.write(json.dumps(record) + "\n")
            handle.flush()


if __name__ == "__main__":
    main()
