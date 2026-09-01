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

from yulon import docker, git
from yulon import platform as platform_module
from yulon.catalog import composegen, preflight
from yulon.catalog.catalog import load_catalog

ENTRY = load_catalog().get("wow-wotlk")
NATIVE = ENTRY.install.native
assert NATIVE is not None
GIB = preflight.GIB
SERVER_DIR = Path("/home/pk/wow")


def facts(**overrides: object) -> preflight.Facts:
    """A machine that passes everything, minus whatever the test breaks.

    `platform_id` is `linux` here, not `macos`, deliberately: on Linux the
    Docker data root is a real host directory, so an ample free-space reading
    is a genuine pass. macOS is the odd one out — its data root is a sparse VM
    image behind a cap, so the same reading there is `unchecked` (never a pass),
    and the tests that care about that say `platform_id="macos"` explicitly.
    """
    base = dict(
        platform_id="linux",
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
        selinux_enforcing=False,
        server_fs_type="ext2/ext3",
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
    """When the host free space cannot be measured, it reports unchecked rather than guessing."""
    report = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(platform_id="macos", data_root=None, data_root_free=None)
    )
    unchecked = [check for check in report.unchecked() if "Docker's disk" in check.name]
    assert unchecked and "on macOS" in unchecked[0].detail
    assert report.ok()


def test_a_macos_host_that_has_plenty_of_room_is_still_unchecked_not_a_pass() -> None:
    """Host free space is an upper bound on the sparse VM's room, not a guarantee.

    Docker Desktop's `Docker.raw` is capped near 64 GB by default and fills
    while the host has hundreds of gigabytes free, so an ample host reading
    proves nothing about the build fitting. The whole design rests on it: a
    false pass here costs the same failed build the tri-state discipline
    exists to prevent.
    """
    report = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(platform_id="macos", data_root_free=200 * GIB)
    )
    assert verdict(report, "free space on Docker's disk") == "unchecked"
    assert report.ok()
    said = [check for check in report.unchecked() if "Docker's disk" in check.name][0]
    assert "not a pass" in said.detail


def test_a_macos_host_below_the_floor_is_still_a_refusal() -> None:
    """Refuse-when-low stays a refusal: the VM certainly has no more than the host does."""
    report = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(platform_id="macos", data_root_free=10 * GIB)
    )
    assert verdict(report, "free space on Docker's disk") == "refuse"
    assert not report.ok()


def test_a_folder_docker_cannot_see_is_refused_before_anything_is_written() -> None:
    """The empty-mount trap: the clone "succeeds" and the build context is empty."""
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(bind_mount=False))
    assert verdict(report, "sharing the folder") == "refuse"
    assert str(SERVER_DIR) in report.message()


def test_the_file_sharing_remedy_is_only_offered_where_that_setting_exists() -> None:
    """D4 again: "Docker Desktop's Settings → Resources" printed on Docker Engine.

    The Ubuntu gate recorded that class of defect as "set Docker Desktop to 8
    CPUs" on a box that has no Docker Desktop, and a Fedora 44 box (2026-08-30)
    hit this one: the install stopped with "add this folder to Docker Desktop's
    Settings → Resources → File sharing" on a machine where no such pane, and no
    such file-sharing list, exists. Docker Engine shares the whole filesystem.
    """
    desktop = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(platform_id="windows", bind_mount=False)
    ).message()
    assert "Settings → Resources → File sharing" in desktop

    engine = preflight.evaluate(ENTRY, SERVER_DIR, facts(bind_mount=False)).message()
    assert "Docker Desktop" not in engine and "File sharing" not in engine
    # What a Linux user can actually act on is the folder itself.
    assert "read by the user the Docker daemon runs as" in engine


def test_the_linux_remedy_rules_selinux_out_rather_than_offering_a_chcon() -> None:
    """The enforcing appendix must not hand over a command that cannot work.

    Two things were wrong with `chcon -Rt container_file_t {server_dir}`. The
    folder is routinely absent at preflight time — that is why the probe mounts
    the nearest POPULATED ancestor at all — so the pasted command answers "No
    such file or directory". And the probe runs `--security-opt label:disable`
    (`docker._probe_selinux_argv()`), so no host label can change what it saw:
    the sentence said "the check itself already runs unconfined" and then
    offered a relabel anyway.

    So the appendix now says what is true — SELinux is not the cause, look at
    the folder. Still only while enforcing: it is noise on a box without
    SELinux, and `None` means nobody could ask.
    """
    enforcing = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(bind_mount=False, selinux_enforcing=True)
    ).message()
    assert "chcon" not in enforcing, "a command that cannot change the outcome is not a remedy"
    assert "SELinux is enforcing here, but it is not what refused this" in enforcing
    assert str(SERVER_DIR) in enforcing

    for answer in (False, None):
        quiet = preflight.evaluate(
            ENTRY, SERVER_DIR, facts(bind_mount=False, selinux_enforcing=answer)
        ).message()
        assert "SELinux" not in quiet


def test_a_bind_probe_that_could_not_run_is_unchecked() -> None:
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(bind_mount=None))
    assert verdict(report, "sharing the folder") == "unchecked"
    assert report.ok()
    # The unchecked line carries the same platform-fitted advice: it named a
    # Docker Desktop settings pane on a Linux host too.
    said = [check for check in report.unchecked() if "sharing the folder" in check.name][0]
    assert "Docker Desktop" not in said.remedy


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
    """TBC with its block taken away, because since G.4 every shipped entry has one.

    The entry used to be its own example. It is a `model_copy` now rather than a
    different game, so the case this refusal exists for — floors and templates
    asked of an entry that never declared any — is still made against a real
    catalog entry and not a hand-built stub that could drift from one.
    """
    tbc = load_catalog().get("wow-tbc")
    bare = tbc.model_copy(update={"install": tbc.install.model_copy(update={"native": None})})
    report = preflight.evaluate(bare, SERVER_DIR, facts())
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


def _gather(tmp_path: Path) -> preflight.Facts:
    """`gather()` with every seam faked except the one under test."""
    return preflight.gather(
        ENTRY,
        tmp_path,
        platform_id=lambda: "macos",
        docker_ready=lambda: True,
        vm_resources=lambda: None,
        data_root=lambda: None,
        disk_free=lambda _p: 100 * GIB,
        dir_problem=lambda _p: None,
        bind_mount_ok=lambda _p: True,
        probe_port=lambda host, port: platform_module.PortProbe(host, port, "unknown", ""),
    )


def test_gather_does_not_report_this_installs_own_containers_as_a_port_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The blocker that made a resume impossible once `up` had run.

    `port_conflicts_for()` is a global scan whose own docstring says it "will
    also flag the same install's own containers (e.g. on a restart)", and
    `_port_check()` turns any non-empty result into a hard refusal. Preflight
    re-runs on every attempt, the three services carry `restart:
    unless-stopped`, and `wait_ready` is bounded at 480 s against a first
    playerbots boot the engine itself calls "many minutes" — so a `ready`
    timeout left the containers up and the next Install was told to remove the
    containers of the install it was trying to finish (review, 2026-08-23).

    The real `docker.foreign_port_conflicts()` is left wired in and only the two
    subprocess-backed answers under it are faked: a double that filters is a
    double that cannot reproduce the bug.
    """
    ours = composegen.project_name(ENTRY.id, tmp_path, platform_id=lambda: "macos")
    monkeypatch.setattr(
        docker,
        "port_conflicts",
        lambda _ports, **_kw: ["ac-authserver", "ac-worldserver", "someone-else"],
    )
    monkeypatch.setattr(
        docker,
        "container_project",
        lambda name, **_kw: ours if name.startswith("ac-") else "another-project",
    )
    got = _gather(tmp_path)
    assert got.port_conflicts == ("someone-else",)
    assert verdict(preflight.evaluate(ENTRY, tmp_path, got), "the server's ports") == "refuse"
    # …and with nothing but our own containers publishing them, it passes.
    monkeypatch.setattr(docker, "container_project", lambda _name, **_kw: ours)
    again = _gather(tmp_path)
    assert again.port_conflicts == ()
    assert verdict(preflight.evaluate(ENTRY, tmp_path, again), "the server's ports") == "pass"


def test_a_publisher_docker_will_not_name_an_owner_for_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable owner is not proof of ownership, so it is not filtered out."""
    monkeypatch.setattr(docker, "port_conflicts", lambda _ports, **_kw: ["ac-worldserver"])
    monkeypatch.setattr(docker, "container_project", lambda _name, **_kw: docker.UNREADABLE)
    assert _gather(tmp_path).port_conflicts == ("ac-worldserver",)


def test_the_windows_volume_branch_is_reachable_without_running_on_windows() -> None:
    """The injected platform has to reach `_same_volume()`, not just `gather()`.

    Reading the real platform inside one unseamed call is the pattern that once
    made this suite red on every Python 3.12+ Linux box while CI stayed green.
    """
    root = Path.cwd()
    assert preflight._same_volume(root, root, "windows") is True
    assert preflight._volume_of(root, "windows") == str(root.resolve().drive).lower()
    # POSIX asks `st_dev`, which is a different question and a different value.
    assert preflight._volume_of(root, "macos") != preflight._volume_of(root, "windows")


def test_the_bind_probe_pulls_the_same_pinned_image_the_clone_does() -> None:
    """A tag and a digest are two different image references, so this is not cosmetic.

    `alpine/git` here meant a SECOND, unpinned pull, and a bind mount of the
    user's chosen directory handed to whatever `:latest` resolved to that day —
    in a repo that pins this exact image by digest for that very reason.
    """
    assert preflight.PROBE_IMAGE == git.CONTAINER_GIT_IMAGE
    assert preflight.PROBE_IMAGE == git.ContainerGit().image
    assert "@sha256:" in preflight.PROBE_IMAGE


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


def test_gather_on_macos_assembles_platform_facts(tmp_path: Path) -> None:
    data_root_path = tmp_path / "Docker.raw"
    data_root_path.write_bytes(b"")

    got = preflight.gather(
        ENTRY,
        tmp_path / "server",
        platform_id=lambda: "macos",
        docker_ready=lambda: True,
        vm_resources=lambda: platform_module.VmResources(memory_bytes=8 * GIB, cpus=4),
        data_root=lambda: data_root_path,
        disk_free=lambda p: 120 * GIB,
        dir_problem=lambda p: None,
        bind_mount_ok=lambda p: True,
        port_conflicts=lambda: [],
        probe_port=lambda host, port: platform_module.PortProbe(host, port, "unknown", ""),
    )
    assert got.platform_id == "macos"
    assert got.docker_ready is True
    assert got.vm is not None and got.vm.cpus == 4
    assert got.data_root == data_root_path
    assert got.data_root_free == 120 * GIB
    assert got.bind_mount is True

    report = preflight.evaluate(ENTRY, tmp_path / "server", got)
    assert report.ok()
    # On macOS, ample data root free space is unchecked, not pass
    assert verdict(report, "free space on Docker's disk") == "unchecked"
    assert verdict(report, "sharing the folder with Docker") == "pass"


@pytest.mark.parametrize(
    ("cpus", "memory_gb", "expected_verdict", "expected_remedy_cpu"),
    [
        (2, 8.0, "pass", None),
        (4, 8.0, "pass", None),  # jobs=5, affordable=4 -> 5 > 4 -> warn
        (8, 6.0, "warn", "2 CPUs"),  # jobs=9, affordable=3 -> warn with 2 CPUs
        (16, 4.0, "warn", "1 CPUs"),  # jobs=17, affordable=2 -> warn with 1 CPUs
    ],
)
def test_cpu_vs_memory_heuristics(
    cpus: int, memory_gb: float, expected_verdict: str, expected_remedy_cpu: str | None
) -> None:
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(vm=_vm(memory_gb, cpus=cpus)))
    cpu_check = [check for check in report.checks if check.name == "CPU vs memory"][0]
    if cpus == 4 and memory_gb == 8.0:
        # jobs=5, affordable=4 -> warn
        assert cpu_check.verdict == "warn"
    else:
        assert cpu_check.verdict == expected_verdict
    if expected_remedy_cpu is not None:
        assert expected_remedy_cpu in cpu_check.remedy


def test_selinux_off_or_labelable_passes() -> None:
    for enforcing, fs in ((False, "ntfs"), (True, "ext2/ext3"), (True, "xfs"), (True, None)):
        report = preflight.evaluate(
            ENTRY, SERVER_DIR, facts(selinux_enforcing=enforcing, server_fs_type=fs)
        )
        assert verdict(report, "SELinux") == "pass", (enforcing, fs)
        assert report.ok()


def test_selinux_enforcing_on_an_unlabelable_drive_warns_and_names_it() -> None:
    """`:z` is omitted there, and the daemon may refuse the mount — say so before the build."""
    report = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(selinux_enforcing=True, server_fs_type="ntfs")
    )
    assert verdict(report, "SELinux") == "warn"
    assert report.ok()
    said = [check for check in report.checks if check.name == "SELinux"][0]
    assert "ntfs" in said.detail and ":z" in said.detail


def test_selinux_that_could_not_be_read_on_linux_is_unchecked_and_a_pass_elsewhere() -> None:
    linux = preflight.evaluate(ENTRY, SERVER_DIR, facts(selinux_enforcing=None))
    assert verdict(linux, "SELinux") == "unchecked"
    assert "not a pass" in [c for c in linux.checks if c.name == "SELinux"][0].detail
    mac = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(platform_id="macos", selinux_enforcing=None, server_fs_type=None)
    )
    assert verdict(mac, "SELinux") == "pass"


def test_gather_asks_selinux_only_on_linux_and_accepts_a_client_dir(tmp_path: Path) -> None:
    """SELinux is asked on Linux only; `client_dir` is accepted now and read in 7.3 (A9)."""
    asked: list[str] = []

    def selinux() -> bool | None:
        asked.append("selinux")
        return True

    def fs_type(path: Path) -> str | None:
        asked.append(f"fs:{path}")
        return "btrfs"

    common: dict[str, object] = dict(
        docker_ready=lambda: True,
        vm_resources=lambda: None,
        data_root=lambda: None,
        disk_free=lambda _p: 100 * GIB,
        dir_problem=lambda _p: None,
        bind_mount_ok=lambda _p: True,
        port_conflicts=lambda: [],
        probe_port=lambda host, port: platform_module.PortProbe(host, port, "unknown", ""),
        selinux=selinux,
        fs_type=fs_type,
    )
    got = preflight.gather(
        ENTRY, tmp_path, platform_id=lambda: "linux", client_dir=tmp_path / "client", **common
    )  # type: ignore[arg-type]
    assert got.selinux_enforcing is True and got.server_fs_type == "btrfs"
    assert asked == ["selinux", f"fs:{tmp_path}"]
    asked.clear()
    got = preflight.gather(
        ENTRY, tmp_path, platform_id=lambda: "windows", **common
    )  # type: ignore[arg-type]
    assert got.selinux_enforcing is None and got.server_fs_type is None
    assert asked == []
