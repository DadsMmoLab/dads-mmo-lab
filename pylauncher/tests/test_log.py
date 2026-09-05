"""Tests for the shared logging convention (roadmap Phase 0.6).

Every test resets `yulon.log`'s module state via `_reset_for_tests()` in a
fixture, so tests never leak handlers onto the real root logger across the
suite (this file previously mutated global logging state with no teardown).
"""

from __future__ import annotations

import ast
import io
import logging
import os
import subprocess
import sys
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from tests import conftest
from yulon import log as log_module
from yulon import platform
from yulon.log import _reset_for_tests, configure, file_log_problem, get_logger


def _a_directory_nothing_can_be_created_under(tmp_path: Path) -> Path:
    """A config dir whose parent is a plain FILE, so `mkdir()` really fails.

    The reported trigger is a read-only `%APPDATA%` on a managed profile, which
    an `icacls` deny reproduces on Windows and nothing reproduces on Linux. A
    file where a directory has to go raises an `OSError` from the same
    `mkdir()`/open on every OS and needs no privileges, so the test travels.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    blocker = tmp_path / "roaming"
    blocker.write_text("a file, so no directory can be made here", encoding="utf-8")
    return blocker / "yulon"


@pytest.fixture(autouse=True)
def _clean_logging_state() -> Iterator[None]:
    """Ensure each test starts and ends with a clean root logger."""
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_get_logger_returns_standard_logger() -> None:
    """`get_logger` returns a real stdlib logger named after the module."""
    logger = get_logger("yulon.tests")
    assert isinstance(logger, logging.Logger)
    assert logger.name == "yulon.tests"


def test_configure_adds_stderr_handler_once() -> None:
    """A single configure() adds exactly one stderr handler."""
    root = logging.getLogger()
    before = len(root.handlers)
    configure()
    assert len(root.handlers) == before + 1


def test_configure_is_idempotent_for_stderr_handler() -> None:
    """Calling configure() twice never adds a second stderr handler."""
    root = logging.getLogger()
    before = len(root.handlers)
    configure()
    configure()
    assert len(root.handlers) == before + 1


def test_configure_can_add_file_handler_after_earlier_stderr_only_call(
    tmp_path: Path,
) -> None:
    """A later configure(config_dir=...) still adds the file handler.

    Regression test: an earlier bug made this a silent no-op if `configure()`
    (or `get_logger()`) had already run once without a `config_dir`.
    """
    configure()  # stderr-only, as get_logger() would trigger implicitly
    root = logging.getLogger()
    assert not any(isinstance(h, RotatingFileHandler) for h in root.handlers)

    configure(config_dir=tmp_path)  # should still add the file handler
    assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)
    assert (tmp_path / "yulon.log").exists()


def test_configure_adds_file_handler_when_config_dir_given(tmp_path: Path) -> None:
    """A config_dir adds a RotatingFileHandler alongside the stderr handler."""
    root = logging.getLogger()
    before = len(root.handlers)
    configure(config_dir=tmp_path)
    assert len(root.handlers) == before + 2  # one stderr, one file
    assert any(isinstance(h, RotatingFileHandler) for h in root.handlers)


def test_a_logged_message_actually_reaches_the_log_file(tmp_path: Path) -> None:
    """Log content, not just handler objects, ends up in yulon.log."""
    configure(config_dir=tmp_path)
    logger = get_logger("yulon.tests.content")
    logger.info("hello from a test")

    for handler in logging.getLogger().handlers:
        handler.flush()

    log_file = tmp_path / "yulon.log"
    assert log_file.exists()
    assert "hello from a test" in log_file.read_text(encoding="utf-8")


# ----------------------------------------- a config dir that cannot be written


def test_a_config_dir_that_cannot_be_created_does_not_take_the_process_down(
    tmp_path: Path,
) -> None:
    """`configure()` is the FIRST statement of `main()`, before `QApplication` exists.

    A profile whose `%APPDATA%` cannot be written turned the `OSError` raised
    here into exit 1 with no window, and in the frozen build - which is
    `console=False`, see `build/pylauncher.spec` - with no message anywhere at
    all. Starting without a log file is a degraded app; not starting is no app.
    """
    configure(config_dir=_a_directory_nothing_can_be_created_under(tmp_path))

    assert logging.getLogger().handlers, "startup logging did not survive at all"


def test_a_log_that_cannot_go_to_the_config_dir_goes_to_the_temp_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A log the user can send us is most of what support has, so it is worth a fallback.

    `%TEMP%` is writable on the profiles where `%APPDATA%` is not - a redirected
    or read-only roaming share leaves the local temp dir alone - so it is the
    one destination worth trying before giving up on file logging.
    """
    elsewhere = tmp_path / "temp"
    elsewhere.mkdir()
    monkeypatch.setattr(log_module.tempfile, "gettempdir", lambda: str(elsewhere))

    configure(config_dir=_a_directory_nothing_can_be_created_under(tmp_path))
    get_logger("yulon.tests.fallback").info("logged anyway")
    for handler in logging.getLogger().handlers:
        handler.flush()

    fallback_log = elsewhere / "yulon" / "yulon.log"
    assert fallback_log.exists(), "the log went nowhere even though the temp dir was writable"
    assert "logged anyway" in fallback_log.read_text(encoding="utf-8")


def test_the_log_file_being_somewhere_else_is_reported_and_not_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resilience that says nothing is indistinguishable from the bug it replaced.

    The message names both directories, because the only reason a user reads it
    is to find the log; the caller is what puts it somewhere a `console=False`
    build can show.
    """
    elsewhere = tmp_path / "temp"
    elsewhere.mkdir()
    monkeypatch.setattr(log_module.tempfile, "gettempdir", lambda: str(elsewhere))
    wanted = _a_directory_nothing_can_be_created_under(tmp_path)

    configure(config_dir=wanted)

    problem = file_log_problem()
    assert problem is not None, "the log moved and nobody was told"
    assert str(wanted) in problem
    assert str(elsewhere / "yulon" / "yulon.log") in problem


def test_nowhere_writable_at_all_still_leaves_a_running_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback can fail too, and that is still not a reason to refuse to start."""
    blocked_temp = _a_directory_nothing_can_be_created_under(tmp_path / "temp")
    monkeypatch.setattr(log_module.tempfile, "gettempdir", lambda: str(blocked_temp))

    configure(config_dir=_a_directory_nothing_can_be_created_under(tmp_path / "appdata"))

    root = logging.getLogger()
    assert not any(isinstance(h, RotatingFileHandler) for h in root.handlers)
    assert any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    problem = file_log_problem()
    assert problem is not None and "no log file" in problem


def test_a_config_dir_that_works_reports_no_problem(tmp_path: Path) -> None:
    """The happy path must not learn to complain: this is what keeps the notice rare."""
    configure(config_dir=tmp_path)

    assert file_log_problem() is None


def test_a_stderr_level_keeps_info_off_the_terminal_and_still_in_the_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two readers, two levels: the file takes the run, the terminal takes what went wrong.

    Asked for by `yulon.install_wiring`, whose stage lines are already on
    stdout. A gate runs that harness as `> log 2>&1`, so logging those same
    lines at INFO through a stderr handler printed every line of a 30-minute
    install twice. Nothing is dropped -- the record they exist for is the file.

    The `configure()` call that names the level is the SECOND one here, and
    that is the case that matters: `install_wiring` does
    `logger = get_logger(__name__)` at module scope, so the stderr handler is
    already built by the time its `main()` runs, and a level that only reached
    a freshly created handler would reach nothing at all.
    """
    terminal = io.StringIO()
    monkeypatch.setattr(sys, "stderr", terminal)
    configure()  # what `get_logger()` at module scope does: stderr, no level
    configure(config_dir=tmp_path, stderr_level=logging.WARNING)

    logging.getLogger("yulon.tests").info("stage: preflight ok")
    logging.getLogger("yulon.tests").warning("that did not work")

    assert "stage: preflight ok" not in terminal.getvalue()
    assert "that did not work" in terminal.getvalue()
    written = (tmp_path / "yulon.log").read_text(encoding="utf-8")
    assert "stage: preflight ok" in written and "that did not work" in written


def test_without_a_stderr_level_info_still_reaches_the_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The GUI entry point passes no level and must keep the stderr it always had.

    The pair to the test above: `stderr_level` is opt-in, so adding it for the
    CLI cannot quietly silence `main.py`, whose developer-facing INFO lines on
    a console build are the only thing a `python main.py` shows.
    """
    terminal = io.StringIO()
    monkeypatch.setattr(sys, "stderr", terminal)
    configure(config_dir=tmp_path)

    logging.getLogger("yulon.tests").info("Yu'lon launcher starting")

    assert "Yu'lon launcher starting" in terminal.getvalue()


# --- The one log the suite may never open (review, 2026-09-05) ---------------


def _stat(path: Path) -> tuple[bool, int, int]:
    """`(exists, size, mtime_ns)` for `path`, in a shape that compares equal across a call."""
    if not path.exists():
        return (False, 0, 0)
    info = path.stat()
    return (True, info.st_size, info.st_mtime_ns)


def test_the_suite_cannot_write_the_users_own_log(tmp_path: Path) -> None:
    """The guard `conftest` puts under every test, driven against the real directory.

    RED for it, measured on m910q 2026-09-05 with `pytest -q tests/test_spine.py`
    at `d18fcc31`: `/home/pk/.local/share/yulon/yulon.log` grew 2,876,987 ->
    2,931,487 bytes, 54,500 of them fabricated installs, and the run left a
    `RotatingFileHandler` on that file attached to the root logger. The file a
    user sends to support is not a scratch pad.

    Four assertions, and each is here because dropping it makes a different
    mutation survive:

    * the guard is INSTALLED -- asserted before anything is opened, because if
      it were not, the very next line would create the directory this test
      exists to protect. `conftest` installs it at import rather than in a
      fixture, which is why this reads `log._open_log` directly instead of
      asking what some fixture did;
    * it REFUSES the user's own directory, and the file on disk is untouched
      across the refusal (byte size and mtime), which is what says the refusal
      happens before `mkdir`/`open` rather than after;
    * it LETS EVERYTHING ELSE THROUGH -- a guard that refused every directory
      would pass the assertion above and turn the other 2,500 tests into a
      suite with no file logging at all;
    * `platform.config_dir()`, as a test sees it, never answers the user's own
      directory. That is the redirect rather than the guard, and without it
      every `install_wiring.main()` in `test_spine.py` would be red instead of
      quiet.
    """
    assert (
        log_module._open_log is not conftest.UNGUARDED_OPEN_LOG
    ), "the guard is not installed, so this run can append to the user's own yulon.log"
    real_log = conftest.THE_USERS_OWN_CONFIG_DIR / "yulon.log"
    before = _stat(real_log)

    with pytest.raises(AssertionError, match="the user's own log"):
        configure(config_dir=conftest.THE_USERS_OWN_CONFIG_DIR)

    assert _stat(real_log) == before, f"{real_log} was touched by the attempt to refuse it"

    # `try`, not a bare call, and the pair is the point. A guard that refused
    # EVERY directory raises out of `configure()` -- which catches `OSError`
    # and nothing else -- so with a bare call the run dies on this line and the
    # sentence below, written for exactly that mutation, is never printed. The
    # mutation table of 2026-09-05 quoted it anyway (review).
    try:
        configure(config_dir=tmp_path / "scratch")
    except AssertionError as refused:
        pytest.fail(f"the guard refused a directory that is not the user's own: {refused}")
    assert (
        tmp_path / "scratch" / "yulon.log"
    ).is_file(), "the guard let a scratch directory through but no log was opened in it"
    assert not conftest.is_the_users_own_config_dir(platform.config_dir()), (
        f"a test's platform.config_dir() answered {platform.config_dir()}, which is the "
        "real one -- the redirect is gone and only the guard is left between the suite "
        "and the user's log"
    )


def test_a_pinned_stderr_level_and_a_leaked_file_handler_are_both_put_back(
    tmp_path: Path,
) -> None:
    """`conftest.restore_root_logging()`, driven on a root logger crafted to hold both leaks.

    RED, measured on m910q 2026-09-05 by a probe test appended after
    `tests/test_spine.py` and run in the same process:

        stderr handler pinned at WARNING
        leaked file handlers: ['/home/pk/.local/share/yulon/yulon.log']

    Both come from ONE call: `install_wiring.main()` does
    `configure(config_dir=..., stderr_level=WARNING)`, and `configure()` is
    documented to apply that level to the handler that already exists, forever.
    Of the two test files that drive that `main()`, only this lane's own
    accounted for it (review, 2026-09-05); `test_spine.py` drives it at four
    sites and knows nothing about logging, which is exactly why the undo
    belongs in `conftest` and not in either file.

    Driven as a function rather than through the fixture because a fixture
    cannot assert on its own teardown -- and asserting the THIRD handler is
    still there is what stops the obvious over-fix: a teardown that simply
    removed everything it had not seen before would take `caplog`'s
    per-phase `LogCaptureHandler` with it.
    """
    root = logging.getLogger()
    stderr_handler = logging.StreamHandler()
    root.addHandler(stderr_handler)
    try:
        levels = conftest.root_handler_levels()
        assert stderr_handler in levels

        stderr_handler.setLevel(logging.WARNING)  # what configure(stderr_level=) does
        leaked = RotatingFileHandler(tmp_path / "yulon.log", encoding="utf-8")
        root.addHandler(leaked)
        stranger = logging.StreamHandler()  # stands in for caplog's own handler
        root.addHandler(stranger)
        log_module._file_configured = True
        log_module._file_problem = "this test's own unwritable directory"

        conftest.restore_root_logging(levels)

        assert stderr_handler.level == logging.NOTSET, "the pinned level outlived the test"
        assert leaked not in root.handlers, "the file handler outlived the test"
        assert log_module._file_configured is False, (
            "the handler is gone but the module still calls file logging done, so the next "
            "test to ask for a log file gets none -- measured as two failures on gw3 under "
            "-n auto --dist loadfile, green serially, m910q 2026-09-05"
        )
        assert (
            log_module._file_problem is None
        ), "one test's diagnosis is still the answer `file_log_problem()` gives the next"
        assert (
            stranger in root.handlers
        ), "a handler the snapshot never saw was removed anyway -- that is caplog's"
    finally:
        for handler in (stderr_handler, stranger):
            root.removeHandler(handler)


_WHAT_THE_CHILD_LOGS = "a child of this suite wrote this line"

_A_CHILD_THAT_KEEPS_A_LOG = f"""\
from yulon import log, platform
log.configure(config_dir=platform.config_dir())
log.get_logger("yulon.tests.child").info({_WHAT_THE_CHILD_LOGS!r})
print(platform.config_dir())
"""
"""A child that does what every entry point does first, and says where it went.

It writes a RECORD as well as opening the file, because "a file was created"
and "a run was appended to it" are different damages and only the second is
what a support log loses. Measured on m910q 2026-09-05, with the guard removed
and the whole suite run under a stand-in home: the file appeared with 0 bytes
in it before this child logged a line, and 83 with it.
"""


def _a_child_run_from_the_app_root(env: dict[str, str] | None) -> subprocess.CompletedProcess[str]:
    """Run `_A_CHILD_THAT_KEEPS_A_LOG`, with `env` handed to `subprocess.run` verbatim."""
    return subprocess.run(
        [sys.executable, "-c", _A_CHILD_THAT_KEEPS_A_LOG],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_child_process_cannot_write_the_users_own_log() -> None:
    """The rule of `conftest` extended to the door it could not see: a child process.

    RED, measured on m910q 2026-09-05: the whole suite run under a stand-in
    home (`HOME=/tmp/red-home`, `XDG_DATA_HOME` unset, so "the user's own log"
    was that home's) with `conftest`'s `Popen` guard removed left

        /tmp/red-home/.local/share/yulon/yulon.log   83 bytes
        2026-09-05 10:16:10 INFO [yulon.tests.child] a child of this suite ...

    at `2 failed, 2552 passed`; the same command with the guard in place left
    that path non-existent, at `2554 passed`. The in-process redirect and
    the `_open_log` guard were both already there and neither can see a child:
    it has its own interpreter, its own `platform.config_dir`, and it inherits
    the real `HOME` of the process that started it.

    Both spellings are driven, because they take different paths through
    `child_env_with_the_users_own_log_out_of_reach` -- `env=None` is the
    inherit path (`os.environ` materialised there), `env=dict(os.environ)` is
    the copy path (compared against `os.environ` key by key) -- and the suite
    contains both.

    The second assertion is what stops the cheap pass: a child that died on
    import, or one that opened a file and recorded nothing, would satisfy "did
    not write the user's log" perfectly.

    What is NOT asserted here is the real log's size across the call, the way
    the in-process guard's test asserts it: on a shared box that file is not
    this test's to predict. Measured on m910q 2026-09-05 while this suite was
    not running at all, `~/.local/share/yulon/yulon.log` grew 3,357,108 ->
    3,360,860 in three minutes, from another lane's checkout running its own
    copy of the suite without this fix. An assertion on it would go red for
    something no reader of this file could act on. The claim it stands for --
    that a full run leaves the file alone -- is measured instead by running
    the suite under a stand-in home, which is the RED above.
    """
    for how, env in (
        ("inheriting this process's environment", None),
        ("handed a copy of it", dict(os.environ)),
    ):
        proc = _a_child_run_from_the_app_root(env)
        assert proc.returncode == 0, f"the child {how} died: {proc.stderr}"
        answered = proc.stdout.strip()
        assert not conftest.is_the_users_own_config_dir(answered), (
            f"a child {how} resolved config_dir() to {answered}, which is the user's own -- "
            "nothing stands between this suite's child processes and the log a user sends "
            "to support"
        )
        written = Path(answered) / "yulon.log"
        assert written.is_file() and _WHAT_THE_CHILD_LOGS in written.read_text(encoding="utf-8"), (
            f"the child {how} answered {answered} and no run of it was recorded there, so "
            "'it did not write the user's own' says nothing at all"
        )


_A_CHILD_THAT_IS_ITSELF_A_TEST_RUN = """\
from tests import conftest
print(conftest.THE_USERS_OWN_CONFIG_DIR)
"""
"""A child that asks this suite's own question: which directory is the user's own?

Every `xdist` worker is one of these -- a pytest started by a pytest, importing
this `conftest` after the guard has already handed it a scratch
`XDG_DATA_HOME`.
"""


def test_a_pytest_this_suite_starts_still_knows_which_log_is_the_users_own() -> None:
    """The redirect must not become the answer. A nested run inherits the truth, not the scratch.

    The self-defeating shape this closes, and it is silent in the direction
    that reads as a pass: the child guard points a child at a scratch
    directory, and a child that is ITSELF a pytest then computes
    `THE_USERS_OWN_CONFIG_DIR` from that scratch. Every assertion in `conftest`
    and in this file goes on passing -- about the scratch -- while nothing at
    all stands between that worker and the user's log. `-n auto --dist
    loadfile` is the suite's normal spelling on the boxes that have `xdist`,
    so this is not a hypothetical worker.

    Driven as a real child rather than by calling
    `conftest._the_users_own_config_dir({...})` with a dict, because the claim
    is about what SURVIVES a process boundary: the hand-down has to be written
    into the child's environment by one half and read back by the other, and a
    dict tests neither.
    """
    proc = subprocess.run(
        [sys.executable, "-c", _A_CHILD_THAT_IS_ITSELF_A_TEST_RUN],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(conftest.THE_USERS_OWN_CONFIG_DIR), (
        f"a pytest started by this suite resolved the user's own config dir to "
        f"{proc.stdout.strip()!r}, not {str(conftest.THE_USERS_OWN_CONFIG_DIR)!r}: it would "
        "guard the scratch directory it was handed and leave the real log open"
    )


def test_a_child_the_test_pointed_somewhere_of_its_own_is_left_alone(tmp_path: Path) -> None:
    """The "only then" clause, for children. A caller's own directory reaches the child.

    Same shape as the in-process redirect, and load-bearing for two tests that
    exist today: `test_main.py`'s launcher child is pointed at an `APPDATA`
    that CANNOT be written, to drive the temp-dir fallback, and
    `test_install_wiring.py` gives each entry point a `XDG_DATA_HOME` of its
    own so it can tell which of them wrote a log. A guard that overrode the
    caller would quietly turn both into tests of the scratch directory.
    """
    mine = tmp_path / "mine"
    env = dict(os.environ)
    env.update({var: str(mine) for var in conftest.VARS_THAT_DECIDE_A_CHILDS_CONFIG_DIR})

    proc = _a_child_run_from_the_app_root(env)

    assert proc.returncode == 0, proc.stderr
    answered = Path(proc.stdout.splitlines()[0])
    assert (
        mine in answered.parents
    ), f"a child pointed at {mine} by the test that started it answered {answered} instead"
    written = (answered / "yulon.log").read_text(encoding="utf-8")
    assert (
        _WHAT_THE_CHILD_LOGS in written
    ), "the child answered its own directory and logged nothing"


def test_a_worker_started_by_this_suite_gets_a_config_dir_of_its_own_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The redirect covers the AMBIENT directory as well as the user's own.

    The second one exists only because of the child guard: an `xdist` worker
    is a child, so it inherits the scratch `XDG_DATA_HOME` that guard hands
    out, and `config_dir()` in that worker answers a directory shared by every
    worker on the box and every test inside it -- not the user's, and equally
    not something to hand a test that is about to assert its config dir is
    empty.

    RED, measured on m910q 2026-09-05: `--checks` (which runs `-n auto --dist
    loadfile` there) with only the user's-own clause gave
    `1 failed, 2552 passed`, the failure being
    `test_usage_and_a_bad_flag_leave_no_config_dir_behind` on
    `assert not PosixPath('/tmp/yulon-test-child-home-p1qhr7ic/yulon').exists()`.
    The same commit was green serially, which is what makes this worth a test:
    the defect is invisible in the spelling CI uses.

    Driven through `platform.config_dir()` -- what a test in that worker
    actually calls, and which is the fixture's redirect by the time any test
    runs -- and NOT through `a_directory_no_test_chose` on its own. The first
    version of this test asked the helper directly and a mutation that put the
    old one-clause condition back at the call site left it at `73 passed`
    (m910q 2026-09-05).

    An environment is arranged that no run of this suite produces on the boxes
    it is gated on: `THE_AMBIENT_CONFIG_DIR` is a real inherited answer that is
    NOT the user's own, which is what an `xdist` worker has and a serial run
    never does. The control is the line before the patch -- without it, an
    assertion that the redirect answered something else would hold for a
    directory the environment had never pointed at in the first place.
    """
    for var in conftest.VARS_THAT_DECIDE_A_CHILDS_CONFIG_DIR:
        monkeypatch.setenv(var, str(tmp_path / "inherited"))
    ambient = conftest.UNREDIRECTED_CONFIG_DIR()
    assert not conftest.is_the_users_own_config_dir(
        ambient
    ), f"the premise: {ambient} is inherited, not the user's own"
    assert (
        platform.config_dir() == ambient
    ), "the control: with nothing inherited, this test is not reaching the redirect at all"

    monkeypatch.setattr(conftest, "THE_AMBIENT_CONFIG_DIR", ambient)

    assert platform.config_dir() != ambient, (
        f"a test in an xdist worker is handed {ambient}, the directory that worker inherited "
        "-- shared with every other worker on the box and with every test in this one, and "
        "already holding whatever an earlier child wrote there"
    )


def test_a_child_pointed_at_the_users_own_directory_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Named as the user's own, a variable is REFUSED rather than rewritten.

    The child half of `_guarded_open_log`, and the same argument: a test that
    names that directory has stated an intent the suite may not carry out, and
    rewriting it under the test would hide the mistake instead of reporting it.
    The refusal happens in `Popen.__init__`, so no child is started at all.

    `delenv` first so the premise holds on any box: "named by the caller" is
    "differs from what this process would have handed down", and on a machine
    whose own `XDG_DATA_HOME` already said this, the value would be an
    inherited one rather than a named one.
    """
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    with pytest.raises(AssertionError, match="the user's own yulon.log"):
        subprocess.run(
            [sys.executable, "-c", "raise SystemExit('this child must never start')"],
            env={"XDG_DATA_HOME": str(conftest.THE_USERS_OWN_CONFIG_DIR.parent)},
            capture_output=True,
            check=False,
        )


def test_the_variables_the_child_guard_rewrites_really_move_config_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`VARS_THAT_DECIDE_A_CHILDS_CONFIG_DIR` is checked against the real function.

    The list is the one piece of `config_dir()`'s per-OS branching that
    `conftest` restates, and a restatement drifts. This asks the shipped
    function, on whatever box is running, whether the rewrite actually moves
    its answer -- so a platform whose variable is missing from the list fails
    here rather than by writing a log on someone's Mac.

    `conftest.UNREDIRECTED_CONFIG_DIR` and not `platform.config_dir`: the
    autouse fixture has replaced the latter with the redirect, which answers a
    scratch path whatever the environment says and would make this pass on an
    empty list.
    """
    scratch = tmp_path / "elsewhere"
    with monkeypatch.context() as env:
        for var in conftest.VARS_THAT_DECIDE_A_CHILDS_CONFIG_DIR:
            env.setenv(var, str(scratch))
        answered = conftest.UNREDIRECTED_CONFIG_DIR()

    assert answered == scratch or scratch in answered.parents, (
        f"config_dir() answered {answered} with every variable this guard rewrites pointed at "
        f"{scratch}: this platform reads one that "
        f"{conftest.VARS_THAT_DECIDE_A_CHILDS_CONFIG_DIR} does not name, so a child of this "
        "suite can still reach the user's own log"
    )


_CHILDREN_STARTED_ANY_OTHER_WAY = (
    "os.system",
    "os.popen",
    "os.fork",
    "os.forkpty",
    "os.posix_spawn",
    "os.posix_spawnp",
    "os.execv",
    "os.execve",
    "os.execvp",
    "os.execvpe",
    "os.spawnl",
    "os.spawnv",
    "os.spawnvp",
    "pty.fork",
    "pty.spawn",
    "multiprocessing.Process",
)
"""Every way to start a child that does NOT go through `subprocess.Popen`."""


def test_the_suite_starts_child_processes_only_through_the_guarded_seam() -> None:
    """Every child in `tests/` goes through `Popen`, which is the only door guarded.

    `child_env_with_the_users_own_log_out_of_reach` is installed on
    `subprocess.Popen.__init__`, which `run`, `call`, `check_call` and
    `check_output` all funnel into. Nothing else does: `os.system` and
    `os.posix_spawn` hand the child this process's environment directly, and a
    test that used one would be back where 2026-09-05 started, with no failure
    anywhere to say so.

    What this cannot see, stated so the price is known: a call spelled without
    its module (`from os import system`), and `yulon/platform.py`'s own
    `os.execv` in `restart_under_docker_group()` -- production code, out of
    scope here, and monkeypatched in every test that reaches it because a real
    `execv` would replace the pytest process rather than start a child.
    """
    started: list[str] = []
    for path in sorted(Path(__file__).resolve().parent.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and ast.unparse(node.func) in (
                _CHILDREN_STARTED_ANY_OTHER_WAY
            ):
                started.append(f"{path.name}:{node.lineno}: {ast.unparse(node.func)}")

    assert not started, (
        "these start a child process without going through subprocess.Popen, so the guard on "
        f"the user's own log cannot see them: {started}"
    )


def test_a_file_handler_that_was_already_there_keeps_file_logging_marked_done(
    tmp_path: Path,
) -> None:
    """The other branch of `restore_root_logging`'s recompute, driven.

    The teardown REMOVES the file handlers a test leaked and then asks the
    handlers that REMAIN whether file logging is still on, rather than writing
    `False` and trusting the two to agree. Until this test the True side of
    that question had nothing behind it (review, 2026-09-05).

    Nothing in the suite reaches this branch on its own today, which is exactly
    why it is driven here: a handler that is in the snapshot is one that
    existed BEFORE the test, so the test did not leak it, and removing it would
    take file logging away from whoever did open it while leaving the module
    saying it was still on.
    """
    root = logging.getLogger()
    survivor = RotatingFileHandler(tmp_path / "yulon.log", encoding="utf-8")
    root.addHandler(survivor)
    try:
        levels = conftest.root_handler_levels()
        assert survivor in levels, "the premise: this handler was there before the test"
        log_module._file_configured = True

        conftest.restore_root_logging(levels)

        assert survivor in root.handlers, (
            "a file handler the test did not open was removed anyway -- the next test to log "
            "gets no file, and whoever opened this one loses it"
        )
        assert log_module._file_configured is True, (
            "the handler is still on the root logger but the module says file logging is off, "
            "so the next configure(config_dir=...) opens a SECOND one"
        )
    finally:
        root.removeHandler(survivor)
        survivor.close()
        log_module._file_configured = False
