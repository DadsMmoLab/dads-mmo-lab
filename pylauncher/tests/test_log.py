"""Tests for the shared logging convention (roadmap Phase 0.6).

Every test resets `yulon.log`'s module state via `_reset_for_tests()` in a
fixture, so tests never leak handlers onto the real root logger across the
suite (this file previously mutated global logging state with no teardown).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from yulon import log as log_module
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
