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
from yulon.catalog.catalog import CatalogEntry, ClientSpec, load_catalog
from yulon.catalog.families import clientdir

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
    """Upstream hardcodes `-j $(nproc+1)` inside the RUN, so on AzerothCore the CPU count is it.

    The number is only offered on an engine that has a pane to set it, so this
    asks a Windows box; the Linux wording has its own test below.
    """
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(platform_id="windows", vm=_vm(8, cpus=16)))
    assert verdict(report, preflight.JOBS_CHECK) == "warn"
    said = [check for check in report.checks if check.name == preflight.JOBS_CHECK][0]
    assert "17 parallel jobs" in said.detail
    assert "3 CPUs" in said.remedy  # 8 GB affords 4 jobs, so 3 CPUs is 4 jobs


def test_the_jobs_warning_counts_the_jobs_this_entry_s_build_actually_runs() -> None:
    """The 2026-09-02 finding: it computed `nproc+1` for a build fixed at `make -j2`.

    A CMaNGOS entry's `-j` is the `{{MAKE_JOBS}}` token `composegen` fills from
    `cmangos.dockerfile.make_jobs`, so its job count is the same on a 4-core box
    and a 64-core one. On the box that warned — 15 CPUs, 19.5 GB — this check
    read only `facts`, so it announced "16 parallel compilers" for a Vanilla
    install that was about to run two, and named a CPU count as the lever for a
    number no CPU count can move.
    """
    assert CLIENT_ENTRY is not None, "a CMaNGOS entry is what this is about"
    native = CLIENT_ENTRY.install.native
    assert native is not None and native.cmangos is not None
    jobs = native.cmangos.dockerfile.make_jobs

    gate_box = facts(vm=_vm(19.5, cpus=15))
    cmangos = preflight.evaluate(CLIENT_ENTRY, SERVER_DIR, gate_box)
    said = [c for c in cmangos.checks if c.name == preflight.JOBS_CHECK][0]
    assert said.verdict == "pass", "two jobs on 19.5 GB is not a machine to warn"
    assert f"{jobs} parallel jobs" in said.detail
    assert "15" not in said.detail and "16" not in said.detail

    # …and the same facts on the AzerothCore entry, whose Dockerfile really does
    # take its job count from `nproc`, still warn. The entry is what differs.
    azerothcore = preflight.evaluate(ENTRY, SERVER_DIR, gate_box)
    warned = [c for c in azerothcore.checks if c.name == preflight.JOBS_CHECK][0]
    assert warned.verdict == "warn"
    assert "16 parallel jobs" in warned.detail


def test_a_fixed_job_count_that_outruns_the_memory_does_not_name_the_cpu_count() -> None:
    """Lowering the CPUs cannot change a number that lives in `catalog.json`."""
    assert CLIENT_ENTRY is not None
    report = preflight.evaluate(
        CLIENT_ENTRY, SERVER_DIR, facts(platform_id="windows", vm=_vm(2.5, cpus=1))
    )
    said = [c for c in report.checks if c.name == preflight.JOBS_CHECK][0]
    assert said.verdict == "warn"
    assert "set Docker Desktop to" not in said.remedy, "no CPU count moves a data-fixed `-j`"
    assert "the CPU count is not the lever" in said.remedy
    assert "more memory" in said.remedy


def test_the_cpu_remedy_is_only_offered_where_a_cpu_setting_exists() -> None:
    """Gate defect D4, 2026-08-31: "set Docker Desktop to 8 CPUs" on Docker Engine.

    Docker Engine has no Resources pane and no CPU setting at all — it hands the
    container every host CPU — so naming a number to set there is advice that
    cannot be carried out. It is also the same box the warning was measured
    non-predictive on, and the sentence now says so rather than leaving a user
    with a warning and no move.
    """
    engine = [
        check
        for check in preflight.evaluate(ENTRY, SERVER_DIR, facts(vm=_vm(19.5, cpus=15))).checks
        if check.name == preflight.JOBS_CHECK
    ][0]
    assert engine.verdict == "warn"
    assert "Docker Desktop" not in engine.remedy and "CPUs —" not in engine.remedy
    assert "no CPU setting to lower" in engine.remedy
    assert "caution" in engine.remedy

    desktop = [
        check
        for check in preflight.evaluate(
            ENTRY, SERVER_DIR, facts(platform_id="windows", vm=_vm(19.5, cpus=15))
        ).checks
        if check.name == preflight.JOBS_CHECK
    ][0]
    assert "Docker Desktop" in desktop.remedy


def test_the_jobs_row_never_refuses_because_it_was_measured_non_predictive() -> None:
    """16 compilers on 19.5 GB finished with nothing OOM-killed (Ubuntu gate, 2026-08-31).

    One roomy box completing does not refute 2 GB-per-job for a 6 GB one, so the
    row stays; what it may never do is block an install that three real ones
    have since shown will finish.
    """
    for cpus, memory in ((15, 19.5), (16, 4.0), (64, 6.0)):
        report = preflight.evaluate(ENTRY, SERVER_DIR, facts(vm=_vm(memory, cpus=cpus)))
        said = [c for c in report.checks if c.name == preflight.JOBS_CHECK][0]
        assert said.verdict != "refuse", (cpus, memory)


def test_the_floors_add_when_both_needs_are_on_one_volume() -> None:
    """48 GB, not 40: the build cache and the checkout grow out of the same free space."""
    roomy_apart = facts(data_root_free=45 * GIB, server_dir_free=45 * GIB, same_volume=False)
    assert preflight.evaluate(ENTRY, SERVER_DIR, roomy_apart).ok()
    one_drive = facts(data_root_free=45 * GIB, server_dir_free=45 * GIB, same_volume=True)
    report = preflight.evaluate(ENTRY, SERVER_DIR, one_drive)
    assert not report.ok()
    assert "share one drive" in report.message()


def _space_rows(report: preflight.Report) -> list[preflight.Check]:
    return [check for check in report.checks if check.name.startswith("free space on ")]


def test_one_drive_gets_one_free_space_row_rather_than_the_same_one_twice() -> None:
    """The 2026-09-02 duplicate: two rows, one measurement, identical text.

    Once `floors_gb()` has replaced both floors with the added pair, the two
    rows read the same filesystem against the same figure and print the same
    sentence — "51 GB free; 60 GB is the comfortable figure", twice. A log that
    says a thing twice reads as two things to go and fix. Both gate boxes are
    this shape (measured 2026-09-02: `/var/lib/docker` and `/home` on one
    `/dev/sda2` on yulon-ubuntu, one `/dev/nvme0n1p3` on m910q), so it is the
    ordinary Linux case and not an edge one.
    """
    one_drive = facts(data_root_free=51 * GIB, server_dir_free=51 * GIB, same_volume=True)
    rows = _space_rows(preflight.evaluate(ENTRY, SERVER_DIR, one_drive))
    assert len(rows) == 1, [row.line() for row in rows]
    assert rows[0].verdict == "warn"
    assert rows[0].detail.count("51 GB free") == 1
    # Still findable as the data-root row, which is how a caller matches it.
    assert rows[0].name.startswith("free space on Docker's disk")
    assert "the server folder" in rows[0].name

    # Two drives, two questions, two rows — unchanged.
    apart = facts(data_root_free=51 * GIB, server_dir_free=51 * GIB, same_volume=False)
    assert len(_space_rows(preflight.evaluate(ENTRY, SERVER_DIR, apart))) == 2


def test_the_one_drive_row_reports_the_smaller_of_the_two_readings() -> None:
    """Two `statvfs` calls at two moments; if they disagree, the smaller can refuse."""
    report = preflight.evaluate(
        ENTRY,
        SERVER_DIR,
        facts(data_root_free=90 * GIB, server_dir_free=45 * GIB, same_volume=True),
    )
    row = _space_rows(report)[0]
    assert row.verdict == "refuse" and "45 GB free" in row.detail

    # A reading that was not taken is not a zero: the other one still answers.
    half = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(data_root_free=None, server_dir_free=90 * GIB, same_volume=True)
    )
    assert _space_rows(half)[0].verdict == "pass"

    # Neither taken is unchecked — never a pass, and never a fabricated refusal.
    neither = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(data_root_free=None, server_dir_free=None, same_volume=True)
    )
    assert _space_rows(neither)[0].verdict == "unchecked"
    assert "not a pass" in _space_rows(neither)[0].detail


def test_macos_keeps_both_rows_because_there_they_are_not_duplicates() -> None:
    """Collapsing them on a Mac would promote an `unchecked` to a pass.

    "Docker's disk" on macOS is the HOST volume holding a sparse image behind
    its own cap, so an ample reading there is `unchecked`; the same number for
    the server folder is a real pass. One row cannot hold both answers.
    """
    report = preflight.evaluate(
        ENTRY,
        SERVER_DIR,
        facts(
            platform_id="macos",
            data_root_free=200 * GIB,
            server_dir_free=200 * GIB,
            same_volume=True,
        ),
    )
    assert len(_space_rows(report)) == 2
    assert verdict(report, "free space on Docker's disk") == "unchecked"
    assert verdict(report, "free space on the server folder") == "pass"


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
    assert {check.name for check in report.unchecked()} >= {"memory", preflight.JOBS_CHECK}


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
        (2, 8.0, "pass", None),  # jobs=3, affordable=4
        (4, 8.0, "warn", "3 CPUs"),  # jobs=5, affordable=4 -> warn with 3 CPUs
        (8, 6.0, "warn", "2 CPUs"),  # jobs=9, affordable=3 -> warn with 2 CPUs
        (16, 4.0, "warn", "1 CPUs"),  # jobs=17, affordable=2 -> warn with 1 CPUs
    ],
)
def test_cpu_vs_memory_heuristics(
    cpus: int, memory_gb: float, expected_verdict: str, expected_remedy_cpu: str | None
) -> None:
    """The AzerothCore arithmetic, on the one engine whose remedy names a CPU count.

    The `(4, 8.0)` row used to declare `pass` and then be corrected to `warn` by
    an `if` inside the body, so the table said one thing and the assertion did
    another — and any real change of that case would have been absorbed by the
    override rather than caught. The table now carries the verdict it asserts.
    """
    report = preflight.evaluate(
        ENTRY, SERVER_DIR, facts(platform_id="windows", vm=_vm(memory_gb, cpus=cpus))
    )
    cpu_check = [check for check in report.checks if check.name == preflight.JOBS_CHECK][0]
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


# -- the client folder (7.3, I.8) ---------------------------------------------
#
# Two facts, and both of them have three answers. `client_checks` carries
# `clientdir.validate()`'s own verdicts through unchanged, `unchecked` rows
# included; `client_bind` is `True`, `False` or "nobody asked", and the last of
# those must never read as the middle one. A client Docker cannot see mounts as
# an EMPTY directory, so the alternative to refusing here is an extractor
# finding no archives after the image was built.


def _client_entry() -> CatalogEntry | None:
    """The first entry whose family block carries a `ClientSpec`; None if none does yet."""
    for entry in load_catalog().games:
        native = entry.install.native
        if native is not None and native.cmangos is not None:
            return entry
    return None


CLIENT_ENTRY = _client_entry()
needs_client_entry = pytest.mark.skipif(
    CLIENT_ENTRY is None, reason="no catalog entry has a ClientSpec yet"
)


def _spec_of(entry: CatalogEntry) -> ClientSpec:
    native = entry.install.native
    assert native is not None and native.cmangos is not None
    return native.cmangos.client


def _a_client(root: Path, spec: ClientSpec) -> Path:
    """A folder built from the spec's own rules, so no game literal lands in this file."""
    client = root / "client"
    (client / clientdir.DATA_DIR).mkdir(parents=True)
    if spec.required_file is not None:
        required = client.joinpath(*spec.required_file.split("/"))
        required.parent.mkdir(parents=True, exist_ok=True)
        required.write_bytes(b"")
    return client


def _client_gather(entry: CatalogEntry, server_dir: Path, **overrides: object) -> preflight.Facts:
    """`gather()` on a Linux box where every seam answers, minus what the test replaces."""
    seams: dict[str, object] = dict(
        platform_id=lambda: "linux",
        docker_ready=lambda: True,
        vm_resources=lambda: None,
        data_root=lambda: None,
        disk_free=lambda _p: 100 * GIB,
        dir_problem=lambda _p: None,
        bind_mount_ok=lambda _p: True,
        port_conflicts=lambda: [],
        probe_port=lambda host, port: platform_module.PortProbe(host, port, "unknown", ""),
        selinux=lambda: None,
        fs_type=lambda _p: None,
    )
    seams.update(overrides)
    return preflight.gather(entry, server_dir, **seams)  # type: ignore[arg-type]


def test_facts_carry_no_client_checks_by_default() -> None:
    assert facts().client_checks == () and facts().client_bind is None


def test_evaluate_appends_the_client_checks_and_a_client_refusal_blocks() -> None:
    refusal = preflight.Check(
        "the client folder", "refuse", "no client folder was chosen", "Pick one."
    )
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(client_checks=(refusal,)))
    assert verdict(report, "the client folder") == "refuse"
    assert not report.ok()
    assert "no client folder was chosen" in report.message()


def test_evaluate_carries_a_client_unchecked_through_as_unchecked() -> None:
    """`clientdir` answers `unchecked` where it could not look, and that is not a pass.

    Folding it into either neighbour is the defect this module is built against:
    rounded up, an unreadable client reports as fine; rounded down, a machine
    that would have installed is refused.
    """
    shrug = preflight.Check(clientdir.MPQ_CHECK, "unchecked", "could not be listed", "Look again.")
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(client_checks=(shrug,)))
    assert verdict(report, clientdir.MPQ_CHECK) == "unchecked"
    assert report.ok()
    assert [check.name for check in report.unchecked()] == [clientdir.MPQ_CHECK]


@needs_client_entry
def test_a_client_docker_cannot_see_is_refused_and_an_unprobed_one_is_unchecked() -> None:
    assert CLIENT_ENTRY is not None
    ok = (preflight.Check("the client folder", "pass", "fine"),)
    refused = preflight.evaluate(
        CLIENT_ENTRY, SERVER_DIR, facts(client_checks=ok, client_bind=False)
    )
    assert verdict(refused, "sharing the client with Docker") == "refuse"
    assert not refused.ok()
    assert "archives would be invisible" in refused.message()
    unprobed = preflight.evaluate(
        CLIENT_ENTRY, SERVER_DIR, facts(client_checks=ok, client_bind=None)
    )
    assert verdict(unprobed, "sharing the client with Docker") == "unchecked"
    assert unprobed.ok(), "nobody asked is not a refusal"
    shrugged = [c for c in unprobed.checks if c.name == "sharing the client with Docker"][0]
    assert "not a pass" in shrugged.detail
    fine = preflight.evaluate(CLIENT_ENTRY, SERVER_DIR, facts(client_checks=ok, client_bind=True))
    assert verdict(fine, "sharing the client with Docker") == "pass"
    # The client's probe is its own row: the server folder still has one, and
    # neither answers for the other.
    assert verdict(fine, "sharing the folder with Docker") == "pass"


@needs_client_entry
def test_the_client_bind_row_does_not_answer_for_the_server_folder_or_the_reverse() -> None:
    """Two folders, two mounts, two rows. One `bind_mount` said both once."""
    assert CLIENT_ENTRY is not None
    ok = (preflight.Check("the client folder", "pass", "fine"),)
    report = preflight.evaluate(
        CLIENT_ENTRY, SERVER_DIR, facts(client_checks=ok, bind_mount=False, client_bind=True)
    )
    assert verdict(report, "sharing the folder with Docker") == "refuse"
    assert verdict(report, "sharing the client with Docker") == "pass"


@needs_client_entry
def test_no_client_bind_check_when_the_client_itself_was_refused() -> None:
    """A mount test on a folder that is not a client answers a question nobody asked.

    Its `unchecked` line would read as a second thing to go and fix, next to
    the one sentence that says what to change.
    """
    assert CLIENT_ENTRY is not None
    refusal = (preflight.Check("the client folder", "refuse", "not a folder", "Pick one."),)
    report = preflight.evaluate(
        CLIENT_ENTRY, SERVER_DIR, facts(client_checks=refusal, client_bind=None)
    )
    assert [c.name for c in report.checks if "sharing the client" in c.name] == []
    # ...and the same entry with a client that passed DOES get the row, so the
    # assertion above is about the refusal and not about the entry.
    passing = (preflight.Check("the client folder", "pass", "fine"),)
    kept = preflight.evaluate(
        CLIENT_ENTRY, SERVER_DIR, facts(client_checks=passing, client_bind=None)
    )
    assert [c.name for c in kept.checks if "sharing the client" in c.name] == [
        "sharing the client with Docker"
    ]


def test_an_entry_with_no_client_spec_never_gets_a_client_bind_row() -> None:
    """AzerothCore extracts nothing from a client, so the row would be a puzzle."""
    passing = (preflight.Check("the client folder", "pass", "fine"),)
    report = preflight.evaluate(ENTRY, SERVER_DIR, facts(client_checks=passing, client_bind=False))
    assert all("sharing the client" not in check.name for check in report.checks)
    assert report.ok()


def test_gather_leaves_the_client_alone_for_an_entry_without_a_client_spec(tmp_path: Path) -> None:
    def never(_dir: Path | None, _spec: object) -> tuple[preflight.Check, ...]:
        raise AssertionError("validated a client for an entry that needs none")

    probed: list[Path] = []

    def probe(path: Path) -> bool | None:
        probed.append(path)
        return True

    got = _client_gather(
        ENTRY,
        tmp_path / "server",
        bind_mount_ok=probe,
        client_dir=tmp_path / "client",
        client_validate=never,
    )
    assert got.client_checks == () and got.client_bind is None
    assert probed == [tmp_path / "server"]


@needs_client_entry
def test_gather_validates_the_client_and_bind_probes_it_only_when_docker_answered(
    tmp_path: Path,
) -> None:
    assert CLIENT_ENTRY is not None
    client = tmp_path / "client"
    (client / clientdir.DATA_DIR).mkdir(parents=True)
    probed: list[Path] = []
    validated: list[Path | None] = []

    # A pass and an `unchecked`, because `gather()` carries what the rules said
    # through untouched: a row it quietly dropped or rounded would reach
    # `evaluate()` as a machine nobody had a doubt about.
    answers = (
        preflight.Check("the client folder", "pass", "fine"),
        preflight.Check(clientdir.MPQ_CHECK, "unchecked", "could not be listed", "Look again."),
    )

    def validate(client_dir: Path | None, _spec: object) -> tuple[preflight.Check, ...]:
        validated.append(client_dir)
        return answers

    def probe(path: Path) -> bool | None:
        probed.append(path)
        return True

    with_docker = _client_gather(
        CLIENT_ENTRY,
        tmp_path / "server",
        docker_ready=lambda: True,
        bind_mount_ok=probe,
        client_dir=client,
        client_validate=validate,
    )
    assert validated == [client]
    assert probed == [tmp_path / "server", client]
    assert with_docker.client_bind is True
    assert with_docker.client_checks == answers
    probed.clear()
    validated.clear()
    without = _client_gather(
        CLIENT_ENTRY,
        tmp_path / "server",
        docker_ready=lambda: False,
        bind_mount_ok=probe,
        client_dir=client,
        client_validate=validate,
    )
    assert probed == [] and without.client_bind is None
    assert validated == [client], "the folder rules need no daemon and still run"
    assert without.client_checks, "the folder rules need no daemon and still run"


@needs_client_entry
def test_a_client_the_rules_refused_is_left_unprobed_rather_than_probed_and_failed(
    tmp_path: Path,
) -> None:
    """`None` because nobody asked, not `False` because a container looked and saw nothing.

    A bool holds two of those three answers, and the folder rules have already
    settled the question a mount test would be asked about — `evaluate()` drops
    the row for exactly this case, so the 30-second container probe would buy a
    fact that is thrown away, about a path the rules just said is not a client.
    """
    assert CLIENT_ENTRY is not None
    probed: list[Path] = []

    def probe(path: Path) -> bool | None:
        probed.append(path)
        return False

    def refuse(_dir: Path | None, _spec: object) -> tuple[preflight.Check, ...]:
        return (preflight.Check("the client folder", "refuse", "not a client", "Pick one."),)

    got = _client_gather(
        CLIENT_ENTRY,
        tmp_path / "server",
        bind_mount_ok=probe,
        client_dir=tmp_path / "client",
        client_validate=refuse,
    )
    assert probed == [tmp_path / "server"], "the client was not worth a container"
    assert got.client_bind is None


@needs_client_entry
def test_the_default_client_validate_is_clientdir_validate_with_preflights_free_space(
    tmp_path: Path,
) -> None:
    """No seam given: the real rules run, and they measure the CLIENT's own drive.

    Through `gather()`'s `disk_free`, not `shutil` — the client sits on a drive
    of its own, and binding the module default here would make this row an
    answer about the machine the suite happens to run on.
    """
    assert CLIENT_ENTRY is not None
    spec = _spec_of(CLIENT_ENTRY)
    client = _a_client(tmp_path, spec)
    asked: list[Path] = []

    def disk_free(path: Path) -> int | None:
        asked.append(path)
        return 1 * GIB if path == client else 100 * GIB

    got = _client_gather(CLIENT_ENTRY, tmp_path / "server", disk_free=disk_free, client_dir=client)
    named = {check.name: check for check in got.client_checks}
    assert client in asked
    assert named[clientdir.CLIENT_CHECK].verdict == "pass"
    assert named[clientdir.SPACE_CHECK].verdict == "warn"
    assert "1 GB free" in named[clientdir.SPACE_CHECK].detail


@needs_client_entry
def test_an_entry_that_needs_a_client_and_was_given_none_is_refused_by_the_real_rules(
    tmp_path: Path,
) -> None:
    assert CLIENT_ENTRY is not None
    probed: list[Path] = []

    def probe(path: Path) -> bool | None:
        probed.append(path)
        return True

    got = _client_gather(CLIENT_ENTRY, tmp_path / "server", bind_mount_ok=probe, client_dir=None)
    assert [check.verdict for check in got.client_checks] == ["refuse"]
    assert got.client_bind is None
    assert probed == [tmp_path / "server"]
    report = preflight.evaluate(CLIENT_ENTRY, tmp_path / "server", got)
    assert not report.ok()
    assert clientdir.PICK_THE_CLIENT in report.message()
