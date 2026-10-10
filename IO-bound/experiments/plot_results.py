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
COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
MARKERS = ["o", "s", "^", "D"]
REQUEST_MB = 0.25


def label(s):
    return f"{s['write_mbps']} MB/s, {s['cpus']} CPU, {s['memory_mb'] // 1024} GB"


def style(axis, ylabel, xs, ylim=None):
    axis.set_xlabel("Concorrência (requisições simultâneas)")
    axis.set_ylabel(ylabel)
    axis.set_xticks(xs)
    if ylim:
        axis.set_ylim(*ylim)
    axis.grid(True, linestyle=":", linewidth=0.8, color="#bbbbbb")
    for side in ("top", "right"):
        axis.spines[side].set_visible(False)


def lines(axis, scenarios, key):
    for i, s in enumerate(scenarios):
        xs = [l["concurrency"] for l in s["levels"]]
        axis.plot(xs, [l[key] for l in s["levels"]], marker=MARKERS[i], markersize=7,
                  linewidth=2, color=COLORS[i], label=label(s),
                  linestyle="--" if s["cpus"] > 1 else "-")
    return xs


def limits(axis, scenarios, value, text):
    for mbps in sorted({s["write_mbps"] for s in scenarios}):
        y = value(mbps)
        axis.axhline(y, color="#888888", linestyle="--", linewidth=1)
        axis.annotate(text(mbps, y), (1, y), xycoords=("axes fraction", "data"),
                      xytext=(-4, 4), textcoords="offset points", ha="right",
                      fontsize=8, color="#555555")


def save(figure, name, rect=(0, 0, 1, 1)):
    figure.tight_layout(rect=rect)
    figure.savefig(FIGURES / name, dpi=140)
    plt.close(figure)


def single(scenarios, key, ylabel, title, name, ylim=None, ceiling=None):
    figure, axis = plt.subplots(figsize=(8, 5.4))
    xs = lines(axis, scenarios, key)
    if ceiling:
        limits(axis, scenarios, *ceiling)
    style(axis, ylabel, xs, ylim)
    axis.set_title(title)
    axis.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=2, frameon=False)
    save(figure, name)


def main():
    scenarios = json.loads(RESULTS.read_text())["scenarios"]
    FIGURES.mkdir(exist_ok=True)
    single(scenarios, "throughput_rps", "Vazão (requisições/s)",
           "Vazão por demanda e provisionamento", "vazao.png", ylim=(0, None),
           ceiling=(lambda m: m / REQUEST_MB, lambda m, y: f"teto {y:.0f} req/s ({m} MB/s)"))
    single(scenarios, "latency_ms_p95", "Latência p95 (ms)",
           "Latência p95 por demanda e provisionamento", "latencia_p95.png", ylim=(0, None))
    single(scenarios, "disk_write_mbps_avg", "Escrita em disco (MB/s)",
           "Escrita em disco da aplicação", "disco.png", ylim=(0, None),
           ceiling=(lambda m: m, lambda m, y: f"limite {m} MB/s"))
    single(scenarios, "node_rss_mb_avg", "Memória dos processos Node (MB)",
           "Memória em uso pela aplicação", "memoria.png", ylim=(0, 2048))

    figure, (left, right) = plt.subplots(1, 2, figsize=(11, 5), sharex=True)
    xs = lines(left, scenarios, "cpu_pct_avg")
    lines(right, scenarios, "iowait_pct_avg")
    style(left, "CPU da aplicação (% do provisionado)", xs, (0, 100))
    style(right, "iowait da máquina (%)", xs, (0, 100))
    left.set_title("CPU usada pela aplicação")
    right.set_title("CPU parada esperando disco")
    handles, labels = left.get_legend_handles_labels()
    figure.legend(handles, labels, loc="lower center", ncol=4, frameon=False, fontsize=9)
    save(figure, "cpu_iowait.png", rect=(0, 0.07, 1, 1))
    print(f"figuras em {FIGURES}")


if __name__ == "__main__":
    main()
