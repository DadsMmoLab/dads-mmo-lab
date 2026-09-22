"""RunLog (T93): a job's lines on disk, ten per kind, and a failure that never fails the job."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from yulon import platform
from yulon.support import runlog
from yulon.support.runlog import KEEP, RunLog


def _at(second: int) -> datetime:
    return datetime(2026, 9, 22, 10, 0, second, tzinfo=UTC)


def test_a_run_log_is_named_for_its_kind_and_stamp_and_keeps_each_line(tmp_path: Path) -> None:
    record = RunLog.open(tmp_path, "install-wow-tbc", now=lambda: _at(5))
    record.write("one")
    record.write("two\n")
    record.close()
    assert record.path == tmp_path / "install-wow-tbc-20260922T100005Z.log"
    assert record.path.read_text(encoding="utf-8") == "one\ntwo\n"


def test_two_runs_in_one_second_get_two_files(tmp_path: Path) -> None:
    first = RunLog.open(tmp_path, "install-wow-tbc", now=lambda: _at(5))
    second = RunLog.open(tmp_path, "install-wow-tbc", now=lambda: _at(5))
    assert first.path != second.path
    assert second.path is not None and second.path.name == "install-wow-tbc-20260922T100005Z-2.log"


def test_retention_keeps_ten_per_kind_and_leaves_other_kinds_alone(tmp_path: Path) -> None:
    for n in range(12):
        old = tmp_path / f"install-wow-tbc-20260901T0000{n:02d}Z.log"
        old.write_text("old\n", encoding="utf-8")
        os.utime(old, (1_000_000 + n, 1_000_000 + n))
    other = tmp_path / "install-wow-tbc-extra-20260901T000000Z.log"
    other.write_text("a different kind\n", encoding="utf-8")
    record = RunLog.open(tmp_path, "install-wow-tbc", now=lambda: _at(5))
    mine = sorted(p.name for p in tmp_path.glob("install-wow-tbc-2026*.log"))
    assert len(mine) == KEEP
    assert record.path is not None and record.path.name in mine
    assert "install-wow-tbc-20260901T000011Z.log" in mine, "the newest old one was pruned"
    assert "install-wow-tbc-20260901T000000Z.log" not in mine, "the oldest one survived"
    assert other.exists(), "a different kind was pruned"


def test_the_file_just_opened_is_never_pruned_even_when_the_clock_went_backwards(
    tmp_path: Path,
) -> None:
    future = 4_000_000_000
    for n in range(KEEP + 3):
        newer = tmp_path / f"rebuild-wow-tbc-0badc0de-20300101T0000{n:02d}Z.log"
        newer.write_text("x\n", encoding="utf-8")
        os.utime(newer, (future + n, future + n))
    record = RunLog.open(tmp_path, "rebuild-wow-tbc-0badc0de", now=lambda: _at(5))
    assert record.path is not None and record.path.exists()
    assert len(list(tmp_path.glob("rebuild-wow-tbc-0badc0de-*.log"))) == KEEP


class _Full:
    """A sink on a full disk."""

    def __init__(self) -> None:
        self.closed = False

    def write(self, text: str, /) -> int:
        raise OSError(28, "No space left on device")

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


def test_a_write_failure_warns_once_and_turns_the_sink_off(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    sink = _Full()
    record = RunLog.open(tmp_path, "install-wow-tbc", opener=lambda _path: sink)
    with caplog.at_level(logging.WARNING, logger="yulon.support.runlog"):
        record.write("one")
        record.write("two")
        record.close()
    warnings = [r for r in caplog.records if r.name == "yulon.support.runlog"]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]
    assert record.active is False
    assert sink.closed is True


def test_a_folder_that_cannot_be_made_gives_an_inactive_log_not_an_exception(
    tmp_path: Path,
) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    record = RunLog.open(blocker / "runs", "install-wow-tbc")
    record.write("nothing happens")
    record.close()
    assert record.active is False and record.path is None


def test_runs_live_under_the_snapshot_folder_of_the_config_dir() -> None:
    assert runlog.logs_dir() == platform.config_dir() / "logs"
    assert runlog.runs_dir() == platform.config_dir() / "logs" / "runs"
