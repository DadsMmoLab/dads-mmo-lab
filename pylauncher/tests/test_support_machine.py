"""The machine's size in `system-info.txt` (T636): CPU, RAM, Docker's view, `.wslconfig`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import docker, platform
from yulon.support import machine
from yulon.support.machine import Probe, machine_lines, wslconfig_lines
from yulon.support.sources import Sources, system_info

CPUINFO = """processor\t: 0
model name\t: AMD Ryzen 7 5800X 8-Core Processor
physical id\t: 0
core id\t\t: 0

processor\t: 1
model name\t: AMD Ryzen 7 5800X 8-Core Processor
physical id\t: 0
core id\t\t: 0

processor\t: 2
model name\t: AMD Ryzen 7 5800X 8-Core Processor
physical id\t: 0
core id\t\t: 1
"""
MEMINFO = "MemTotal:       16384000 kB\nMemFree:         100 kB\nMemAvailable:    8192000 kB\n"
WINDOWS_OUT = (
    "name=Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz\ncores=8\n"
    "total_kb=16658432\nfree_kb=4194304\n"
)
VM_STAT = (
    "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
    "Pages free:                               1000.\n"
    "Pages inactive:                           2000.\n"
    "Pages speculative:                        500.\n"
)


def _probe(
    platform_id: str,
    *,
    files: dict[str, str] | None = None,
    commands: dict[str, str] | None = None,
    logical: int | None = 16,
    home: Path = Path("/home/bob"),
) -> Probe:
    files = files or {}
    commands = commands or {}

    def read(path: Path) -> str | None:
        return files.get(str(path).replace("\\", "/"))

    def run(argv: list[str]) -> str | None:
        return commands.get(argv[0] if argv[0] != "sysctl" else argv[-1])

    return Probe(platform_id, run, read, lambda: logical, lambda: home)


def test_linux_names_cpu_cores_and_ram() -> None:
    probe = _probe("linux", files={"/proc/cpuinfo": CPUINFO, "/proc/meminfo": MEMINFO})
    assert machine_lines(probe) == [
        "CPU: AMD Ryzen 7 5800X 8-Core Processor",
        "Cores: 2 physical, 16 logical",
        "RAM: 15.6 GiB total, 7.8 GiB free",
    ]


def test_windows_asks_powershell_and_reads_wslconfig() -> None:
    probe = _probe(
        "win32",
        files={"/home/bob/.wslconfig": "[wsl2]\nmemory=4GB\nprocessors=2\nswap=0\n"},
        commands={"powershell": WINDOWS_OUT},
        logical=8,
    )
    assert machine_lines(probe) == [
        "CPU: Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz",
        "Cores: 8 physical, 8 logical",
        "RAM: 15.9 GiB total, 4.0 GiB free",
        "WSL2 limits (.wslconfig): memory=4GB, processors=2",
    ]


def test_macos_asks_sysctl_and_vm_stat() -> None:
    probe = _probe(
        "darwin",
        commands={
            "machdep.cpu.brand_string": "Apple M2\n",
            "hw.physicalcpu": "8\n",
            "hw.memsize": "17179869184\n",
            "vm_stat": VM_STAT,
        },
        logical=8,
    )
    assert machine_lines(probe) == [
        "CPU: Apple M2",
        "Cores: 8 physical, 8 logical",
        "RAM: 16.0 GiB total, 0.1 GiB free",
    ]


@pytest.mark.parametrize("platform_id", ["linux", "win32", "darwin"])
def test_a_probe_that_answers_nothing_is_a_plain_could_not_read(platform_id: str) -> None:
    lines = machine_lines(_probe(platform_id, logical=None))
    assert lines[:3] == [
        "CPU: could not read the model",
        "Cores: could not read physical, could not read logical",
        "RAM: could not read",
    ]


def test_a_raising_probe_still_gives_the_lines() -> None:
    def boom(_: list[str]) -> str | None:
        raise RuntimeError("no powershell")

    def no_cores() -> int | None:
        raise RuntimeError("no cores")

    probe = Probe("win32", boom, lambda _: None, no_cores, lambda: Path("/home/bob"))
    lines = machine_lines(probe)
    assert lines[0] == "CPU: could not read the model"
    assert "could not read logical" in lines[1]


def test_free_ram_alone_missing_says_so() -> None:
    probe = _probe(
        "linux", files={"/proc/cpuinfo": CPUINFO, "/proc/meminfo": "MemTotal: 2097152 kB\n"}
    )
    assert machine_lines(probe)[2] == "RAM: 2.0 GiB total, could not read free"


def test_wslconfig_reads_only_the_wsl2_size_keys() -> None:
    text = (
        "[wsl2]\nkernel=C:\\\\Users\\\\bob\\\\k\nmemory = 8GB\n# processors=99\n"
        "[experimental]\nmemory=1GB\n"
    )
    probe = _probe("win32", files={"/home/bob/.wslconfig": text})
    assert wslconfig_lines(probe) == ["WSL2 limits (.wslconfig): memory=8GB"]


def test_wslconfig_absent_and_without_size_lines() -> None:
    assert wslconfig_lines(_probe("win32")) == ["WSL2 limits (.wslconfig): none set (no file)"]
    probe = _probe("win32", files={"/home/bob/.wslconfig": "[wsl2]\nswap=0\n"})
    assert wslconfig_lines(probe) == ["WSL2 limits (.wslconfig): no memory or processors line"]


def test_wslconfig_never_prints_a_value_that_is_not_a_size() -> None:
    text = "[wsl2]\nmemory=C:\\Users\\bob\\secret\nprocessors=bob@example.com\n"
    probe = _probe("win32", files={"/home/bob/.wslconfig": text})
    out = "\n".join(machine_lines(probe))
    assert "bob" not in out
    assert "Users" not in out


def test_the_lines_carry_no_username_or_home_folder() -> None:
    probe = _probe(
        "win32",
        files={"/home/bob/.wslconfig": "[wsl2]\nmemory=4GB\n"},
        commands={"powershell": WINDOWS_OUT},
        home=Path("/home/bob"),
    )
    text = "\n".join(machine_lines(probe))
    assert "bob" not in text
    assert "/home" not in text


def test_engine_size_asks_docker_info_and_parses_ncpu_and_memtotal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[list[str], float | None, str | None]] = []

    def fake(
        argv: list[str], cwd: Path | None = None, timeout: float | None = None, *, wsl_distro=None
    ) -> subprocess.CompletedProcess[str]:
        seen.append((argv, timeout, wsl_distro))
        return subprocess.CompletedProcess(argv, 0, "4 8318255104\n", "")

    monkeypatch.setattr(docker, "_docker", fake)
    assert docker.engine_size(wsl_distro="Ubuntu", timeout=10.0) == (4, 8318255104)
    assert seen == [(["info", "--format", "{{.NCPU}} {{.MemTotal}}"], 10.0, "Ubuntu")]


@pytest.mark.parametrize(
    ("code", "out"), [(1, "4 8318255104"), (0, ""), (0, "4"), (0, "x y"), (0, "<no value> 3")]
)
def test_engine_size_is_none_when_docker_does_not_say(
    monkeypatch: pytest.MonkeyPatch, code: int, out: str
) -> None:
    monkeypatch.setattr(
        docker,
        "_docker",
        lambda argv, *a, **k: subprocess.CompletedProcess(argv, code, out, ""),
    )
    assert docker.engine_size() is None


def _sources() -> Sources:
    return Sources(platform.config_dir(), None, ())


def test_system_info_carries_the_machine_and_dockers_view() -> None:
    text = system_info(
        _sources(),
        lambda _: "27.0.1",
        machine=lambda: ["CPU: X", "RAM: Y"],
        engine=lambda distro: (4, 8 * 1024**3),
    )
    assert "CPU: X\nRAM: Y\n" in text
    assert "Docker on this machine sees: 4 CPUs, 8.0 GiB memory" in text


def test_system_info_says_when_docker_will_not_say_or_raises() -> None:
    def boom(_: str | None) -> tuple[int, int] | None:
        raise RuntimeError("x")

    text = system_info(_sources(), lambda _: "1", machine=lambda: [], engine=lambda d: None)
    assert "Docker on this machine sees: could not read" in text
    text = system_info(_sources(), lambda _: "1", machine=lambda: [], engine=boom)
    assert "Docker on this machine sees: could not read" in text


def test_system_info_does_not_ask_the_machine_probes_of_a_silent_docker() -> None:
    asked: list[str | None] = []

    def engine(distro: str | None) -> tuple[int, int] | None:
        asked.append(distro)
        return (1, 1)

    text = system_info(
        _sources(), lambda _: "1", silent_targets={None}, machine=lambda: [], engine=engine
    )
    assert asked == []
    assert "Docker on this machine sees: skipped" in text


def test_default_probe_reports_on_this_host() -> None:
    lines = machine.machine_lines()
    assert lines[0].startswith("CPU: ")
    assert lines[2].startswith("RAM: ")


def test_system_info_asks_each_wsl_distros_docker_for_its_size(tmp_path: Path) -> None:
    from yulon.support.sources import InstallFacts

    wsl = InstallFacts("wow-wotlk", "0badc0de", tmp_path / "w", "Ubuntu", None)
    sources = Sources(platform.config_dir(), None, (wsl,))
    text = system_info(
        sources,
        lambda _: "1",
        machine=lambda: [],
        engine=lambda distro: (2, 2 * 1024**3) if distro == "Ubuntu" else None,
    )
    assert "Docker in WSL distro Ubuntu sees: 2 CPUs, 2.0 GiB memory" in text
    assert "Docker on this machine sees: could not read" in text
