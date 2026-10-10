"""Provisionamento local com cgroups: CPUs, memória e banda de escrita em disco."""

import os
import signal
import time
from pathlib import Path

ROOT = Path("/sys/fs/cgroup")
NAME = "poi-io-bound"
V1_CONTROLLERS = ("cpuset", "cpuacct", "memory", "blkio")


def is_v2():
    return (ROOT / "cgroup.controllers").exists()


def disk_of(path):
    st = os.stat(path)
    node = Path(os.path.realpath(f"/sys/dev/block/{os.major(st.st_dev)}:{os.minor(st.st_dev)}"))
    if (node / "partition").exists():
        node = node.parent
    return (node / "dev").read_text().strip(), node.name


def write(path, value):
    Path(path).write_text(f"{value}\n")


def dirs():
    if is_v2():
        return [ROOT / NAME]
    return [ROOT / c / NAME for c in V1_CONTROLLERS]


def pids():
    found = set()
    for d in dirs():
        procs = d / "cgroup.procs"
        if procs.exists():
            found.update(int(p) for p in procs.read_text().split())
    return sorted(found)


def destroy():
    for pid in pids():
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    time.sleep(0.5)
    for d in dirs():
        if d.exists():
            d.rmdir()


def create(cpus, memory_mb, write_mbps, data_dir):
    destroy()
    dev, disk = disk_of(data_dir)
    cpu_list = "0" if cpus == 1 else f"0-{cpus - 1}"
    mem = memory_mb * 1024 * 1024
    bps = write_mbps * 1024 * 1024
    if is_v2():
        try:
            write(ROOT / "cgroup.subtree_control", "+cpuset +cpu +memory +io")
        except OSError:
            pass
        g = ROOT / NAME
        g.mkdir()
        write(g / "cpuset.cpus", cpu_list)
        write(g / "memory.max", mem)
        write(g / "io.max", f"{dev} wbps={bps}")
    else:
        for d in dirs():
            d.mkdir()
        cs = ROOT / "cpuset" / NAME
        write(cs / "cpuset.mems", (cs.parent / "cpuset.mems").read_text().strip())
        write(cs / "cpuset.cpus", cpu_list)
        write(ROOT / "memory" / NAME / "memory.limit_in_bytes", mem)
        write(ROOT / "blkio" / NAME / "blkio.throttle.write_bps_device", f"{dev} {bps}")
    return {"device": dev, "disk": disk, "cpuset": cpu_list}


def join():
    """Chamado no processo filho, antes do exec: os workers herdam o grupo."""
    for d in dirs():
        write(d / "cgroup.procs", os.getpid())


def stats(dev):
    """CPU acumulada (s), memória em uso (bytes) e bytes escritos no disco pelo grupo."""
    if is_v2():
        g = ROOT / NAME
        cpu = next(int(l.split()[1]) for l in (g / "cpu.stat").read_text().splitlines()
                   if l.startswith("usage_usec")) / 1e6
        mem = int((g / "memory.current").read_text())
        written = 0
        for line in (g / "io.stat").read_text().splitlines():
            if line.startswith(dev + " "):
                written = int(dict(kv.split("=") for kv in line.split()[1:])["wbytes"])
        return cpu, mem, written
    cpu = int((ROOT / "cpuacct" / NAME / "cpuacct.usage").read_text()) / 1e9
    mem = int((ROOT / "memory" / NAME / "memory.usage_in_bytes").read_text())
    written = 0
    for line in (ROOT / "blkio" / NAME / "blkio.throttle.io_service_bytes").read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == dev and parts[1] == "Write":
            written = int(parts[2])
    return cpu, mem, written
