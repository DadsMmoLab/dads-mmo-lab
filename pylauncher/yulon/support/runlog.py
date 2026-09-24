"""A job's lines, kept on disk after the window closes (T93).

The on-screen `LogPanel` keeps 5000 lines and forgets them when the app
closes, and a failed install is the commonest thing anybody asks us about.
So a panel told `record_as=` also writes each line here, RAW: redaction
happens on the way out (the Logs tab, the support zip), and the file stays
the true record. One file per run, `logs/runs/<kind>-<UTC stamp>.log`,
flushed per line so a crash or an app exit mid-job loses nothing.

Two rules, both borrowed from `logsnap`:

* **It never fails the job.** Opening and writing swallow `OSError`; the first
  failure is one warning and turns the sink off.
* **It never prunes the file it just opened.** Retention keeps the newest
  `KEEP` per kind by modification time, and excludes the new file by identity,
  because a clock that stepped backwards would otherwise make it the oldest
  (`logsnap._prune`'s reasoning).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from yulon import platform
from yulon.log import get_logger

logger = get_logger(__name__)

KEEP = 10
"""Runs kept per kind -- per install for a rebuild, per game for an install."""

RUNS_DIR_NAME = "runs"

_STAMP = "%Y%m%dT%H%M%SZ"
_SAME_SECOND_LIMIT = 50


class Sink(Protocol):
    """What a run log writes into: an open text file, or a test's stand-in."""

    def write(self, text: str, /) -> int: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


Opener = Callable[[Path], Sink]


def logs_dir(config_dir: Path | None = None) -> Path:
    """`<config>/logs`: where `logsnap` keeps worldserver snapshots."""
    return (config_dir if config_dir is not None else platform.config_dir()) / "logs"


def runs_dir(config_dir: Path | None = None) -> Path:
    """`<config>/logs/runs`: where run logs live, apart from the snapshots."""
    return logs_dir(config_dir) / RUNS_DIR_NAME


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_opener(path: Path) -> Sink:
    """Create the file, refusing one that exists (`x`), with the line endings we write."""
    return path.open("x", encoding="utf-8", newline="")


def _safe_kind(kind: str) -> str:
    """Lower-case letters, digits and dashes: a kind becomes part of a file name."""
    return re.sub(r"[^a-z0-9-]+", "-", kind.lower()).strip("-") or "run"


class RunLog:
    """One run's lines on disk. Every method is safe to call on an inactive log."""

    def __init__(self, path: Path | None, sink: Sink | None) -> None:
        self.path = path
        self._sink = sink

    @property
    def active(self) -> bool:
        """True while lines are still being written."""
        return self._sink is not None

    @classmethod
    def open(
        cls,
        directory: Path,
        kind: str,
        *,
        now: Callable[[], datetime] = _utc_now,
        opener: Opener = _default_opener,
    ) -> RunLog:
        """Start a new run log of `kind` in `directory`; an inactive one if that fails."""
        kind = _safe_kind(kind)
        stamp = now().strftime(_STAMP)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path, sink = _open_fresh(directory, kind, stamp, opener)
        except OSError as exc:
            logger.warning(f"could not keep this job's output under {directory}: {exc}")
            return cls(None, None)
        _prune(directory, kind, keep=path)
        return cls(path, sink)

    def write(self, line: str) -> None:
        """Append one line and flush it. A failure turns this log off, once."""
        if self._sink is None:
            return
        try:
            self._sink.write(line.rstrip("\n") + "\n")
            self._sink.flush()
        except (OSError, ValueError) as exc:
            logger.warning(f"stopped keeping this job's output in {self.path}: {exc}")
            self.close()

    def close(self) -> None:
        """Close the file. Idempotent."""
        sink, self._sink = self._sink, None
        if sink is None:
            return
        try:
            sink.close()
        except OSError as exc:
            logger.debug(f"closing {self.path}: {exc}")


def _open_fresh(directory: Path, kind: str, stamp: str, opener: Opener) -> tuple[Path, Sink]:
    """`<kind>-<stamp>.log`, or `-2`, `-3`... when this second already has one."""
    for n in range(1, _SAME_SECOND_LIMIT + 1):
        suffix = "" if n == 1 else f"-{n}"
        path = directory / f"{kind}-{stamp}{suffix}.log"
        try:
            return path, opener(path)
        except FileExistsError:
            continue
    raise OSError(f"{_SAME_SECOND_LIMIT} runs of {kind} already started in {stamp}")


def _prune(directory: Path, kind: str, *, keep: Path) -> None:
    """Keep the newest `KEEP` of this kind, `keep` always among them."""
    own = re.compile(rf"{re.escape(kind)}-\d{{8}}T\d{{6}}Z(?:-\d+)?\.log")
    try:
        others = [p for p in directory.iterdir() if p != keep and own.fullmatch(p.name)]
        others.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in others[KEEP - 1 :]:
            stale.unlink()
    except OSError as exc:
        logger.warning(f"could not prune old run logs in {directory}: {exc}")
