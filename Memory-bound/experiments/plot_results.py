#!/usr/bin/env python3
"""Gera os gráficos comparativos a partir de results.json."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent


def memory_label(memory_mb):
    return f"{memory_mb // 1024} GB" if memory_mb >= 1024 and memory_mb % 1024 == 0 else f"{memory_mb} MB"


def scenario_label(scenario):
    if scenario.get("cpus") and scenario.get("memory_mb"):
        return f"{scenario['cpus']} vCPU, {memory_label(scenario['memory_mb'])}"
    return scenario["scenario"]


def column(scenario, key):
    """Pontos (concorrência, valor) dos níveis que têm a métrica."""
    return [
        (level["concurrency"], level[key] * 100 if key == "error_rate" else level[key])
        for level in scenario["levels"]
        if key in level
    ]


def new_axis(title, ylabel, concurrency):
    figure, axis = plt.subplots(figsize=(8, 4.8))
    axis.set_xlabel("Concorrência (requisições simultâneas)")
    axis.set_ylabel(ylabel)
    axis.set_title(title)
    axis.set_xscale("log", base=2)
    axis.set_xticks(concurrency)
    axis.set_xticklabels([str(item) for item in concurrency])
    axis.minorticks_off()
    axis.grid(True, linestyle=":", linewidth=0.8)
    return figure, axis


def finish(figure, axis, path, ylim=None):
    if ylim is not None:
        axis.set_ylim(*ylim)
    axis.legend()
    figure.tight_layout()
    figure.savefig(path, dpi=140)
    plt.close(figure)


def draw(results, key, ylabel, title, path, concurrency, ylim=None, ram_lines=False):
    figure, axis = new_axis(title, ylabel, concurrency)
    drawn = False
    for scenario in results:
        points = column(scenario, key)
        if not points:
            continue
        xs, ys = zip(*points)
        (line,) = axis.plot(xs, ys, marker="o", linewidth=2, label=scenario_label(scenario))
        drawn = True
        if ram_lines and scenario.get("memory_mb"):
            axis.axhline(scenario["memory_mb"], color=line.get_color(), linestyle="--", linewidth=1, alpha=0.6)
    if not drawn:
        plt.close(figure)
        print(f"sem dados para {path.name}; ignorado")
        return
    if ram_lines:
        axis.plot([], [], color="gray", linestyle="--", label="RAM da VM (tracejado)")
    finish(figure, axis, path, ylim)


def draw_server_time(results, path, concurrency):
    figure, axes = plt.subplots(1, 2, figsize=(11, 4.6), sharex=True)
    for axis, key, title in (
        (axes[0], "server_alloc_ms_avg", "Alocação do bloco"),
        (axes[1], "server_access_ms_avg", "Acessos aleatórios"),
    ):
        for scenario in results:
            points = column(scenario, key)
            if points:
                xs, ys = zip(*points)
                axis.plot(xs, ys, marker="o", linewidth=2, label=scenario_label(scenario))
        axis.set_title(title)
        axis.set_xlabel("Concorrência (requisições simultâneas)")
        axis.set_ylabel("Tempo médio no servidor (ms)")
        axis.set_xscale("log", base=2)
        axis.set_xticks(concurrency)
        axis.set_xticklabels([str(item) for item in concurrency])
        axis.minorticks_off()
        axis.grid(True, linestyle=":", linewidth=0.8)
    axes[0].legend()
    figure.suptitle("Tempo gasto no servidor por requisição")
    figure.tight_layout()
    figure.savefig(path, dpi=140)
    plt.close(figure)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default=str(ROOT / "results.json"))
    parser.add_argument("--figures", default=str(ROOT / "figures"))
    args = parser.parse_args()

    results = json.loads(Path(args.results).read_text(encoding="utf-8"))
    figures = Path(args.figures)
    figures.mkdir(exist_ok=True, parents=True)
    concurrency = sorted({level["concurrency"] for item in results for level in item["levels"]})

    draw(results, "throughput_rps", "Vazão (requisições/s)",
         "Vazão por demanda e provisionamento", figures / "vazao.png", concurrency)
    draw(results, "latency_ms_p95", "Latência p95 (ms)",
         "Latência p95 por demanda e provisionamento", figures / "latencia_p95.png", concurrency)
    draw(results, "error_rate", "Requisições com erro (%)",
         "Taxa de erro por demanda e provisionamento", figures / "taxa_erro.png", concurrency, ylim=(0, 100))
    draw(results, "mem_used_mb_max", "Memória em uso, pico (MB)",
         "Memória em uso na VM durante o teste", figures / "memoria.png", concurrency, ram_lines=True)
    draw(results, "swap_used_mb_max", "Swap em uso, pico (MB)",
         "Uso de swap na VM durante o teste", figures / "swap.png", concurrency)
    draw(results, "cpu_pct_avg", "CPU média da VM (%)",
         "Utilização de CPU da VM durante o teste", figures / "cpu.png", concurrency, ylim=(0, 100))
    draw_server_time(results, figures / "tempo_servidor.png", concurrency)
    print(f"figuras em {figures}")


if __name__ == "__main__":
    main()
