"""How big the machine is (T636): CPU, RAM and the WSL2 limits in `system-info.txt`.

A lag report says little without this: Docker Desktop's WSL2 VM is capped by
`.wslconfig`, often far below the PC's RAM. Every probe is bounded by
`PROBE_TIMEOUT_S`, never raises, and on any failure becomes a plain
"could not read" line, so `system-info.txt` is always written. Only numbers,
the CPU's marketing name and `.wslconfig`'s two size keys are reported:
nothing here is a secret, and no path is printed.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import runner

PROBE_TIMEOUT_S = 10.0
"""One command's bound, so a wedged `powershell` or `sysctl` costs at most this."""

_WSL_VALUE = re.compile(r"[0-9]+(?:\.[0-9]+)?\s*(?:[KMGT]?B)?", re.IGNORECASE)
_WINDOWS_PROBE = (
    "$p=@(Get-CimInstance Win32_Processor);$o=Get-CimInstance Win32_OperatingSystem;"
    "'name='+$p[0].Name;"
    "'cores='+($p|Measure-Object NumberOfCores -Sum).Sum;"
    "'total_kb='+$o.TotalVisibleMemorySize;'free_kb='+$o.FreePhysicalMemory"
)


def _run(argv: list[str]) -> str | None:
    """A command's stdout, or None when it failed, timed out or is not there."""
    try:
        proc = runner.run(argv, timeout=PROBE_TIMEOUT_S)
    except Exception:  # boundary: a probe never ends the support file
        return None
    return proc.stdout if proc.returncode == 0 else None


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


@dataclass(frozen=True)
class Probe:
    """The host and what it can be asked. Real by default; a test hands its own over."""

    platform: str = sys.platform
    run: Callable[[list[str]], str | None] = _run
    read: Callable[[Path], str | None] = _read
    logical_cores: Callable[[], int | None] = os.cpu_count
    home: Callable[[], Path] = Path.home


def gib(size_bytes: float) -> str:
    return f"{size_bytes / 1024**3:.1f} GiB"


@dataclass(frozen=True)
class _Machine:
    cpu: str | None = None
    physical: int | None = None
    total_kib: int | None = None
    free_kib: int | None = None


def _int(text: str | None) -> int | None:
    match = re.fullmatch(r"\s*(\d+)\s*", text or "")
    return int(match.group(1)) if match else None


def _linux(probe: Probe) -> _Machine:
    info = probe.read(Path("/proc/cpuinfo")) or ""
    names = re.findall(r"^model name\s*:\s*(.+)$", info, re.MULTILINE)
    cores = set(
        zip(
            re.findall(r"^physical id\s*:\s*(\d+)$", info, re.MULTILINE),
            re.findall(r"^core id\s*:\s*(\d+)$", info, re.MULTILINE),
            strict=False,
        )
    )
    mem = probe.read(Path("/proc/meminfo")) or ""
    total = re.search(r"^MemTotal:\s*(\d+) kB", mem, re.MULTILINE)
    free = re.search(r"^MemAvailable:\s*(\d+) kB", mem, re.MULTILINE)
    return _Machine(
        names[0].strip() if names else None,
        len(cores) or None,
        int(total.group(1)) if total else None,
        int(free.group(1)) if free else None,
    )


def _windows(probe: Probe) -> _Machine:
    out = probe.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_PROBE])
    fields = dict(re.findall(r"^(\w+)=(.*)$", out or "", re.MULTILINE))
    return _Machine(
        (fields.get("name") or "").strip() or None,
        _int(fields.get("cores")),
        _int(fields.get("total_kb")),
        _int(fields.get("free_kb")),
    )


def _macos(probe: Probe) -> _Machine:
    name = (probe.run(["sysctl", "-n", "machdep.cpu.brand_string"]) or "").strip() or None
    physical = _int(probe.run(["sysctl", "-n", "hw.physicalcpu"]))
    memsize = _int(probe.run(["sysctl", "-n", "hw.memsize"]))
    vm = probe.run(["vm_stat"]) or ""
    size = re.search(r"page size of (\d+) bytes", vm)
    pages = [
        re.search(rf"^Pages {kind}:\s*(\d+)\.", vm, re.MULTILINE)
        for kind in ("free", "inactive", "speculative")
    ]
    free = None
    if size and all(pages):
        free = sum(int(m.group(1)) for m in pages if m) * int(size.group(1)) // 1024
    return _Machine(name, physical, memsize // 1024 if memsize else None, free)


def wslconfig_lines(probe: Probe) -> list[str]:
    """The `[wsl2]` `memory` and `processors` values of `.wslconfig`, or what is wrong with it."""
    try:
        text = probe.read(probe.home() / ".wslconfig")
    except Exception:  # boundary: no home folder is not a reason to lose the file
        return ["WSL2 limits (.wslconfig): could not read"]
    if text is None:
        return ["WSL2 limits (.wslconfig): none set (no file)"]
    section = ""
    found: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line.strip("[] ").lower()
        elif section == "wsl2" and "=" in line:
            key, _, value = line.partition("=")
            key, value = key.strip().lower(), value.strip()
            if key in ("memory", "processors") and _WSL_VALUE.fullmatch(value):
                found.append(f"{key}={value}")
    if not found:
        return ["WSL2 limits (.wslconfig): no memory or processors line"]
    return [f"WSL2 limits (.wslconfig): {', '.join(found)}"]


def machine_lines(probe: Probe | None = None) -> list[str]:
    """The CPU, RAM and (on Windows) `.wslconfig` lines of `system-info.txt`."""
    probe = probe or Probe()
    try:
        if probe.platform.startswith("win"):
            got = _windows(probe)
        elif probe.platform == "darwin":
            got = _macos(probe)
        else:
            got = _linux(probe)
    except Exception:  # boundary: a probe never ends the support file
        got = _Machine()
    try:
        logical = probe.logical_cores()
    except Exception:  # boundary
        logical = None
    cores = f"Cores: {got.physical} physical" if got.physical else "Cores: could not read physical"
    cores += f", {logical} logical" if logical else ", could not read logical"
    lines = [f"CPU: {got.cpu}" if got.cpu else "CPU: could not read the model", cores]
    if got.total_kib:
        ram = f"RAM: {gib(got.total_kib * 1024)} total"
        ram += f", {gib(got.free_kib * 1024)} free" if got.free_kib else ", could not read free"
        lines.append(ram)
    else:
        lines.append("RAM: could not read")
    if probe.platform.startswith("win"):
        lines += wslconfig_lines(probe)
    return lines
