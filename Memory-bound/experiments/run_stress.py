#!/usr/bin/env python3
"""Teste de stress do backend Memory-bound.

A carga é gerada neste processo, no hospedeiro, fora da VM. A VM só executa a
aplicação e o coletor de CPU/memória/swap (guest_monitor.py).

Em cada cenário a RAM (e as vCPUs) da VM é reconfigurada. Em cada nível de
demanda, N conexões simultâneas pedem GET /memory durante DURATION segundos.
Como cada requisição segura sizeMb MB enquanto está em voo, a memória pedida à
VM é aproximadamente N x sizeMb.

Modos:
  vm      reconfigura a VM com VBoxManage, sobe a aplicação e o coletor via SSH
  attach  usa uma aplicação que já está no ar (um cenário só); o coletor roda
          via SSH, a não ser que se use --no-guest
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parent
MONITOR_LOCAL = ROOT / "guest_monitor.py"
MONITOR_REMOTE = "/tmp/guest_monitor_membound.py"
METRICS_REMOTE = "/tmp/membound-metrics.jsonl"
APP_LOG_REMOTE = "/tmp/membound.log"

DEFAULT_SCENARIOS = "1:768,1:1024,1:1536,1:2048"  # vCPUs:RAM em MB
DEFAULT_LEVELS = "1,2,4,8,12,16,24,32"


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--mode", choices=["vm", "attach"], default="vm")
    parser.add_argument("--vm", default="cav_vm", help="nome da VM no VirtualBox")
    parser.add_argument("--ssh-host", default="127.0.0.1")
    parser.add_argument("--ssh-port", type=int, default=2222)
    parser.add_argument("--ssh-user", default="osboxes")
    parser.add_argument("--app-host", default="127.0.0.1")
    parser.add_argument("--app-port", type=int, default=3000)
    parser.add_argument("--guest-node", default="/home/osboxes/node-v22.18.0-linux-x64/bin/node")
    parser.add_argument("--guest-dir", default="/home/osboxes/memory-bound-backend")
    parser.add_argument("--size-mb", type=int, default=64, help="sizeMb de cada requisição")
    parser.add_argument("--hold-ms", type=int, default=4000, help="holdMs de cada requisição")
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--duration", type=int, default=30, help="segundos por nível de demanda")
    parser.add_argument("--levels", default=DEFAULT_LEVELS, help="concorrências, separadas por vírgula")
    parser.add_argument("--scenarios", default=DEFAULT_SCENARIOS, help="vCPUs:RAM_MB separados por vírgula")
    parser.add_argument("--max-inflight-mb", type=int, default=0,
                        help="MAX_INFLIGHT_MB da aplicação (0 = desligado; deixe desligado para medir saturação)")
    parser.add_argument("--request-timeout", type=int, default=60)
    parser.add_argument("--stop-error-rate", type=float, default=0.5,
                        help="para de subir a demanda no cenário se a taxa de erro chegar a este valor")
    parser.add_argument("--stop-throughput-frac", type=float, default=0.1,
                        help="para de subir a demanda se a vazão cair abaixo desta fração do pico do cenário")
    parser.add_argument("--restore", default="1:1024",
                        help="vCPUs:RAM para deixar a VM ao final (vm), ou 'none'")
    parser.add_argument("--label", default="attach", help="nome do cenário no modo attach")
    parser.add_argument("--no-guest", action="store_true", help="attach: sem SSH, só métricas do cliente")
    parser.add_argument("--results", default=str(ROOT / "results.json"))
    parser.add_argument("--force", action="store_true", help="refaz cenários que já estão no results.json")
    return parser.parse_args()


# ---------------------------------------------------------------- VirtualBox


def find_vboxmanage():
    candidates = [
        os.environ.get("VBOXMANAGE"),
        shutil.which("VBoxManage"),
        r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe",
    ]
    for candidate in candidates:
        if candidate and (Path(candidate).exists() or shutil.which(candidate)):
            return candidate
    raise SystemExit(
        "VBoxManage não encontrado. Adicione-o ao PATH ou defina a variável VBOXMANAGE com o caminho completo."
    )


def vbox(*arguments, timeout=60, check=True):
    command = [find_vboxmanage(), *arguments]
    print("+", " ".join(command), flush=True)
    completed = subprocess.run(command, text=True, capture_output=True, timeout=timeout, check=False)
    if completed.returncode != 0 and check:
        print(completed.stderr.strip(), flush=True)
        raise RuntimeError(f"comando falhou ({completed.returncode}): {' '.join(command)}")
    return completed


def vm_info(vm):
    completed = subprocess.run(
        [find_vboxmanage(), "showvminfo", vm, "--machinereadable"],
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"showvminfo falhou: {completed.stderr.strip()}")
    info = {}
    for line in completed.stdout.splitlines():
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        info[key] = raw.strip().strip('"')
    return info


def forwarded_host_ports(info):
    ports = set()
    for key, value in info.items():
        if key.startswith("Forwarding("):
            fields = value.split(",")
            if len(fields) >= 4 and fields[3].isdigit():
                ports.add(int(fields[3]))
    return ports


def ensure_forwarding(args, info):
    wanted = [("mb-ssh", args.ssh_port, 22), ("mb-app", args.app_port, 3000)]
    present = forwarded_host_ports(info)
    running = info.get("VMState") == "running"
    for name, host_port, guest_port in wanted:
        if host_port in present:
            continue
        rule = f"{name},tcp,,{host_port},,{guest_port}"
        if running:
            vbox("controlvm", args.vm, "natpf1", rule)
        else:
            vbox("modifyvm", args.vm, "--natpf1", rule)


def poweroff_vm(args):
    vbox("controlvm", args.vm, "acpipowerbutton", check=False)
    for attempt in range(60):
        state = vm_info(args.vm).get("VMState")
        print("estado:", state, flush=True)
        if state == "poweroff":
            return
        if attempt == 20:
            print("ACPI não desligou a VM; forçando poweroff.", flush=True)
            vbox("controlvm", args.vm, "poweroff", check=False)
        time.sleep(3)
    raise RuntimeError("A VM não desligou.")


def ensure_scenario(args, guest, cpus, memory_mb):
    info = vm_info(args.vm)
    same = (
        info.get("VMState") == "running"
        and int(info.get("cpus", "0")) == cpus
        and int(info.get("memory", "0")) == memory_mb
    )
    if same:
        print(f"VM já está com {cpus} vCPU e {memory_mb} MB.", flush=True)
        ensure_forwarding(args, info)
        guest.wait_ready()
        return
    if info.get("VMState") == "running":
        poweroff_vm(args)
    ensure_forwarding(args, vm_info(args.vm))
    vbox("modifyvm", args.vm, "--cpus", str(cpus), "--memory", str(memory_mb))
    vbox("startvm", args.vm, "--type", "headless", timeout=120)
    guest.wait_ready()


# ---------------------------------------------------------------------- SSH


class Guest:
    """Acesso SSH à VM (paramiko, funciona igual em Windows, macOS e Linux)."""

    def __init__(self, host, port, user):
        self.host, self.port, self.user = host, port, user
        self.password = os.environ.get("VM_SSH_PASSWORD", "")

    def _connect(self):
        try:
            import paramiko
        except ImportError:
            raise SystemExit("Instale as dependências: pip install -r requirements.txt")
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            self.host,
            port=self.port,
            username=self.user,
            password=self.password or None,
            timeout=10,
            banner_timeout=20,
            auth_timeout=20,
            allow_agent=not self.password,
            look_for_keys=not self.password,
        )
        return client

    def run(self, command, timeout=60, check=True):
        client = self._connect()
        try:
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            code = stdout.channel.recv_exit_status()
        finally:
            client.close()
        if code != 0 and check:
            print(err.strip(), flush=True)
            raise RuntimeError(f"comando remoto falhou ({code}): {command[:80]}")
        return code, out, err

    def put(self, local, remote):
        client = self._connect()
        try:
            sftp = client.open_sftp()
            sftp.put(str(local), remote)
            sftp.close()
        finally:
            client.close()

    def wait_ready(self, attempts=60):
        if not self.password and not os.environ.get("SSH_AUTH_SOCK"):
            print("Aviso: VM_SSH_PASSWORD não definida; só funciona com chave SSH.", flush=True)
        last = None
        for _ in range(attempts):
            try:
                code, out, _ = self.run("echo up", timeout=15, check=False)
                if code == 0 and "up" in out:
                    return
            except Exception as error:  # a VM ainda está subindo
                last = error
            time.sleep(3)
        raise RuntimeError(f"SSH da VM não respondeu: {last}")


# ---------------------------------------------------------------- aplicação


def http_json(args, path, timeout=5):
    try:
        connection = HTTPConnection(args.app_host, args.app_port, timeout=timeout)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body
    except OSError:
        return 0, b""


def health_ok(args):
    return http_json(args, "/health")[0] == 200


def start_app(args, guest):
    # O texto enviado por SSH não pode conter o caminho literal do main.js: o pkill
    # casaria com o próprio shell e o encerraria. Por isso as variáveis e o [n].
    remote = f"""
pkill -f 'dist/mai[n].js' || true
sleep 0.5
bin='{args.guest_node}'
dir='{args.guest_dir}/dist'
: > {APP_LOG_REMOTE}
cd '{args.guest_dir}'
env PORT={args.app_port} MAX_INFLIGHT_MB={args.max_inflight_mb} setsid "$bin" "$dir/main.js" > {APP_LOG_REMOTE} 2>&1 < /dev/null &
echo started
"""
    guest.run(remote, timeout=30)
    deadline = time.time() + 40
    while time.time() < deadline:
        if health_ok(args):
            return observe(args, guest)
        time.sleep(1)
    _, tail, _ = guest.run(f"tail -40 {APP_LOG_REMOTE}", check=False)
    print(tail, flush=True)
    raise RuntimeError("A aplicação não respondeu em /health.")


def observe(args, guest):
    _, out, _ = guest.run(
        "nproc; awk '/MemTotal/ {print $2}' /proc/meminfo; awk '/SwapTotal/ {print $2}' /proc/meminfo; "
        f"grep -a 'iniciando' {APP_LOG_REMOTE} | tail -1",
        check=False,
    )
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    facts = {"nproc": int(lines[0]), "mem_total_kb": int(lines[1]), "swap_total_kb": int(lines[2])}
    facts["workers_log"] = lines[3] if len(lines) > 3 else ""
    return facts


# ------------------------------------------------------------------- carga


def memory_path(args):
    return f"/memory?sizeMb={args.size_mb}&holdMs={args.hold_ms}&passes={args.passes}"


def one_request(args):
    connection = HTTPConnection(args.app_host, args.app_port, timeout=args.request_timeout)
    connection.request("GET", memory_path(args))
    response = connection.getresponse()
    response.read()
    connection.close()
    return response.status


def load_for(args, concurrency, duration_s):
    path = memory_path(args)
    stop_at = time.perf_counter() + duration_s
    buckets = [[] for _ in range(concurrency)]

    def worker(bucket):
        connection = HTTPConnection(args.app_host, args.app_port, timeout=args.request_timeout)
        while time.perf_counter() < stop_at:
            started = time.perf_counter()
            status = 0
            payload = None
            try:
                connection.request("GET", path)
                response = connection.getresponse()
                raw = response.read()
                status = response.status
                if status == 200:
                    payload = json.loads(raw.decode("utf-8"))
            except Exception:
                status = 0
                try:
                    connection.close()
                except Exception:
                    pass
                connection = HTTPConnection(args.app_host, args.app_port, timeout=args.request_timeout)
            bucket.append((time.perf_counter() - started, status, payload))

    threads = [Thread(target=worker, args=(bucket,)) for bucket in buckets]
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


def mean_of(values):
    return round(statistics.fmean(values), 2) if values else 0


def summarize_requests(records, wall_s):
    ok = [item for item in records if item[1] == 200]
    latencies = [item[0] * 1000 for item in ok]
    payloads = [item[2] for item in ok if item[2]]
    total = len(records)
    errors = total - len(ok)
    return {
        "requests_total": total,
        "requests_ok": len(ok),
        "requests_error": errors,
        "requests_503": sum(1 for item in records if item[1] == 503),
        "requests_failed_connection": sum(1 for item in records if item[1] == 0),
        "error_rate": round(errors / total, 4) if total else 1.0,
        "throughput_rps": round(len(ok) / wall_s, 2),
        "latency_ms_avg": mean_of(latencies),
        "latency_ms_p50": round(percentile(latencies, 0.50), 2),
        "latency_ms_p95": round(percentile(latencies, 0.95), 2),
        "latency_ms_max": round(max(latencies), 2) if latencies else 0,
        "server_elapsed_ms_avg": mean_of([p["elapsedMs"] for p in payloads]),
        "server_alloc_ms_avg": mean_of([p["allocMs"] for p in payloads]),
        "server_access_ms_avg": mean_of([p["accessMs"] for p in payloads]),
        "inflight_at_start_mb_avg": mean_of([p["inflightAtStartMb"] for p in payloads]),
        "inflight_at_start_mb_max": max([p["inflightAtStartMb"] for p in payloads], default=0),
    }


# ----------------------------------------------------------------- monitor


def start_monitor(guest):
    guest.put(MONITOR_LOCAL, MONITOR_REMOTE)
    guest.run(f"rm -f {METRICS_REMOTE}; pkill -f 'guest_monitor_membou[n]d.py' || true", check=False)
    guest.run(
        f"setsid python3 {MONITOR_REMOTE} {METRICS_REMOTE} >/tmp/guest-monitor.out 2>&1 </dev/null & echo started"
    )
    time.sleep(1.2)


def stop_monitor(guest):
    guest.run("pkill -f 'guest_monitor_membou[n]d.py' || true", check=False)
    _, out, _ = guest.run(f"cat {METRICS_REMOTE}", timeout=30, check=False)
    samples = []
    for line in out.splitlines():
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

    def column(key):
        return [item.get(key, 0) for item in samples]

    return {
        "samples": len(samples),
        "cpu_pct_avg": mean_of(column("cpu_pct")),
        "cpu_pct_max": round(max(column("cpu_pct")), 2),
        "iowait_pct_avg": mean_of(column("iowait_pct")),
        "iowait_pct_max": round(max(column("iowait_pct")), 2),
        "steal_pct_avg": mean_of(column("steal_pct")),
        "mem_used_mb_avg": mean_of(column("mem_used_mb")),
        "mem_used_mb_max": round(max(column("mem_used_mb")), 2),
        "mem_available_mb_min": round(min(column("mem_available_mb")), 2),
        "swap_used_mb_max": round(max(column("swap_used_mb")), 2),
        "swap_in_pages": int(sum(column("swap_in_pages"))),
        "swap_out_pages": int(sum(column("swap_out_pages"))),
        "oom_kills": int(sum(column("oom_kills"))),
        "node_rss_mb_avg": mean_of(column("node_rss_mb")),
        "node_rss_mb_max": round(max(column("node_rss_mb")), 2),
    }


# --------------------------------------------------------------- cenários


def run_levels(args, guest, levels, restart, out):
    """Roda os níveis de demanda e acrescenta cada resultado em `out`."""
    peak = 0.0
    for concurrency in levels:
        restarted = False
        if not health_ok(args) and restart is not None:
            print("aplicação fora do ar; reiniciando", flush=True)
            try:
                restart()
                restarted = True
            except Exception as error:
                print("não foi possível reiniciar a aplicação:", error, flush=True)
                break
        print(f"concorrência {concurrency}", flush=True)
        time.sleep(2)
        monitor_ok = False
        if guest:
            try:
                start_monitor(guest)
                monitor_ok = True
            except Exception as error:  # a VM pode estar sem resposta sob pressão de memória
                print("coletor não iniciou:", error, flush=True)
        started = time.perf_counter()
        records = load_for(args, concurrency, args.duration)
        wall = time.perf_counter() - started
        samples = []
        if monitor_ok:
            try:
                samples = stop_monitor(guest)
            except Exception as error:
                print("coleta das métricas da VM falhou:", error, flush=True)
        level = {
            "concurrency": concurrency,
            "demanded_mb": concurrency * args.size_mb,
            "wall_s": round(wall, 2),
            "app_restarted": restarted,
            **summarize_requests(records, wall),
            **summarize_samples(samples),
        }
        print(json.dumps(level), flush=True)
        out.append(level)
        peak = max(peak, level["throughput_rps"])
        if level["error_rate"] >= args.stop_error_rate:
            print(f"taxa de erro {level['error_rate']:.0%}: não sobe mais a demanda neste cenário.", flush=True)
            level["stopped_here"] = True
            break
        if peak > 0 and level["throughput_rps"] < args.stop_throughput_frac * peak:
            print("vazão colapsou: não sobe mais a demanda neste cenário.", flush=True)
            level["stopped_here"] = True
            break


def warmup(args, nproc):
    burst = max(2, nproc * 2)
    print(f"aquecimento: {burst} requisições", flush=True)
    for _ in range(burst):
        try:
            one_request(args)
        except Exception as error:
            print("aquecimento falhou:", error, flush=True)


def parse_scenarios(text):
    scenarios = []
    for item in text.split(","):
        cpus, memory = item.split(":")
        scenarios.append(
            {"name": f"{int(cpus)}vcpu-{int(memory)}mb", "cpus": int(cpus), "memory_mb": int(memory)}
        )
    return scenarios


def run_params(args):
    return {
        "size_mb": args.size_mb,
        "hold_ms": args.hold_ms,
        "passes": args.passes,
        "duration_s": args.duration,
        "max_inflight_mb": args.max_inflight_mb,
    }


def save(path, results):
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


def load_previous(args, path):
    if path.exists() and not args.force:
        return json.loads(path.read_text(encoding="utf-8"))
    return []


def main_vm(args, levels):
    path = Path(args.results)
    results = [item for item in load_previous(args, path) if not item.get("incomplete")]
    done = {item["scenario"] for item in results}
    guest = Guest(args.ssh_host, args.ssh_port, args.ssh_user)
    for scenario in parse_scenarios(args.scenarios):
        if scenario["name"] in done:
            print("pulando", scenario["name"], flush=True)
            continue
        print(f"\n=== {scenario['name']} ===", flush=True)
        ensure_scenario(args, guest, scenario["cpus"], scenario["memory_mb"])
        observed = start_app(args, guest)
        if observed["nproc"] != scenario["cpus"]:
            print(f"AVISO: a VM vê {observed['nproc']} CPU(s), esperado {scenario['cpus']}.", flush=True)
        warmup(args, observed["nproc"])
        scenario_levels = []
        entry = {"scenario": scenario["name"], **scenario, **observed, "params": run_params(args), "levels": scenario_levels}
        try:
            run_levels(args, guest, levels, lambda: start_app(args, guest), scenario_levels)
        except BaseException:
            entry["incomplete"] = True  # guarda o que já foi medido antes de propagar o erro
            raise
        finally:
            results.append(entry)
            save(path, results)
    if args.restore != "none":
        cpus, memory = (int(item) for item in args.restore.split(":"))
        print(f"Restaurando a VM para {cpus} vCPU e {memory} MB.", flush=True)
        ensure_scenario(args, guest, cpus, memory)


def main_attach(args, levels):
    path = Path(args.results)
    results = load_previous(args, path)
    if args.label in {item["scenario"] for item in results}:
        raise SystemExit(f"O cenário '{args.label}' já existe em {path}. Use outro --label ou --force.")
    if not health_ok(args):
        raise SystemExit(f"Nada respondendo em http://{args.app_host}:{args.app_port}/health")
    guest = None if args.no_guest else Guest(args.ssh_host, args.ssh_port, args.ssh_user)
    observed = {"nproc": 1}
    if guest:
        guest.wait_ready()
        observed = observe(args, guest)
    warmup(args, observed["nproc"])
    scenario_levels = []
    run_levels(args, guest, levels, None, scenario_levels)
    entry = {
        "scenario": args.label,
        "name": args.label,
        "cpus": observed.get("nproc") if guest else None,
        "memory_mb": round(observed["mem_total_kb"] / 1024) if guest else None,
        **observed,
        "params": run_params(args),
        "levels": scenario_levels,
    }
    results.append(entry)
    save(path, results)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args()
    levels = [int(item) for item in args.levels.split(",")]
    if args.mode == "vm":
        main_vm(args, levels)
    else:
        main_attach(args, levels)
    print(f"\nResultados em {args.results}")


if __name__ == "__main__":
    main()
