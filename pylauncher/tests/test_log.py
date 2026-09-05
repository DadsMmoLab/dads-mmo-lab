"""Tests for the shared logging convention (roadmap Phase 0.6).

Every test resets `yulon.log`'s module state via `_reset_for_tests()` in a
fixture, so tests never leak handlers onto the real root logger across the
suite (this file previously mutated global logging state with no teardown).
"""

from __future__ import annotations

import io
import logging
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

    configure(config_dir=tmp_path / "scratch")
    assert (
        tmp_path / "scratch" / "yulon.log"
    ).is_file(), "the guard refused a directory that is not the user's own"
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
