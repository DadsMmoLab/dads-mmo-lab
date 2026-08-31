"""Tests for the game-free install spine (`yulon.catalog.native.StagedInstaller`, roadmap 7.1).

What is asserted here is true of EVERY family: the state file and its hint
semantics, the guard, ask-forwarding, streaming, and what a stage tuple may
and may not contain. Anything AzerothCore-shaped lives in
`test_families_azerothcore.py`. The machine double is shared:
`tests/support_native.py`.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.support_native import ENTRY, IMPORTED, PARTIAL, TBC, Recorder, install
from yulon import docker, platform, resources
from yulon.catalog import composegen, native, preflight
from yulon.catalog.catalog import CatalogEntry, ReadyMarkers
from yulon.catalog.families import FAMILIES, family_for
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import DockerUnavailableError, InstallerError, InstallOptions

ORDER = ("clone-sources", "build", "import", "up")
CANARY = "hunter2-a2-canary"


# -- the state file ---------------------------------------------------------


def test_the_state_file_round_trips_the_family(tmp_path: Path) -> None:
    """`family` is the 7.1 key: a folder installed as one family is never read as another."""
    native.write_state(
        tmp_path,
        native.InstallState(
            game_id="wow-wotlk", install_id="abcd1234", family="azerothcore", completed=("build",)
        ),
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == "azerothcore"
    assert state.completed == ("build",)
    assert state.game_id == "wow-wotlk"
    assert json.loads((tmp_path / native.STATE_FILE).read_text(encoding="utf-8"))["family"] == (
        "azerothcore"
    )


def test_a_state_file_written_before_family_existed_reads_as_an_empty_family(
    tmp_path: Path,
) -> None:
    """`version` stays 1: the key is additive, and an old file is not a refusal."""
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {"version": 1, "game_id": "wow-wotlk", "install_id": "abc", "completed": ["build"]}
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == ""
    assert state.version == 1


def test_a_family_that_is_not_a_string_reads_as_an_empty_family(tmp_path: Path) -> None:
    """A junk `family` must degrade to "unknown", never to a family name.

    `""` is the one value that means "this file does not say", and the guard is
    what turns that into "trust the entry". A non-string that survived as, say,
    `None` would type-lie to every reader downstream.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps({"version": 1, "game_id": "wow-wotlk", "install_id": "abc", "family": 7}),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == ""


def test_read_state_keeps_only_the_stage_names_the_entry_has(tmp_path: Path) -> None:
    """Per-entry validation replaces the global `STAGE_ORDER` filter (7.1).

    A file naming a stage this entry does not have must not become a skip —
    and the entry, not a module constant, is what says which names exist.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "clone-core", "invent-a-stage"],
            }
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.completed == ("build",)


def test_the_same_file_reads_differently_for_two_entries_stage_tuples(tmp_path: Path) -> None:
    """`valid` is per entry, so "known stage" is not a property of the module.

    The rejection above must be the `valid` argument doing the work and not a
    surviving module-level filter: `clone-core` is a real AzerothCore stage
    name, and it is dropped only because the tuple passed in has no such name.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "clone-core"],
            }
        ),
        encoding="utf-8",
    )
    narrow = native.read_state(tmp_path, valid=ORDER)
    wide = native.read_state(tmp_path, valid=("clone-core", "build"))
    assert narrow is not None and wide is not None
    assert narrow.completed == ("build",)
    # `valid` filters; it does not sort. The file's own order is kept, because
    # `with_stage` is what wrote that order and is what normalises it again.
    assert wide.completed == ("build", "clone-core")


def test_with_stage_orders_by_the_entry_tuple_and_never_records_twice() -> None:
    fresh = native.InstallState(game_id="wow-tbc", install_id="abc", family="cmangos")
    once = fresh.with_stage("import", ORDER).with_stage("clone-sources", ORDER)
    assert once.completed == ("clone-sources", "import")
    assert once.with_stage("import", ORDER) is once
    # A name outside the entry's tuple is dropped, the rule `read_state()` applies too.
    assert once.with_stage("invent-a-stage", ORDER).completed == ("clone-sources", "import")
    assert once.with_stage("build", ORDER).last_error == ""


def test_with_stage_keeps_the_family_it_was_given() -> None:
    """Recording progress must not quietly drop the ownership claim it carries."""
    state = native.InstallState(game_id="wow-tbc", install_id="abc", family="cmangos")
    assert state.with_stage("build", ORDER).family == "cmangos"


# -- the stage model --------------------------------------------------------


def _a_context(secret: native.Secrets) -> native.StageContext:
    """The context a stage body is handed, built once so every leak test uses the same one."""
    return native.StageContext(
        server_dir=Path("/srv"),
        client_dir=None,
        state=native.InstallState("wow-tbc", "abc", "cmangos"),
        cancel=threading.Event(),
        secrets=secret,
    )


def _a_stage_that_crashes(ctx: native.StageContext) -> Iterator[str]:
    """A stage body that dies mid-run, so its frame — holding `ctx` — lands in a traceback."""
    raise InstallerError("the build stopped")
    yield ""  # pragma: no cover - makes this a generator, which is what `Stage.run` is


def _crash_dump_with_locals() -> str:
    """A crash dump that `repr()`s every frame's locals, the way a crash reporter does.

    Done in a helper and not in the test so the ONLY frames in the traceback are
    ones holding a `StageContext`: a test frame would have the bare canary
    string in its own locals and the assertion would fail for the wrong reason.
    """
    ctx = _a_context(native.Secrets(db_password=CANARY))
    try:
        for _ in native.Stage("build", _a_stage_that_crashes).run(ctx):
            pass
    except InstallerError as exc:
        plain = "".join(traceback.format_exception(exc))
        with_locals = "".join(
            traceback.TracebackException.from_exception(exc, capture_locals=True).format()
        )
        return plain + with_locals
    raise AssertionError("the stage was supposed to crash")


def test_secrets_never_print_the_password() -> None:
    """A `StageContext` ends up in tracebacks and debug logs; the value must not."""
    secret = native.Secrets(db_password=CANARY)
    assert repr(secret) == "Secrets(db_password=***)"
    assert CANARY not in str(secret)
    assert CANARY not in repr(_a_context(secret))
    assert secret.db_password == CANARY


def test_the_password_never_reaches_a_log_line_or_a_crash_dump(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Where the value would actually escape: a debug log and a dump that repr()s locals.

    `repr()` masking is only worth having if it holds at the two places the
    object is rendered by something other than the author — a log line
    interpolating the context, and a crash reporter formatting every frame's
    locals. A default dataclass `repr()` passes the first and loses the
    password in the second, which is the failure this pins.
    """
    ctx = _a_context(native.Secrets(db_password=CANARY))
    with caplog.at_level(logging.DEBUG):
        native.logger.debug(f"stage context: {ctx}")
        native.logger.debug("secrets are %r / %s", ctx.secrets, ctx.secrets)

    rendered = [record.getMessage() for record in caplog.records]
    rendered.append(_crash_dump_with_locals())
    for text in rendered:
        assert CANARY not in text


def test_a_stage_is_recorded_by_default_and_carries_its_own_cancel_note() -> None:
    """The two-argument form must MEAN `recorded=True, cancel_note=""`, not merely default to it.

    Families write most stages as `Stage(name, body)`; the spine then reads
    `recorded` to decide what a resume may skip and `cancel_note` to decide what
    a Stop costs HERE. So the contract worth pinning is the equality — the short
    form and the spelled-out form are the same stage — plus the fact that both
    fields are actually overridable, which is what makes them data and not a
    module constant.
    """

    def body(ctx: native.StageContext) -> Iterator[str]:
        yield ctx.server_dir.name

    assert native.Stage("build", body) == native.Stage("build", body, recorded=True, cancel_note="")
    assert native.Stage("up", body, recorded=False).recorded is False
    assert native.Stage("build", body, cancel_note="the build restarts").cancel_note == (
        "the build restarts"
    )


def test_a_callable_gate_answers_the_probe_and_refuses_to_reset_without_a_seam() -> None:
    """The AzerothCore pair (probe, reset) behind the family-neutral `ImportGate`."""
    calls: list[str] = []

    def probe() -> docker.ImportState:
        calls.append("probe")
        return PARTIAL

    def reset() -> tuple[str, ...]:
        calls.append("reset")
        return ("acore_world",)

    gate: native.ImportGate = native.CallableGate(probe, reset)
    assert gate.probe() is PARTIAL
    assert gate.reset() == ("acore_world",)
    assert calls == ["probe", "reset"]
    with pytest.raises(InstallerError, match="no way to clear"):
        native.CallableGate(probe, None).reset()


# -- the spine's own machinery ----------------------------------------------


def _family(
    stages_of: Callable[[native.StagedInstaller], tuple[native.Stage, ...]],
) -> type[native.StagedInstaller]:
    """A throwaway family for one test: WotLK's entry and seams, any stage tuple."""

    class Family(native.StagedInstaller):
        family = "azerothcore"

        def stages(self) -> tuple[native.Stage, ...]:
            return stages_of(self)

    return Family


def _build(
    rec: Recorder,
    family: type[native.StagedInstaller],
    entry: CatalogEntry = ENTRY,
    **overrides: object,
) -> native.StagedInstaller:
    return family(
        entry,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(**overrides),
    )


def _say(ctx: native.StageContext) -> Iterator[str]:
    yield f"in {ctx.server_dir.name}"


# -- the stage tuple --------------------------------------------------------


def test_families_maps_each_id_to_its_class_and_an_unknown_one_is_a_sentence() -> None:
    assert FAMILIES["azerothcore"] is AzerothCoreInstaller
    assert family_for(ENTRY) is AzerothCoreInstaller
    with pytest.raises(InstallerError, match="install.native"):
        family_for(TBC)  # no native block yet in 7.1


def test_a_family_must_agree_with_the_entry_about_its_family(tmp_path: Path) -> None:
    """`family` is asserted against catalog data before anything is written."""

    class Wrong(native.StagedInstaller):
        family = "cmangos"

        def stages(self) -> tuple[native.Stage, ...]:
            return (native.Stage("only", _say),)

    rec = Recorder()
    with pytest.raises(InstallerError, match="cmangos"):
        list(_build(rec, Wrong).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "clone" not in " ".join(rec.calls)


def test_a_stage_tuple_may_not_repeat_a_name_or_claim_preflight_or_guard() -> None:
    """A broken family is a `ValueError` at construction — a bug, not a user's sentence."""
    rec = Recorder()
    with pytest.raises(ValueError, match="twice"):
        _build(rec, _family(lambda me: (native.Stage("a", _say), native.Stage("a", _say))))
    with pytest.raises(ValueError, match="guard"):
        _build(rec, _family(lambda me: (native.Stage("guard", _say),)))
    with pytest.raises(ValueError, match="preflight"):
        _build(rec, _family(lambda me: (native.Stage("preflight", _say),)))


def test_an_unrecorded_stage_is_never_written_down(tmp_path: Path) -> None:
    rec = Recorder()
    family = _family(
        lambda me: (native.Stage("always", _say), native.Stage("never", _say, recorded=False))
    )
    server_dir = tmp_path / "wow"
    lines = list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))
    assert lines.count("in wow") == 2
    state = native.read_state(server_dir, valid=("always", "never"))
    assert state is not None
    assert state.completed == ("always",)
    assert state.family == "azerothcore"


def test_the_cancel_note_is_said_by_the_spine_right_after_the_stage_heading(
    tmp_path: Path,
) -> None:
    """A4: the spine yields `cancel_note` once, immediately after `--- <name>`."""
    rec = Recorder()
    family = _family(
        lambda me: (
            native.Stage("first", _say),
            native.Stage("second", _say, cancel_note="Stopping here costs nothing."),
        )
    )
    lines = list(_build(rec, family).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert lines.count("Stopping here costs nothing.") == 1
    assert lines.index("Stopping here costs nothing.") == lines.index("--- second") + 1
    assert lines.index("Stopping here costs nothing.") > lines.index("--- first")


# -- the guard: family --------------------------------------------------------


def test_a_folder_installed_as_another_family_is_refused_not_reinterpreted(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "macos"),
            family="cmangos",
        ),
    )
    rec = Recorder()
    with pytest.raises(InstallerError, match="installed as `cmangos`"):
        install(rec, server_dir)
    assert "clone" not in " ".join(rec.calls)


def test_a_state_file_without_a_family_is_adopted_and_gains_the_current_one(
    tmp_path: Path,
) -> None:
    """Files from before 7.1 have no `family`; they are ours, and the next write says so."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    (server_dir / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": ENTRY.id,
                "install_id": composegen.install_id(server_dir, platform_id=lambda: "macos"),
                "completed": [],
            }
        ),
        encoding="utf-8",
    )
    install(Recorder(images=False), server_dir)
    state = native.read_state(server_dir, valid=AzerothCoreInstaller.STAGE_NAMES)
    assert state is not None
    assert state.family == "azerothcore"


# -- preflight ----------------------------------------------------------------


def test_preflight_hands_the_client_dir_to_gather(tmp_path: Path) -> None:
    """A9: `gather()` is told the client folder from 7.1 on; 7.3 starts checking it."""
    rec = Recorder(images=False)
    seen: dict[str, object] = {}

    def gather(entry: object, server_dir: Path, **kwargs: object) -> preflight.Facts:
        seen.update(kwargs)
        return rec.gather(entry, server_dir)

    installer = _build(rec, AzerothCoreInstaller, gather=gather)
    client = tmp_path / "client"
    installer.preflight(InstallOptions(server_dir=tmp_path / "wow", client_dir=client))
    assert seen["client_dir"] == client
    assert seen["platform_id"] is installer._seams.platform_id
    assert seen["docker_ready"] is installer._seams.docker_ready


def test_the_real_gather_takes_the_client_dir_and_ignores_it_until_7_3(tmp_path: Path) -> None:
    """The seam's default must accept what the spine passes it, or only the double does.

    `Seams.gather` is typed `Callable[..., Facts]`, so mypy cannot see this
    keyword at the seam; the call is covered here instead (A.2 review finding).
    """
    facts = preflight.gather(
        ENTRY,
        tmp_path,
        client_dir=tmp_path / "client",
        platform_id=lambda: "macos",
        docker_ready=lambda: False,
    )
    assert facts.platform_id == "macos"
    assert facts.docker_ready is False


# -- the forwarded prompter, all the way down to the sudo dialog ---------------
#
# The wire (`preflight(ask=...)` → `ensure_docker(ask=...)`, A4) and the thing
# it feeds (`platform.SudoSession`, C7) were built on branches that never saw
# each other, so a green suite on either side was no evidence about the join.
# Everything below drives the REAL `platform.ensure_docker()` and
# `_ensure_docker_linux()`; only the machine underneath them is a double.

SUDO_CANARY = "hunter2-sudo-canary"


class _LinuxBox:
    """A `RunCmd` for a Linux box with no Docker and a package manager to install one with.

    Answers what `ensure_docker()` really asks a machine: `docker info` fails,
    `id -nG` says the user is not in the docker group, and every `sudo -n`
    refuses with sudo's own words — the state a clean Fedora/Arch box is in,
    and the only stderr `_needs_password()` accepts.

    `needs_password=False` is the root/NOPASSWD box, where no step ever reaches
    the session and the outcome stays `unasked`.
    """

    def __init__(self, needs_password: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.needs_password = needs_password

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "id":
            return subprocess.CompletedProcess(argv, 0, "pk wheel", "")
        if argv[:2] == ["sudo", "-n"] and self.needs_password:
            return subprocess.CompletedProcess(argv, 1, "", "sudo: a password is required")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _provisions_a_linux_box(
    run: _LinuxBox, run_input: platform.RunWithInput
) -> Callable[..., platform.ProvisionReport]:
    """The `ensure_docker` seam, wired to the real function with a fake machine under it.

    The spine hands this seam `cancel` and `ask` and nothing else, which is the
    point: the fakes are bound here, so `ask` has to travel the real parameter
    rather than one the test handed in.
    """

    def provision(**kwargs: object) -> platform.ProvisionReport:
        return platform.ensure_docker(
            run=run,
            which=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
            user="pk",
            wait_seconds=0.0,
            run_input=run_input,
            **kwargs,  # type: ignore[arg-type]
        )

    return provision


def test_the_prompter_the_spine_forwards_is_the_one_the_sudo_dialog_asks_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The join, by identity: `ask` leaves `preflight()` and arrives inside `SudoSession`.

    Not "a prompter was called" — THE prompter. `SudoSession` is spied on at
    construction, so what is asserted is the object the engine forwarded, and
    both of the questions the module docstring promises ("both via the
    forwarded `ask`") are put to it. The password it answers with then has to
    be seen reaching `sudo` on STDIN, or the wire reaches the dialog and drops
    what the dialog says.
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: False)
    built: list[object] = []
    real_session = platform.SudoSession

    def spy(ask: object, run_input: object, **kwargs: object) -> platform.SudoSession:
        built.append(ask)
        return real_session(ask, run_input, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(platform, "SudoSession", spy)

    asked: list[str] = []

    def prompter(question: str) -> str:
        asked.append(question)
        return SUDO_CANARY if question == platform.SUDO_PASSWORD_QUESTION else "n"

    fed: list[tuple[list[str], str]] = []

    def feed(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
        fed.append((argv, text))
        return subprocess.CompletedProcess(argv, 0 if text == SUDO_CANARY + "\n" else 1, "", "")

    rec = Recorder(images=False)
    installer = _build(
        rec,
        AzerothCoreInstaller,
        docker_ready=lambda: False,
        ensure_docker=_provisions_a_linux_box(_LinuxBox(), feed),
    )
    with pytest.raises(DockerUnavailableError):
        installer.preflight(InstallOptions(server_dir=tmp_path / "wow"), ask=prompter)

    assert built == [prompter], "the session was built with something other than the forwarded ask"
    assert asked[0] == platform.DOCKER_GROUP_QUESTION.format(user="pk")
    assert platform.SUDO_PASSWORD_QUESTION in asked
    assert asked.count(platform.SUDO_PASSWORD_QUESTION) == 1  # one dialog per run, still
    # The answer went to sudo, on stdin, and never as an argv element.
    assert fed[0][0] == ["sudo", "-S", "-p", "", "-v"] and fed[0][1] == SUDO_CANARY + "\n"
    assert [argv for argv, _ in fed if any(SUDO_CANARY in part for part in argv)] == []
    # ...and every later privileged step was fed it rather than re-asking.
    assert ["sudo", "-S", "-p", "", "apt-get", "update"] in [argv for argv, _ in fed]


class _NoSudoBinary:
    """A `RunWithInput` for a box with no `sudo` at all: the exec itself fails."""

    def __call__(self, argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "sudo")


def _refuses_every_password(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 1, "", "Sorry, try again.")


def _accepts(password: str) -> platform.RunWithInput:
    def feed(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0 if text == password + "\n" else 1, "", "")

    return feed


def _never_feeds(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
    raise AssertionError(f"a password was fed to {argv} when no step needed one")


def test_every_sudo_outcome_survives_the_trip_up_to_the_sentence_the_user_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SudoOutcome`'s five values, driven from the Install button to the failure dialog.

    `SudoOutcome` exists because a `bool` loses the difference between "they
    said no", "the machine said no" and "nobody could be asked" — and carrying
    it is only worth anything if it is still legible after `ensure_docker()`,
    `_preflight_lines()` and `DockerUnavailableError` have each rewritten it. A
    distinction that exists in the type and dies in the message is the exact
    failure this asserts against.

    Five values, THREE sentences, and that is the right count: `unasked` and
    `verified` are the two where the privileged steps actually ran, so the user
    is told nothing about sudo at all — the same silence, for the same reason.

    `unavailable` is the one with a wrong answer waiting for it: "run them in a
    terminal with sudo" is useless advice on a box whose `sudo` is what could
    not be run, so it must not appear there (measured in a container on
    yulon-ubuntu, 2026-08-24).
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: False)

    def message(run: _LinuxBox, run_input: platform.RunWithInput, password: str | None) -> str:
        def prompter(question: str) -> str | None:
            return password if question == platform.SUDO_PASSWORD_QUESTION else "n"

        rec = Recorder(images=False)
        installer = _build(
            rec,
            AzerothCoreInstaller,
            docker_ready=lambda: False,
            ensure_docker=_provisions_a_linux_box(run, run_input),
        )
        with pytest.raises(DockerUnavailableError) as caught:
            installer.preflight(InstallOptions(server_dir=tmp_path / "wow"), ask=prompter)
        return str(caught.value)

    unasked = message(_LinuxBox(needs_password=False), _never_feeds, None)
    verified = message(_LinuxBox(), _accepts(SUDO_CANARY), SUDO_CANARY)
    declined = message(_LinuxBox(), _accepts(SUDO_CANARY), "")
    refused = message(_LinuxBox(), _refuses_every_password, "wrong")
    unavailable = message(_LinuxBox(), _NoSudoBinary(), SUDO_CANARY)

    # The two whose steps ran carry no sudo complaint at all. (Both still carry
    # the docker-group advice, which names `sudo usermod` for a reason of its
    # own — so this asserts the absence of the SKIP sentences, not of the word.)
    for quiet in (unasked, verified):
        assert "password" not in quiet.lower(), quiet
        assert "Some steps" not in quiet, quiet
        assert "sudo could not be run" not in quiet, quiet

    # The three that did not are three different sentences, not one house one.
    # `declined` and `refused` were the same sentence until this merge review:
    # the reason is written into `skipped` and was then thrown away by the line
    # that renders `manual_steps`, which kept only the command names.
    assert "Some steps needed a password; run them in a terminal with sudo" in declined
    assert "Some steps needed a password and sudo refused the one given" in refused
    assert "sudo could not be run, so nothing was elevated" in unavailable
    assert len({declined, refused, unavailable}) == 3

    # ...and the box with no `sudo` is never sent to a terminal to run one.
    assert "terminal with sudo" not in unavailable
    assert "needed a password" not in unavailable
    assert "Some steps did not run" in unavailable


# -- secrets ------------------------------------------------------------------


def test_a_fixed_password_is_the_catalog_value(tmp_path: Path) -> None:
    assert _build(Recorder(), AzerothCoreInstaller).resolve_secrets(tmp_path) == native.Secrets(
        ENTRY.install.password.value or ""
    )


def test_a_generated_password_is_read_from_its_file_or_minted_with_the_prefix(
    tmp_path: Path,
) -> None:
    """The spine resolves before stage 1; a family's `db-password` stage only persists (A8)."""
    plan = TBC.install.password
    assert plan.mode == "generated" and plan.file is not None
    installer = AzerothCoreInstaller(TBC, seams=Recorder().seams())
    minted = installer.resolve_secrets(tmp_path).db_password
    assert minted.startswith(plan.prefix) and len(minted) == len(plan.prefix) + 16
    assert re.fullmatch(r"[0-9a-f]{16}", minted[len(plan.prefix) :])
    (tmp_path / plan.file).write_text("tbc-kept\n", encoding="utf-8")
    assert installer.resolve_secrets(tmp_path).db_password == "tbc-kept"


def test_a_password_file_that_does_not_start_with_the_prefix_is_still_the_password(
    tmp_path: Path,
) -> None:
    """`prefix` decorates what this app MINTS; it is not a shape an existing file must have.

    The shipped bash installers minted these without the dash `catalog.json`
    carries, so a folder installed before 7.1 holds a password the prefix rule
    would reject — and rejecting it would mean the install could not talk to
    its own database (A.2 review finding).
    """
    plan = TBC.install.password
    assert plan.file is not None and plan.prefix
    installer = AzerothCoreInstaller(TBC, seams=Recorder().seams())
    (tmp_path / plan.file).write_text("no-prefix-here\n", encoding="utf-8")
    assert installer.resolve_secrets(tmp_path).db_password == "no-prefix-here"


# -- the spine's own stage bodies ---------------------------------------------


def test_stage_clone_sources_clones_every_source_at_its_dest_and_refuses_what_it_does_not_own(
    tmp_path: Path,
) -> None:
    """The CMaNGOS family's clone stage; AzerothCore keeps its two historical ones."""
    rec = Recorder()
    family = _family(
        lambda me: (
            native.Stage(
                "clone-sources",
                lambda ctx: me.stage_clone_sources(
                    ctx, me.entry.emulator.sources, recorded_as="clone-sources"
                ),
            ),
        )
    )
    server_dir = tmp_path / "wow"
    list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))
    assert [spec.dest for spec in rec.clones] == [
        server_dir / source.dest for source in ENTRY.emulator.sources
    ]
    assert rec.clones[0].dest == server_dir  # dest "." IS the server dir

    # The spine's own stage obeys the same rule the family's two do: a second
    # run over a recorded, present clone touches nothing (D5).
    settled = Recorder()
    for source in ENTRY.emulator.sources:
        settled.remotes[server_dir / source.dest] = source.url
    list(_build(settled, family).run(InstallOptions(server_dir=server_dir)))
    assert settled.clones == []

    hand_made = tmp_path / "wow2" / ENTRY.emulator.sources[1].dest
    hand_made.mkdir(parents=True)
    (hand_made / "my-own-patches.cpp").write_text("mine", encoding="utf-8")
    again = Recorder()
    (tmp_path / "wow2" / ".git").mkdir()
    again.remotes[tmp_path / "wow2"] = ENTRY.emulator.sources[0].url
    # This install owns wow2 and got part-way through its clone: without the
    # record, the core checkout is somebody's own repository and
    # `refuse_unowned_checkout()` stops the run one source before the module
    # this test is about.
    native.write_state(
        tmp_path / "wow2",
        native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(tmp_path / "wow2", platform_id=lambda: "macos"),
            family="azerothcore",
        ),
    )
    with pytest.raises(InstallerError, match="has files in it but is not a checkout"):
        list(_build(again, family).run(InstallOptions(server_dir=tmp_path / "wow2")))
    assert (hand_made / "my-own-patches.cpp").read_text(encoding="utf-8") == "mine"


def test_stage_start_db_starts_the_database_even_for_an_entry_with_no_import_service(
    tmp_path: Path,
) -> None:
    """A7: the spine's start-db is family-neutral — CMaNGOS has no `db_import` and needs it."""
    no_import = ENTRY.model_copy(
        update={"containers": ENTRY.containers.model_copy(update={"db_import": None})}
    )
    rec = Recorder()
    family = _family(lambda me: (native.Stage("start-db", me.stage_start_db, recorded=False),))
    list(_build(rec, family, no_import).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "start-db" in rec.calls
    assert rec.db_started is True


def _gated(rec: Recorder) -> type[native.StagedInstaller]:
    """start-db, then an import stage with NO service: the branch table, then the family's SQL."""

    def sql(me: native.StagedInstaller) -> Callable[[native.StageContext], Iterator[str]]:
        def body(ctx: native.StageContext) -> Iterator[str]:
            yield from me.stage_import(ctx, native.CallableGate(rec.probe, rec.reset), None)
            yield "applying sql"

        return body

    return _family(
        lambda me: (
            native.Stage("start-db", me.stage_start_db, recorded=False),
            native.Stage("sql", sql(me)),
        )
    )


def test_stage_import_without_a_service_runs_the_branch_table_then_returns(
    tmp_path: Path,
) -> None:
    """A7: the five-branch probe table always runs; only the one-shot + verify need a service."""
    rec = Recorder(probe_answers=[PARTIAL])
    lines = list(_build(rec, _gated(rec)).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert rec.calls.index("start-db") < rec.calls.index("probe") < rec.calls.index("reset")
    assert "verify" not in rec.calls
    assert not any(call.startswith("one-shot:") for call in rec.calls)
    assert "applying sql" in lines
    assert any(line.startswith("Cleared acore_world") for line in lines)


def test_stage_import_without_a_service_still_skips_an_imported_database(
    tmp_path: Path,
) -> None:
    rec = Recorder(probe_answers=[IMPORTED])
    lines = list(_build(rec, _gated(rec)).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "reset" not in rec.calls
    assert any("already imported" in line for line in lines)


def test_stage_import_without_a_service_still_refuses_an_unreadable_database(
    tmp_path: Path,
) -> None:
    rec = Recorder(db_start_error="")
    family = _family(
        lambda me: (
            native.Stage(
                "sql",
                lambda ctx: me.stage_import(ctx, native.CallableGate(rec.probe, rec.reset), None),
            ),
        )
    )
    # No start-db stage ran, so the Recorder's probe answers `unreadable`, as the real one does.
    with pytest.raises(InstallerError, match="could not be asked"):
        list(_build(rec, family).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "reset" not in rec.calls


def test_ready_markers_are_filled_and_escaped_unless_the_catalog_says_regex(
    tmp_path: Path,
) -> None:
    """A3/A5: `{{REALM_HOST}}:{{WORLD_PORT}}` is filled from `INSTALL_REALM_HOST`, then escaped."""
    assert native.INSTALL_REALM_HOST == "127.0.0.1"
    rec = Recorder(images=False)
    seen: list[docker.ReadySpec] = []

    def wait_ready(spec: docker.ContainerSpec, ready: docker.ReadySpec) -> bool:
        seen.append(ready)
        return True

    install(rec, tmp_path / "wow", wait_ready=wait_ready)
    assert ENTRY.install.native is not None
    markers = ENTRY.install.native.ready
    assert markers.regex is False
    tokens = {"REALM_HOST": native.INSTALL_REALM_HOST, "WORLD_PORT": str(ENTRY.ports.world)}
    assert seen[0].world == re.escape(composegen.fill(markers.world, tokens))
    assert markers.auth is not None
    filled_auth = composegen.fill(markers.auth, tokens)
    assert seen[0].auth == re.escape(filled_auth)
    assert re.search(seen[0].auth, filled_auth)
    assert not re.search(seen[0].auth, filled_auth.replace(".", "x"))
    assert seen[0].timeout == float(markers.timeout_s)
    assert seen[0].restart_loop == markers.restart_loop

    literal = _build(rec, AzerothCoreInstaller)._ready_spec(
        ReadyMarkers(world="World initialized|Ready", auth=None, regex=True)
    )
    assert literal.world == "World initialized|Ready"
    assert literal.auth is None


def test_the_catalogue_timeout_wins_over_the_docker_default() -> None:
    """Two contract-mandated numbers meet here: 600 from the data, 480 in `docker.py`.

    `ReadyMarkers.timeout_s` is 600 and `docker.ReadySpec`'s own default is
    480. A spec built from catalogue data uses the data (A.2 review finding),
    and the numbers must actually differ or this pins nothing.
    """
    spec = _build(Recorder(), AzerothCoreInstaller)._ready_spec(ReadyMarkers(world="ready..."))
    assert docker.ReadySpec(world="x").timeout == 480.0
    assert spec.timeout == 600.0


def test_a_broken_ready_pattern_is_refused_where_it_is_read_not_mid_poll() -> None:
    """A bad regex from `catalog.json` must not raise `re.error` 40 minutes into an install.

    `regex: true` hands the string to `re.search` as written, so an unbalanced
    group reaches the daemon poll and dies there — after the build, after the
    import, inside `wait_ready()`. Compiled here instead, and the sentence
    names the file to fix (A.2 review finding).
    """
    installer = _build(Recorder(), AzerothCoreInstaller)
    with pytest.raises(InstallerError, match="catalog.json"):
        installer._ready_spec(ReadyMarkers(world="World (initialized", regex=True))
    with pytest.raises(InstallerError, match="catalog.json"):
        installer._ready_spec(ReadyMarkers(world="ready...", fatal="*nope", regex=True))


def test_an_unfillable_ready_marker_is_a_sentence_not_a_traceback() -> None:
    """`fill()` raises `ComposeGenError` for a token nobody fills; the engine words it."""
    installer = _build(Recorder(), AzerothCoreInstaller)
    with pytest.raises(InstallerError, match="ready markers are broken"):
        installer._ready_spec(ReadyMarkers(world="{{NO_SUCH_TOKEN}}"))


def test_pumped_output_arrives_in_order_and_before_the_stage_ends(tmp_path: Path) -> None:
    """`_pump` streams a push-style docker call; nothing is collected into a list first."""
    rec = Recorder(images=False)
    lines = install(rec, tmp_path / "wow")
    assert lines.index("compiling") < lines.index("The build finished.")
    assert lines.index("--- build") < lines.index("compiling")


# -- SELinux ----------------------------------------------------------------
#
# Three facts meet in `generate-compose`: is SELinux enforcing, can this
# filesystem hold a label, and did the one-off relabel work. The engine asks
# the first two through seams and lets `platform.bind_label()` combine them, so
# the policy lives in one function and this file asserts the WIRING.

_MOUNT_MODES = frozenset(
    {
        "ro",
        "rw",
        "z",
        "Z",
        "shared",
        "rshared",
        "slave",
        "rslave",
        "private",
        "rprivate",
        "nocopy",
        "consistent",
        "cached",
        "delegated",
    }
)
"""Every mount option a compose short-syntax bind may already carry before the label."""


def _compose_text(server_dir: Path) -> str:
    """Both auto-loaded files, because `{{BIND_LABEL}}` is in the override too."""
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    override = (server_dir / composegen.OVERRIDE_FILE).read_text(encoding="utf-8")
    return f"{base}\n{override}"


def test_bind_mounts_are_labelled_only_when_selinux_enforces_on_a_labelable_fs(
    tmp_path: Path,
) -> None:
    """`:z` on every host bind line under enforcing SELinux; byte-identical files elsewhere.

    The two negatives carry as much weight as the positive. A `:z` written on
    Ubuntu would be harmless noise, but a `:z` written on the exFAT drive a
    user keeps their servers on makes the daemon refuse to create the
    container at all — which is why `fs_type` is asked separately from
    `selinux_enforcing` rather than assumed.
    """
    off = tmp_path / "off"
    rec_off = Recorder(images=False)
    install(rec_off, off)
    assert ":z" not in _compose_text(off)
    assert rec_off.relabelled == []

    on = tmp_path / "on"
    rec = Recorder(images=False)
    install(rec, on, selinux_enforcing=lambda: True, fs_type=lambda path: "ext4")
    assert ":z" in (on / composegen.BASE_FILE).read_text(encoding="utf-8")
    assert ":z" in (on / composegen.OVERRIDE_FILE).read_text(encoding="utf-8")
    assert rec.relabelled == [on]

    ntfs = tmp_path / "ntfs"
    rec_ntfs = Recorder(images=False)
    install(rec_ntfs, ntfs, selinux_enforcing=lambda: True, fs_type=lambda p: "ntfs")
    assert ":z" not in _compose_text(ntfs)
    # No label written, so nothing to relabel: `chcon` on a filesystem with no
    # xattrs fails anyway, and the warning line would be noise on a machine
    # whose install is fine.
    assert rec_ntfs.relabelled == []


def test_selinux_that_could_not_be_asked_arrives_as_none_and_not_as_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`selinux_enforcing()` has THREE answers and the middle one must survive the trip.

    `True`, `False`, `None` — and `None` means the question went unanswered,
    never "no". Both `None` and `False` end in an empty label today, so no
    rendered file can tell them apart; what this pins is that the engine hands
    `bind_label()` what the seam actually said, so a `bool(...)` or an
    `is True` at the call site is a failing test rather than a silent collapse
    that a later change to the policy would turn into a wrong install. The
    shell lineage collapsed exactly this pair and relabelled nothing on an
    enforcing Fedora while its own test passed.
    """
    asked: list[tuple[bool | None, str | None]] = []
    real = platform.bind_label

    def spy(*, enforcing: bool | None, fs_type: str | None) -> str:
        asked.append((enforcing, fs_type))
        return real(enforcing=enforcing, fs_type=fs_type)

    monkeypatch.setattr(native.platform, "bind_label", spy)
    rec = Recorder(images=False)
    install(rec, tmp_path / "wow", selinux_enforcing=lambda: None, fs_type=lambda p: "ext4")
    assert asked == [(None, "ext4")]
    assert ":z" not in _compose_text(tmp_path / "wow")
    assert rec.relabelled == []


def test_the_filesystem_is_probed_for_the_folder_the_binds_actually_live_in(
    tmp_path: Path,
) -> None:
    """`fs_type` is asked about the SERVER dir — the thing every `./` bind is relative to.

    A compose bind is resolved against the file's own directory, so the only
    filesystem whose label support matters is the one holding the server
    folder. Asking about anything else (the config dir, the home dir, cwd)
    answers a different machine's question on any box with more than one drive
    — which is the box this check exists for.
    """
    server_dir = tmp_path / "wow"
    probed: list[Path] = []

    def fs_type(path: Path) -> str | None:
        probed.append(path)
        return "btrfs"

    install(Recorder(images=False), server_dir, selinux_enforcing=lambda: True, fs_type=fs_type)
    assert probed == [server_dir]


def test_a_label_is_never_appended_to_a_bind_that_already_carries_a_mode(tmp_path: Path) -> None:
    """`:ro:z` is not a mount spec; the legal spelling is `:ro,z`, and Docker refuses the first.

    The label is spliced onto the end of a bind line as a bare suffix, so a
    host bind that already carries a mode renders `./x:/y:ro:z` and the daemon
    rejects the whole file at `up` — after the build, on the one platform that
    needs the label. No WotLK host bind carries a mode, which is exactly why
    nothing else catches it: the templates and the token convention agree by
    luck rather than by rule. The CMaNGOS games (7.3) DO mount the client
    read-only, so the first template that puts `{{BIND_LABEL}}` after a mode
    steps straight onto it. This is the tripwire that makes that a red test
    here rather than a Fedora bug report; closing it properly means teaching
    `composegen.render()` the comma form, which is group B's contract and not
    this task's file.
    """
    server_dir = tmp_path / "wow"
    install(
        Recorder(images=False), server_dir, selinux_enforcing=lambda: True, fs_type=lambda p: "ext4"
    )
    labelled = [
        line.strip()
        for line in _compose_text(server_dir).splitlines()
        if line.rstrip().endswith(":z")
    ]
    assert labelled, "the enforcing render wrote no labelled bind at all"
    for line in labelled:
        already = line.rstrip()[: -len(":z")].rsplit(":", 1)[-1]
        assert already not in _MOUNT_MODES, f"{line!r} renders an illegal `:mode:z`; use `:mode,z`"


def test_a_relabel_that_fails_is_a_warning_line_not_a_refusal(tmp_path: Path) -> None:
    """`chcon` is belt to the `:z` braces: it can fail and the install still has to finish.

    `:z` relabels the mount source when the container starts, which is the
    mechanism that actually carries a Fedora install; the one-off `chcon`
    covers the files that exist before compose ever runs. Failing the install
    on it would refuse a server that works, so the line is said and the stages
    keep going — `start` is in the calls, and the sentence names the command
    to run by hand.
    """
    rec = Recorder(images=False)
    lines = install(
        rec,
        tmp_path / "wow",
        selinux_enforcing=lambda: True,
        fs_type=lambda path: "ext4",
        relabel=lambda path: False,
    )
    assert any("could not be relabelled" in line for line in lines)
    assert any("chcon -Rt container_file_t" in line for line in lines)
    assert "start" in rec.calls
