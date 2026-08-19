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

from yulon.log import _reset_for_tests, configure, get_logger


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
