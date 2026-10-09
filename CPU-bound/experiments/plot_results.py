#!/usr/bin/env python3
"""Gera os gráficos comparativos a partir de results.json."""

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results.json"
FIGURES = ROOT / "figures"

LABELS = {
    "1vcpu-1gb": "1 vCPU, 1 GB",
    "2vcpu-1gb": "2 vCPU, 1 GB",
    "4vcpu-1gb": "4 vCPU, 1 GB",
    "2vcpu-2gb": "2 vCPU, 2 GB",
}


def series(results, key):
    lines = []
    for scenario in results:
        label = LABELS.get(scenario["scenario"], scenario["scenario"])
        xs = [level["concurrency"] for level in scenario["levels"]]
        ys = [level[key] for level in scenario["levels"]]
        lines.append((label, xs, ys))
    return lines


def draw(results, key, ylabel, title, filename, ylim=None):
    figure, axis = plt.subplots(figsize=(8, 4.8))
    for label, xs, ys in series(results, key):
        axis.plot(xs, ys, marker="o", linewidth=2, label=label)
    axis.set_xlabel("Concorrência (requisições simultâneas)")
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.set_xticks(CONCURRENCY)
    if ylim is not None:
        axis.set_ylim(*ylim)
    axis.grid(True, linestyle=":", linewidth=0.8)
    axis.legend()
    figure.tight_layout()
    figure.savefig(FIGURES / filename, dpi=140)
    plt.close(figure)


def main():
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    FIGURES.mkdir(exist_ok=True)
    global CONCURRENCY
    CONCURRENCY = results[0]["levels"] and [
        level["concurrency"] for level in results[0]["levels"]
    ]
    draw(
        results,
        "throughput_rps",
        "Vazão (requisições/s)",
        "Vazão por demanda e provisionamento",
        "vazao.png",
    )
    draw(
        results,
        "latency_ms_p95",
        "Latência p95 (ms)",
        "Latência p95 por demanda e provisionamento",
        "latencia_p95.png",
    )
    draw(
        results,
        "cpu_pct_avg",
        "CPU média da VM (%)",
        "Utilização de CPU da VM durante o teste",
        "cpu.png",
        ylim=(0, 100),
    )
    draw(
        results,
        "mem_used_mb_avg",
        "Memória em uso (MB)",
        "Memória em uso na VM durante o teste",
        "memoria.png",
        ylim=(0, 2048),
    )
    print(f"figuras em {FIGURES}")


if __name__ == "__main__":
    main()
