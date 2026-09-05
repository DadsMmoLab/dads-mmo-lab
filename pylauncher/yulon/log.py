"""Shared logging setup for Yu'lon.

Establishes the single logging convention used across the codebase (roadmap
Phase 0.6, style-guide §2: `logging`, not `print`). Every module gets a logger
via `get_logger(__name__)`.

The root logger always gets a stderr handler on first use. A rotating file
handler in the per-OS config dir (README §11) is added only once a real
`config_dir` is supplied to `configure()` — until then, logging is stderr-only.
Calling `configure(config_dir=...)` later (after an earlier stderr-only call)
still adds the file handler; it does not silently no-op.

Opening that file is allowed to fail. `configure()` is the first statement of
`main()`, before `QApplication` exists, so an `OSError` here — a managed
profile with a read-only `%APPDATA%`, a redirected or offline roaming share —
used to end the process at exit 1 with no window, and in the frozen build
(`console=False`, see `build/pylauncher.spec`) with no message at all. Not
having a log file is a degraded app; not starting is no app. So the file
handler falls back to the temp dir and then to nothing, and records why in
`file_log_problem()` for a caller that has a way to tell the user.

`configure(config_dir=...)` is called by EVERY entry point a user can run, and
by nothing else. There are two — `main.py` and `yulon/install_wiring.py` — and
for a while there was one: measured 2026-09-05 on `yulon-ubuntu`, after the
whole 7.2 gate had been driven through the CLI harness,
`~/.local/share/yulon/` did not exist, because the call appeared once in the
tree and that once was the GUI. The entry point with nowhere to print kept a
file; the entry point whose only other record is a terminal the user closes
kept nothing, and a gate criterion that asked for a count out of `yulon.log`
was scored against a file that had never been written. `test_install_wiring.py`
now enumerates the runnable modules and fails on the DISAGREEMENT, in either
direction, rather than on a missing call.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "yulon"
_DEFAULT_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_lock = threading.Lock()
_stderr_configured = False
_file_configured = False
_file_problem: str | None = None
_stderr_handler: logging.Handler | None = None
"""The stderr handler this module added, kept so `stderr_level` can find it again.

Not a convenience. Every module does `logger = get_logger(__name__)` at import,
which builds this handler long before any `main()` runs, so a level named by a
later `configure()` has to reach the handler that is ALREADY THERE — a level
applied only to a handler this call had just created would apply to nothing at
all in the one case that asks for one (`install_wiring.main()`).
"""


def file_log_problem() -> str | None:
    """One line about the log file, or `None` when it went where it was asked to.

    Read by `main.py` once the GUI is up: this is the only channel that reaches
    a user of the frozen build, whose stderr goes nowhere.
    """
    return _file_problem


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
    stderr_level: int | None = None,
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

    A `config_dir` that cannot be written never raises out of here; see the
    module docstring. `file_log_problem()` afterwards says whether that
    happened, and a failed attempt is not remembered as a done one, so a later
    call naming a writable directory still gets file logging.

    Args:
        config_dir: Directory to write the rotating `yulon.log` to. If omitted,
            only the stderr handler is (re-)ensured.
        level: Root logging level.
        stderr_level: Floor for the STDERR handler only, leaving the file at
            `level`. Opt-in, and omitted by `main.py`: the GUI's stderr is a
            developer's console and keeps every INFO line it always had. The CLI
            harness passes `WARNING` because its stage lines are already on
            stdout, and a gate runs it as `> log 2>&1` — logging them at INFO
            through this handler printed every line of a 30-minute install
            twice. Applied to the handler however old it is; see
            `_stderr_handler`.
        log_format/date_format: `logging` format strings.
        max_bytes: RotatingFileHandler max file size before rotation.
        backup_count: Number of rotated log files retained.
    """
    global _stderr_configured, _file_configured, _file_problem, _stderr_handler

    with _lock:
        root = logging.getLogger()
        root.setLevel(level)
        formatter = logging.Formatter(log_format, datefmt=date_format)

        if not _stderr_configured:
            _stderr_handler = logging.StreamHandler()
            _stderr_handler.setFormatter(formatter)
            root.addHandler(_stderr_handler)
            _stderr_configured = True
        if stderr_level is not None and _stderr_handler is not None:
            # Outside the `if` above on purpose: by the time a `main()` names a
            # level, the handler is the one that module-scope `get_logger()`
            # built at import time.
            _stderr_handler.setLevel(stderr_level)

        if config_dir is not None and not _file_configured:
            wanted = Path(config_dir)
            try:
                file_handler = _open_log(wanted, max_bytes, backup_count)
                _file_problem = None
            except OSError as exc:
                # The temp dir, and only the temp dir, is worth a second try:
                # the profiles that lose `%APPDATA%` lose it to redirection or
                # a read-only roaming share, neither of which touches the local
                # temp dir. A log the user can send us is most of what support
                # has, so it is worth one fallback — but not a search.
                fallback = Path(tempfile.gettempdir()) / APP_NAME
                _file_problem = f"Yu'lon could not write its log to {wanted}: {exc}."
                try:
                    file_handler = _open_log(fallback, max_bytes, backup_count)
                    _file_problem += f"\n\nIt is logging to {fallback / f'{APP_NAME}.log'} instead."
                except OSError as fallback_exc:
                    file_handler = None
                    _file_problem += (
                        f"\n\n{fallback} did not work either ({fallback_exc}), so this session "
                        "keeps no log file. Everything else works normally."
                    )
            if file_handler is not None:
                file_handler.setFormatter(formatter)
                root.addHandler(file_handler)
                # Only a handler that exists counts as done, so a later call
                # naming a writable directory is still able to open one.
                _file_configured = True
            if _file_problem is not None:
                # Logged as well as returned: this is one of the few lines worth
                # having in the fallback file itself, which is where support
                # will be reading from.
                logging.getLogger(__name__).warning(_file_problem.replace("\n", " "))


def _open_log(directory: Path, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    """Create `directory` and open `yulon.log` in it, or raise `OSError` trying.

    Both halves have to be in here: `mkdir()` is what fails on a read-only
    roaming share, and opening the file is what fails when the directory exists
    but is not writable, and the caller has to treat the two the same way.
    """
    directory.mkdir(parents=True, exist_ok=True)
    return RotatingFileHandler(
        directory / f"{APP_NAME}.log",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )


def _reset_for_tests() -> None:
    """Reset module state and remove handlers this module added.

    Test-only helper (avoids leaking global root-logger state across the test
    suite — see `tests/test_log.py`). Not part of the public API.
    """
    global _stderr_configured, _file_configured, _file_problem, _stderr_handler
    with _lock:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, (logging.StreamHandler, RotatingFileHandler)):
                root.removeHandler(handler)
                handler.close()
        _stderr_configured = False
        _file_configured = False
        _file_problem = None
        _stderr_handler = None


def use_utf8_streams() -> None:
    """Make stdout/stderr encode UTF-8, replacing what they cannot.

    Windows gives a redirected stream cp1252, and this app's own messages carry
    characters cp1252 has no place for -- `apply.py` alone writes `->` as a real
    arrow in six sentences. Writing one raises `UnicodeEncodeError` and takes the
    process down.

    Measured 2026-09-03 on `yulon-win11`: `python -m yulon.install_wiring
    wow-wotlk` reached the end of preflight and died with
    `'charmap' codec can't encode character '→' in position 210`. The GUI
    entry point had carried this fix since the provisioning work; the CLI harness
    every gate runs through had never had it, so no Windows gate could get past
    the first non-ASCII line.

    `errors="replace"` rather than letting it raise: a diagnostic that kills the
    thing it is diagnosing is worse than one with a "?" in it. A stream that
    cannot be re-wrapped is left alone.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):  # a stream that cannot be re-wrapped
                pass
