"""Tests for the native install preflight (`yulon.catalog.preflight`, roadmap 6.2).

`evaluate()` is pure, so every threshold is asserted without a daemon, a disk
or a Mac. The tri-state is what most of these are about: a fact that could not
be established must come out as *unchecked*, never rounded to a pass (which
would let a doomed four-hour build start) and never to a refusal (which would
turn a stopped Docker Desktop into "your machine has 0 GB of RAM").
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import platform as platform_module
from yulon.catalog import preflight
from yulon.catalog.catalog import load_catalog

ENTRY = load_catalog().get("wow-wotlk")
NATIVE = ENTRY.install.native
assert NATIVE is not None
GIB = preflight.GIB
SERVER_DIR = Path("/home/pk/wow")


def facts(**overrides: object) -> preflight.Facts:
    """A machine that passes everything, minus whatever the test breaks."""
    base = dict(
        platform_id="macos",
        docker_ready=True,
        vm=platform_module.VmResources(memory_bytes=16 * GIB, cpus=4),
        data_root=Path("/var/lib/docker"),
        data_root_free=200 * GIB,
        server_dir_free=200 * GIB,
        same_volume=False,
        dir_problem=None,
        bind_mount=True,
        port_conflicts=(),
        ports_in_use=(),
    )
    base.update(overrides)
    return preflight.Facts(**base)  # type: ignore[arg-type]


def verdict(report: preflight.Report, name_fragment: str) -> str:
    """The verdict of one check. An exact name wins over a fragment.

    Two checks are called "the server folder" and "free space on the server
    folder", and a fragment match would silently answer for whichever came
    first — which is how a test asserting a refusal passed against a check that
    was not the one under test.
    """
    for check in report.checks:
        if check.name == name_fragment:
            return check.verdict
    matched = [check for check in report.checks if name_fragment in check.name]
    if len(matched) == 1:
        return matched[0].verdict
    raise AssertionError(f"{name_fragment!r} names {len(matched)} checks in {report.checks}")


def test_a_healthy_machine_passes_everything() -> None:
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts())
    assert report.ok()
    assert not report.warnings()
    assert not report.unchecked()


def test_too_little_memory_is_a_refusal_not_a_warning() -> None:
    """Closed by the design: yes, a hard refusal.

    Below the floor the OOM killer SIGKILLs a compiler and the symptom is
    "dies at the same low percentage every retry" with a bare `Killed` — three
    hours to learn. A false refusal costs one settings change.
    """
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(vm=_vm(4)))
    assert verdict(report, "memory") == "refuse"
    assert not report.ok()
    assert "Resources" in report.message()


def test_memory_between_the_floors_warns_and_does_not_block() -> None:
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(vm=_vm(7)))
    assert verdict(report, "memory") == "warn"
    assert report.ok()


def test_memory_that_could_not_be_read_is_unchecked_not_a_pass_and_not_a_refusal() -> None:
    """A stopped Docker Desktop prints well-formed zeroes; `None` is the honest answer."""
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(vm=None))
    assert verdict(report, "memory") == "unchecked"
    assert report.ok()  # unknown does not block…
    assert report.unchecked()  # …and is not silently a pass either
    assert "not a pass" in report.unchecked()[0].detail


def test_more_cpus_than_the_memory_affords_warns_and_names_the_number() -> None:
    """Upstream hardcodes `-j $(nproc+1)` inside the RUN, so the CPU count is the only lever."""
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(vm=_vm(8, cpus=16)))
    assert verdict(report, "CPU") == "warn"
    said = [check for check in report.checks if "CPU" in check.name][0]
    assert "3 CPUs" in said.remedy  # 8 GB affords 4 jobs, so 3 CPUs is 4 jobs


def test_the_floors_add_when_both_needs_are_on_one_volume() -> None:
    """48 GB, not 40: the build cache and the checkout grow out of the same free space."""
    roomy_apart = facts(data_root_free=45 * GIB, server_dir_free=45 * GIB, same_volume=False)
    assert preflight.evaluate(ENTRY, SERVER_DIR, roomy_apart).ok()
    one_drive = facts(data_root_free=45 * GIB, server_dir_free=45 * GIB, same_volume=True)
    report = preflight.evaluate(ENTRY, SERVER_DIR, one_drive)
    assert not report.ok()
    assert "share one drive" in report.message()


def test_a_macos_data_root_that_cannot_be_resolved_says_so_in_its_own_words() -> None:
    """Unverifiable without a Mac, so it reports unchecked rather than guessing.

    Docker Desktop's settings path, its keys, and what "free space" even means
    against a sparse `Docker.raw` are all unconfirmed by this project.
    """
    report = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(platform_id="macos", data_root=None, data_root_free=None)
    )
    unchecked = [check for check in report.unchecked() if "Docker's disk" in check.name]
    assert unchecked and "on macOS" in unchecked[0].detail
    assert report.ok()


def test_a_folder_docker_cannot_see_is_refused_before_anything_is_written() -> None:
    """The empty-mount trap: the clone "succeeds" and the build context is empty."""
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(bind_mount=False))
    assert verdict(report, "sharing the folder") == "refuse"
    assert "File sharing" in report.message()


def test_a_bind_probe_that_could_not_run_is_unchecked() -> None:
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(bind_mount=None))
    assert verdict(report, "sharing the folder") == "unchecked"
    assert report.ok()


def test_a_synced_or_network_folder_is_refused_with_the_reason() -> None:
    report = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(dir_problem="it is inside a cloud-synced folder (onedrive)")
    )
    assert verdict(report, "the server folder") == "refuse"
    assert "onedrive" in report.message()


def test_a_container_already_publishing_the_ports_refuses_but_a_bare_listener_warns() -> None:
    """Two sources, and only one of them is proof.

    Hyper-V and WSL reserve port ranges and a permission error looks exactly
    like a listener, so the socket half can only warn — hard-refusing on it
    would refuse a server that would have started.
    """
    hard = preflight.evaluate(ENTRY, SERVER_DIR, facts(port_conflicts=("ac-authserver",)))
    assert verdict(hard, "ports") == "refuse"
    soft = preflight.evaluate(ENTRY, SERVER_DIR, facts(ports_in_use=(3724,)))
    assert verdict(soft, "ports") == "warn"
    assert soft.ok()


def test_no_daemon_is_a_refusal_and_everything_under_it_is_unchecked() -> None:
    report = preflight.evaluate(
        ENTRY,
        SERVER_DIR,
        facts(docker_ready=False, vm=None, data_root=None, data_root_free=None, bind_mount=None),
    )
    assert verdict(report, "Docker") == "refuse"
    assert {check.name for check in report.unchecked()} >= {"memory", "CPU vs memory"}


def test_an_entry_with_no_native_data_is_refused_rather_than_guessed_at() -> None:
    tbc = load_catalog().get("wow-tbc")
    report = preflight.evaluate(tbc, SERVER_DIR, facts())
    assert not report.ok()
    assert "catalog.json" in report.message()


def test_gather_asks_docker_nothing_when_docker_is_not_there(tmp_path: Path) -> None:
    """No daemon means no numbers, rather than numbers a stopped daemon made up."""
    asked: list[str] = []

    def never(*_args: object, **_kwargs: object) -> object:
        asked.append("asked")
        raise AssertionError("preflight asked Docker something with no daemon running")

    got = preflight.gather(
        ENTRY,
        tmp_path,
        platform_id=lambda: "macos",
        docker_ready=lambda: False,
        vm_resources=never,  # type: ignore[arg-type]
        data_root=never,  # type: ignore[arg-type]
        disk_free=lambda _p: 100 * GIB,
        dir_problem=lambda _p: None,
        bind_mount_ok=never,  # type: ignore[arg-type]
        port_conflicts=never,  # type: ignore[arg-type]
        probe_port=lambda host, port: platform_module.PortProbe(host, port, "unknown", ""),
    )
    assert asked == []
    assert got.docker_ready is False
    assert got.vm is None and got.bind_mount is None and got.data_root is None


def test_gather_only_calls_a_port_in_use_when_the_connection_completed(tmp_path: Path) -> None:
    """`probe_tcp` reports refusal, timeout and permission errors alike as `unknown`."""
    got = preflight.gather(
        ENTRY,
        tmp_path,
        platform_id=lambda: "macos",
        docker_ready=lambda: True,
        vm_resources=lambda: None,
        data_root=lambda: None,
        disk_free=lambda _p: 100 * GIB,
        dir_problem=lambda _p: None,
        bind_mount_ok=lambda _p: True,
        port_conflicts=lambda: [],
        probe_port=lambda host, port: platform_module.PortProbe(
            host, port, "open" if port == ENTRY.ports.world else "unknown", ""
        ),
    )
    assert got.ports_in_use == (ENTRY.ports.world,)


def _vm(gigabytes: float, cpus: int = 4) -> platform_module.VmResources:
    return platform_module.VmResources(memory_bytes=int(gigabytes * GIB), cpus=cpus)


@pytest.mark.parametrize(
    ("free_gb", "expected"),
    [(200, "pass"), (50, "warn"), (10, "refuse")],
)
def test_the_data_root_floors_are_the_catalog_entry_s(free_gb: int, expected: str) -> None:
    """40 refuse / 60 warn — inherited from the Rust incidents, carried as data."""
    assert NATIVE is not None
    assert (NATIVE.min_data_root_gb, NATIVE.warn_data_root_gb) == (40.0, 60.0)
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(data_root_free=free_gb * GIB))
    assert verdict(report, "Docker's disk") == expected
