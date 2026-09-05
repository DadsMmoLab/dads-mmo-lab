"""`docker_group_reexec()` and the launcher's use of it (2026-09-02).

The problem these cover: a user added to the `docker` group cannot use Docker
from the session that was already open, because supplementary groups are process
credentials rather than a lookup. Measured on `yulon-ubuntu` by sampling one
process across a `usermod`: `os.getgroups()` did not contain the new group in
any of the eighteen samples after the join, while `id -nG <user>` contained it
one second after. `sg` builds a process from the database instead, so a re-exec
through it holds the group with no logout.

Every test here injects BOTH sides of that predicate, and they are set to
DIFFERENT values in the cases that matter. That is deliberate: the tempting
simplification is to read the process's groups twice, or the database twice, and
either one leaves a function that is a no-op or one that re-execs on every start
for the rest of the machine's life. A test that fed both sides the same answer
would pass against all three versions.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

import main
from yulon import platform

if TYPE_CHECKING:
    from collections.abc import Callable


def _id_saying(
    *groups: str, asked_about: str = "pk"
) -> Callable[[list[str]], subprocess.CompletedProcess[str]]:
    """A `RunCmd` standing in for `id -nG <user>` -- the DATABASE side.

    Asserts the argv it was handed rather than answering anything it is asked:
    `_docker_group_member()` is the only caller, `id -nG` is the only command it
    may run, and a stand-in that answers every question would keep passing if
    the production code started asking a different one.
    """

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        # The WHOLE argv, including the user. It used to assert `argv[:2]` --
        # the two words that never vary -- so replacing `_linux_user(None)` with
        # the literal "root" left the suite green while production asked whether
        # ROOT was in the docker group and re-exec'd on the answer. Asserting a
        # parameter's shape is not asserting that the value arrives
        # (review, 2026-09-02).
        assert argv == ["id", "-nG", asked_about], argv
        return subprocess.CompletedProcess(argv, 0, " ".join(groups) + "\n", "")

    return run


def _reexec(**over: Any) -> list[str] | None:
    """`docker_group_reexec()` with the whole predicate injected and no ambient state.

    The defaults describe the ONE machine this feature exists for: Linux, no
    marker, `sg` present, the process WITHOUT the docker group, the database
    WITH it. Each test overrides exactly the fact it is about, so a test that
    expects None names the single reason it expects one.
    """
    kwargs: dict[str, Any] = {
        "platform_id": lambda: "linux",
        "environ": {},
        "which": lambda name: f"/usr/bin/{name}" if name == "sg" else None,
        "getgroups": lambda: [1000],
        "run": _id_saying("pk", "docker"),
        "orig_argv": ["/usr/bin/python3", "-m", "main"],
    }
    kwargs.update(over)
    return platform.docker_group_reexec(**kwargs)


@pytest.fixture(autouse=True)
def _known_group_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """gid 1000 is `pk`, gid 999 is `docker`, and nothing else resolves.

    `grp` is patched rather than the helper that uses it, so the production path
    from a gid list to a name set is the one under test. Patched on every test
    including the ones that never look, because the alternative is a suite whose
    result depends on the groups of whoever runs it -- and this file is run on
    CI, on the maintainer's Windows box (where `grp` does not exist at all) and
    on the Linux test box.
    """
    names = {1000: "pk", 999: "docker"}

    class _Entry:
        def __init__(self, name: str) -> None:
            self.gr_name = name

    class _Grp:
        @staticmethod
        def getgrgid(gid: int) -> _Entry:
            if gid not in names:
                raise KeyError(gid)
            return _Entry(names[gid])

    real = platform.importlib.import_module

    def fake(name: str) -> Any:
        # ONLY `grp`. `_linux_user()` reaches the same `import_module` for
        # `pwd` on the very call under test, and a stand-in that answered
        # every name would hand it a module with no `getpwuid` -- an
        # `AttributeError` that `_linux_user()` does not catch, so the suite
        # would go red on Linux for a reason nothing here is about.
        return _Grp() if name == "grp" else real(name)

    monkeypatch.setattr(platform.importlib, "import_module", fake)

    # STATE the user, never measure it. `_linux_user(None)` returns whoever is
    # running the suite -- `pk` on the test VM, `runner` on GitHub Actions -- so
    # `_id_saying`'s assertion that the right user reaches `id -nG` passed here and
    # failed on CI for three commits before anyone looked (2026-09-02). Pinning it is
    # what makes that assertion mean "the value arrives" rather than "this box is pk".
    monkeypatch.setattr(platform, "_linux_user", lambda explicit: "pk")


def test_a_stale_session_whose_join_already_happened_is_restarted_under_sg() -> None:
    """The one case the feature exists for, and the only one that returns an argv.

    Process lacks the group, database has it -- exactly the state measured after
    `usermod` on a session opened before it. Asserts the whole argv, because
    `sg` takes its command as ONE string and a version that passed the argv as
    separate words would run `sg docker -c /usr/bin/python3` and silently drop
    every argument after the first.
    """
    assert _reexec() == ["/usr/bin/sg", "docker", "-c", "/usr/bin/python3 -m main"]


def test_a_process_that_already_has_the_group_is_left_alone() -> None:
    """The common case: nothing to regain, so no restart.

    The database is told the SAME thing as the process here, which is what makes
    this test discriminating: a version that decided by asking the database
    would find `docker` and restart -- on this start, and on every start after
    it, since the answer never changes. That is a launcher that never opens.
    """
    assert _reexec(getgroups=lambda: [1000, 999], run=_id_saying("pk", "docker")) is None


def test_no_restart_when_the_join_has_not_happened() -> None:
    """Neither side has the group, so a re-exec would gain nothing.

    `ensure_docker()` owns this user -- they have not been asked yet, or said
    no. Restarting them under `sg docker` would fail to grant anything and cost
    a visible relaunch to end up exactly where they started.
    """
    assert _reexec(run=_id_saying("pk", "users")) is None


def test_the_marker_stops_a_second_restart() -> None:
    """A machine where `sg` runs but does not deliver the group must not loop.

    Everything else here is set to the case that DOES restart, so the marker is
    the only thing being tested. Without it this is not a slow launcher, it is a
    process that replaces itself forever and never draws a window.
    """
    assert _reexec(environ={platform.REGROUP_ENV: "1"}) is None


def test_no_restart_without_sg() -> None:
    """A stripped image without `passwd`/`shadow-utils` gets the old advice, not a crash."""
    assert _reexec(which=lambda name: None) is None


@pytest.mark.parametrize("os_name", ["windows", "macos"])
def test_no_restart_off_linux(os_name: str) -> None:
    """Windows needs a REBOOT and macOS has no docker group; neither is fixable here.

    Parametrised over both rather than testing Linux's opposite once, because
    the two have different reasons and a guard written as `!= "windows"` would
    pass a single-case test while re-execing on macOS, where `sg` does not
    exist and the failure would be a launcher that will not start.
    """
    assert _reexec(platform_id=lambda: os_name) is None


def test_an_empty_argv_is_not_turned_into_an_sg_that_starts_nothing() -> None:
    """`sg docker -c ""` exits 0 having run nothing, which looks like a vanished app."""
    assert _reexec(orig_argv=[]) is None


def test_the_command_survives_a_path_with_a_space_in_it() -> None:
    """The single quoting site in this design, so it is asserted rather than assumed.

    A frozen build installs under `C:\\Program Files`-shaped paths on Windows and
    under `~/My Apps`-shaped ones often enough on Linux; joining with a bare
    `" ".join` would hand `sg` two words and start neither. Checks by SPLITTING
    the result back, so the test states the property (the shell will see these
    exact arguments) instead of restating whatever quoting style `shlex` picked.
    """
    argv = ["/opt/My Apps/yulon", "--server-dir", "/home/pk/wow server"]
    got = _reexec(orig_argv=argv)
    assert got is not None
    import shlex

    assert shlex.split(got[3]) == argv


def test_a_gid_with_no_group_row_is_skipped_rather_than_fatal() -> None:
    """A group deleted while a session was open leaves a gid that resolves to nothing.

    `getgrgid` raises `KeyError` for it. This runs on every start, so raising
    would be a launcher that refuses to open on a machine whose only fault is a
    tidied-up group. The unresolvable gid sits BESIDE the docker gid, so the
    test also shows the loop keeps going rather than stopping at the bad one.
    """
    assert _reexec(getgroups=lambda: [4242, 999]) is None


def test_a_host_claiming_linux_without_os_getgroups_refuses_rather_than_guessing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No gids means no way to tell "already has the group" from "does not".

    `os.getgroups` is POSIX-only. The direct call was `Module has no attribute
    "getgroups"` under `mypy --platform win32` -- a CI pass the Linux run, the
    suite and `ruff` were all green across, so this branch exists because CI
    caught what three local checks could not.

    Refusing is the safe direction, and it is asserted rather than assumed: the
    other reading is "no gids, so the group cannot be among them, so restart",
    which relaunches a user who had nothing to gain and, on a box where `sg`
    also cannot deliver, does it behind the marker exactly once and leaves them
    confused rather than looping.

    `getgroups=None` so the production path to `os` is the one taken; deleting
    the attribute is what a non-POSIX host looks like from inside this function.
    """
    monkeypatch.delattr(os, "getgroups", raising=False)

    assert _reexec(getgroups=None) is None


def test_the_launcher_sets_the_marker_before_it_execs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop guard is only real if it is set BEFORE control leaves the process.

    `os.execv` never returns, so a marker set after it is a marker never set at
    all -- and the test has to read the environment AT the call for the same
    reason. Recording it afterwards would pass against that broken order.
    """
    seen: dict[str, str | None] = {}

    def fake_execv(path: str, argv: list[str]) -> None:
        seen["marker"] = os.environ.get(platform.REGROUP_ENV)
        seen["path"] = path

    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(os, "execv", fake_execv)
    monkeypatch.delenv(platform.REGROUP_ENV, raising=False)

    main._regain_docker_group()

    assert seen["marker"] == "1"
    assert seen["path"] == "/usr/bin/sg"


def test_a_failed_exec_leaves_the_launcher_running_and_unmarked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sg` missing between the check and the call must not take the app down.

    Without the group the install refuses with a sentence that says what to do,
    which is where this user stood before any of this existed -- so the failure
    mode is the old behaviour, not a crash. The marker is cleared too: leaving
    it set would tell anything downstream that a re-exec happened.
    """
    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(os, "execv", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    monkeypatch.delenv(platform.REGROUP_ENV, raising=False)

    main._regain_docker_group()

    assert platform.REGROUP_ENV not in os.environ


def test_the_launcher_does_not_exec_when_there_is_nothing_to_regain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every normal start: `docker_group_reexec()` says None and nothing happens."""
    called: list[str] = []
    monkeypatch.setattr(platform, "docker_group_reexec", lambda: None)
    monkeypatch.setattr(os, "execv", lambda *a: called.append("execv"))
    monkeypatch.delenv(platform.REGROUP_ENV, raising=False)

    main._regain_docker_group()

    assert called == []
    assert platform.REGROUP_ENV not in os.environ


def test_restart_under_docker_group_does_nothing_when_there_is_nothing_to_regain() -> None:
    """The unit under both callers: None in means False out, and no exec attempted."""
    assert platform.restart_under_docker_group(reexec=lambda: None) is False


# --------------------------------------------------------------------------
# The other half of the problem: the user still sitting in the session the join
# happened in. `_regain_docker_group()` above cannot reach them -- their process
# is already running -- so the install gate has to ask.


def _view(tmp_path: Path) -> Any:
    """A `CatalogView` built only far enough to answer a finished job."""
    from yulon.catalog.catalog import load_catalog
    from yulon.ui.catalog_view import CatalogView
    from yulon.ui.widgets.log_panel import LogPanel

    view = CatalogView(load_catalog(), lambda e: None, LogPanel(), pick_dir=lambda *_: tmp_path)
    # What `start_install()` would have left behind. Set directly because these
    # tests are about what happens AFTER a job ends, and driving a whole install
    # to reach that point would make each one depend on every rule before it.
    view._current = ("wow-wotlk", tmp_path, None)
    return view


def test_a_failed_install_offers_a_restart_when_one_would_help(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The join has just happened, so the user is asked rather than told to log out.

    Asserts the plain "Install failed" warning is NOT also shown. Two dialogs
    for one failure is what the naive wiring produces, and the question already
    carries the message in full.
    """
    from PySide6.QtWidgets import QMessageBox

    asked: list[str] = []
    warned: list[str] = []
    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))

    _view(tmp_path)._on_run_finished(False, "Docker is installed and set up.")

    assert len(asked) == 1, asked
    assert "do NOT" in asked[0] and "log out" in asked[0], asked[0]
    assert warned == [], warned


def test_a_failure_a_restart_cannot_fix_still_shows_the_plain_warning(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other install failure is untouched by this -- the common case by far.

    Without this test the offer could be made unconditionally and the suite
    would stay green: a disk-space refusal would invite the user to restart the
    launcher, which fixes nothing and hides the sentence that would have.
    """
    from PySide6.QtWidgets import QMessageBox

    asked: list[str] = []
    warned: list[str] = []
    monkeypatch.setattr(platform, "docker_group_reexec", lambda: None)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))

    _view(tmp_path)._on_run_finished(False, "not enough disk space")

    assert asked == []
    assert warned == ["not enough disk space"]


def test_closing_the_restart_question_without_answering_does_not_restart(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Escape and the window's close button both return `NoButton`, not `No`.

    The same trap `attach_existing()` documents thirty lines of comment about.
    Spelled `is not ... No` this would RESTART on a dialog the user dismissed --
    throwing away the running application because they pressed Escape --
    since `NoButton is not No` is true. Only an explicit Yes may act.
    """
    from PySide6.QtWidgets import QMessageBox

    restarts: list[str] = []
    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(
        platform, "restart_under_docker_group", lambda *a, **k: restarts.append("did") or False
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.NoButton
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    _view(tmp_path)._on_run_finished(False, "Docker is installed and set up.")

    assert restarts == []


def test_saying_yes_restarts_under_the_docker_group(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yes reaches the same function the silent startup path uses, not a second copy."""
    from PySide6.QtWidgets import QMessageBox

    restarts: list[str] = []
    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(
        platform, "restart_under_docker_group", lambda *a, **k: restarts.append("did") or False
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    _view(tmp_path)._on_run_finished(False, "Docker is installed and set up.")

    assert restarts == ["did"]


# --------------------------------------------------------------------------
# The advice itself. Changing the mechanism without changing these sentences
# leaves an app that restarts itself while still telling the user to log out --
# and the dialog in `_offer_a_restart_instead()` shows the message it is
# offering to act on, so the two would contradict each other inside one box.
# The whole suite was green across that wording change, which is why these
# exist: 2034 tests could not see it.


@pytest.mark.parametrize(
    ("outcome", "manual_steps"),
    [
        pytest.param(
            "granted",
            (platform.DOCKER_GROUP_RELOGIN_STEP.format(user="pk"),),
            id="granted-as-ensure_docker-returns-it",
        ),
        pytest.param("granted", (), id="granted-with-no-manual-steps"),
        pytest.param("already-member", (), id="already-member"),
    ],
)
def test_the_restart_is_offered_before_the_logout(
    outcome: platform.DockerGroupOutcome, manual_steps: tuple[str, ...]
) -> None:
    """Both refusals name restarting FIRST and a logout only as the fallback.

    Order is the assertion, not presence. Both remedies are named on purpose --
    `docker_group_reexec()` refuses on a box with no `sg`, and that user still
    needs the old one -- so a test that only checked "restart is mentioned"
    would pass against a sentence that buries it after the logout, which is the
    sentence this replaced.

    The shape of the report is parametrised too, because for `granted` it used
    to be a report `ensure_docker()` cannot return: `manual_steps=()`, which
    sends `docker_unavailable()` down the `details or ...` fallback -- a
    sentence no user reads, since `_ensure_docker_linux()` appends
    `DOCKER_GROUP_RELOGIN_STEP` for that outcome unconditionally. The first case
    is the production shape and pins the ordering on the sentence that actually
    ships; the second keeps the fallback covered, because the fallback is kept
    on purpose (see the comment on that branch). `already-member` carries no
    steps in production either -- `platform.py` deliberately leaves the relogin
    step out of that outcome so the advice is not printed twice -- so its
    ordering rests on the branch's own inline sentence (verified against
    `platform.py`, 2026-09-02).
    """
    from yulon.catalog.installer import docker_unavailable

    report = platform.ProvisionReport(
        platform="linux", manual_steps=manual_steps, docker_group=outcome
    )
    message = str(docker_unavailable(report)).lower()

    assert "restart yu'lon" in message, message
    assert "log out and back in" in message, message
    assert message.index("restart yu'lon") < message.index("log out and back in"), message


def test_the_manual_step_for_a_granted_join_names_the_restart_first() -> None:
    """`DOCKER_GROUP_RELOGIN_STEP` is the line a headless `--provision` prints.

    It reaches a user with no dialog at all, so it carries the same order as
    the dialogs do.
    """
    step = platform.DOCKER_GROUP_RELOGIN_STEP.format(user="pk").lower()

    assert "restart yu'lon" in step, step
    assert step.index("restart yu'lon") < step.index("log out and back in"), step


# --------------------------------------------------------------------------
# The PRODUCTION defaults. `_reexec()` above injects every seam, which is what
# makes those tests readable -- and it left three defaults with no coverage at
# all. A mutation review on 2026-09-02 turned each into a no-op and the whole
# 2086-test suite stayed green:
#   * `which=None -> _which` made to return None: the feature is dead on every
#     real machine.
#   * `orig_argv=None -> sys.orig_argv` swapped for `sys.argv`: exactly the
#     regression `docker_group_reexec`'s docstring spends a paragraph arguing
#     against.
#   * the user handed to `id -nG` replaced by the literal "root".


def test_the_production_defaults_are_the_ones_the_docstring_promises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`which`, `orig_argv` and the user, left at their defaults and driven through.

    Patches the module-level names the defaults resolve to rather than passing
    substitutes, so the wiring under test is the wiring production uses. Asserts
    the whole argv: the interpreter and `-m` form come from `sys.orig_argv`, and
    the `sg` path from `_which`.
    """
    monkeypatch.setattr(platform, "_which", lambda name, path=None: f"/usr/bin/{name}")
    monkeypatch.setattr(sys, "orig_argv", ["/usr/bin/python3", "-m", "yulon.install_wiring"])
    monkeypatch.setattr(platform, "_linux_user", lambda explicit: "pk")

    got = platform.docker_group_reexec(
        platform_id=lambda: "linux",
        environ={},
        getgroups=lambda: [1000],
        run=_id_saying("pk", "docker"),
    )

    assert got == [
        "/usr/bin/sg",
        "docker",
        "-c",
        "/usr/bin/python3 -m yulon.install_wiring",
    ], got


def test_a_machine_with_no_sg_is_found_through_the_real_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `which=None` default, taken. A stub that always answers hides this."""
    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    monkeypatch.setattr(sys, "orig_argv", ["/usr/bin/python3", "-m", "x"])
    monkeypatch.setattr(platform, "_linux_user", lambda explicit: "pk")

    assert (
        platform.docker_group_reexec(
            platform_id=lambda: "linux",
            environ={},
            getgroups=lambda: [1000],
            run=_id_saying("pk", "docker"),
        )
        is None
    )


def test_root_never_restarts_because_it_has_nothing_to_regain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under `sudo` the two halves of the predicate are about DIFFERENT accounts.

    `os.getgroups()` describes this process (root); `_linux_user(None)` returns
    `$SUDO_USER`, so the database half describes the invoking user. Both answer
    yes, so the predicate was permanently true for a process that already
    reached the socket -- and `CatalogView._offer_a_restart_instead()` gates on
    exactly that, so EVERY install failure (disk full, missing client, a failed
    compile) was answered with "Docker is set up ... Restart Yu'lon now?" while
    the real message was suppressed.

    Measured on yulon-ubuntu 2026-09-02: `sudo python3 -c 'os.getgroups()'` is
    `[0]`, and `sudo sh -c 'id -nG $SUDO_USER'` contains `docker`.
    """
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)

    assert _reexec() is None


def test_the_launcher_actually_calls_it_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting the call from `main()` left the whole suite green.

    Every other test here calls `_regain_docker_group()` directly, so nothing
    proved the launcher ever reaches it -- the silent half of the fix could
    simply not run, and every user with a pending group join would be back to
    logging out. Asserts it happens BEFORE the `--provision` branch, which is
    where the headless path would otherwise leave without it.
    """
    calls: list[str] = []
    monkeypatch.setattr(main, "_regain_docker_group", lambda: calls.append("regain"))
    monkeypatch.setattr(main, "provision_headless", lambda: calls.append("provision") or 0)
    monkeypatch.setenv("YULON_PROVISION", "1")

    main.main()

    assert calls == ["regain", "provision"], calls


def test_a_successful_install_shows_no_dialog_at_all(
    qapp: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    the_compose_project_is_not_pinned: list[Path],
) -> None:
    """Dropping `not ok` from the call site left the suite green.

    `_on_run_finished(True, ...)` would then run through
    `_offer_a_restart_instead`, get False, and show `QMessageBox.warning(self,
    "Install failed", ...)` on a successful install. Nothing caught it because
    `conftest`'s autouse `_no_modal_dialogs` stubs `warning`/`information` to a
    silent Ok for every test that does not patch them itself -- so no test in
    the suite can observe an UNEXPECTED dialog. This one patches them itself
    and asserts silence (review, 2026-09-02).
    """
    from PySide6.QtWidgets import QMessageBox

    warned: list[str] = []
    asked: list[str] = []
    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]))

    view = _view(tmp_path)
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    view._on_run_finished(True, "done")

    assert warned == [], f"a successful install showed a warning: {warned}"
    assert asked == [], f"a successful install asked something: {asked}"


def test_the_restart_question_carries_what_actually_failed(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dialog suppresses the plain warning, so it must carry the message itself.

    That is the stated justification for returning True. Removing `{message}`
    from the dialog text left the suite green: the existing assertions look for
    "do NOT" and "log out", which both live in the STATIC half of the string,
    so the user could lose the only statement of what went wrong and every test
    still passed (review, 2026-09-02).
    """
    from PySide6.QtWidgets import QMessageBox

    asked: list[str] = []
    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)

    _view(tmp_path)._on_run_finished(False, "not enough disk space on /home")

    assert asked and "not enough disk space on /home" in asked[0], asked


def test_a_restart_that_could_not_happen_still_reports_the_failure(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Yes, then the exec fails: the user must not be left with a closed dialog.

    `restart_under_docker_group()` returns False when `os.execv` raised. The
    recovery `QMessageBox.warning` was unasserted -- deleting it left the suite
    green and the user staring at a dialog that closed and did nothing.
    """
    from PySide6.QtWidgets import QMessageBox

    warned: list[str] = []
    monkeypatch.setattr(
        platform, "docker_group_reexec", lambda: ["/usr/bin/sg", "docker", "-c", "x"]
    )
    monkeypatch.setattr(platform, "restart_under_docker_group", lambda *a, **k: False)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))

    _view(tmp_path)._on_run_finished(False, "Docker is installed and set up.")

    assert warned == ["Docker is installed and set up."], warned
