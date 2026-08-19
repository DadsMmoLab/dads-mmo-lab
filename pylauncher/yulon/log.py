"""Shared logging setup for Yu'lon.

Establishes the single logging convention used across the codebase (roadmap
Phase 0.6, style-guide §2: `logging`, not `print`). Every module gets a logger
via `get_logger(__name__)`.

The root logger always gets a stderr handler on first use. A rotating file
handler in the per-OS config dir (README §11) is added only once a real
`config_dir` is supplied to `configure()` — until then, logging is stderr-only.
Calling `configure(config_dir=...)` later (after an earlier stderr-only call)
still adds the file handler; it does not silently no-op.

This module is imported once from `main.py` entry points; nothing else
configures logging.
"""

from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "yulon"
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_lock = threading.Lock()
_stderr_configured = False
_file_configured = False


def get_logger(name: str) -> logging.Logger:
    """Return a logger, ensuring the root logger has at least a stderr handler.

    Safe to call repeatedly and from multiple modules. It never adds a file
    handler on its own — call `configure(config_dir=...)` explicitly (e.g. from
    `main.py`, once `platform.config_dir()` is available) to enable file
    logging. Calling `configure()` again later with a `config_dir` still works
    even if `get_logger()` already triggered the stderr-only setup.
    """
    if not _stderr_configured:
        configure()
    return logging.getLogger(name)


def configure(
    config_dir: Path | None = None,
    *,
    level: int = logging.INFO,
    log_format: str = _DEFAULT_FORMAT,
    date_format: str = _DATE_FORMAT,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 3,
) -> None:
    """Configure the root logger. Safe to call more than once.

    The stderr handler is added at most once, ever. The file handler is added
    at most once, the first time `config_dir` is not `None` — so an initial
    `configure()` (stderr-only, e.g. via `get_logger()`) followed later by
    `configure(config_dir=...)` still enables file logging; it is not a no-op.

    Args:
        config_dir: Directory to write the rotating `yulon.log` to. If omitted,
            only the stderr handler is (re-)ensured.
        level: Root logging level.
        log_format/date_format: `logging` format strings.
        max_bytes: RotatingFileHandler max file size before rotation.
        backup_count: Number of rotated log files retained.
    """
    global _stderr_configured, _file_configured

    with _lock:
        root = logging.getLogger()
        root.setLevel(level)
        formatter = logging.Formatter(log_format, datefmt=date_format)

        if not _stderr_configured:
            stderr_handler = logging.StreamHandler()
            stderr_handler.setFormatter(formatter)
            root.addHandler(stderr_handler)
            _stderr_configured = True

        if config_dir is not None and not _file_configured:
            config_dir = Path(config_dir)
            config_dir.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                config_dir / f"{APP_NAME}.log",
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
            _file_configured = True


def _reset_for_tests() -> None:
    """Reset module state and remove handlers this module added.

    Test-only helper (avoids leaking global root-logger state across the test
    suite — see `tests/test_log.py`). Not part of the public API.
    """
    global _stderr_configured, _file_configured
    with _lock:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, (logging.StreamHandler, RotatingFileHandler)):
                root.removeHandler(handler)
                handler.close()
        _stderr_configured = False
        _file_configured = False
