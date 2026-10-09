"""The line after Make a support file says what it left out of the zip (T609).

A log left out for size, or because the cleaner could not vouch for it, was told only in
MANIFEST.txt inside the zip, while the player was told "Send this file". The cleaner removes
tokens and home paths as well as passwords, so the words say "secrets".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_logs_view import _view
from yulon.support import bundle


def _status(dest: Path, **report: object) -> str:
    fields: dict[str, object] = {
        "path": dest,
        "included": ("MANIFEST.txt",),
        "skipped": (),
        "dropped": (),
        "size": 1_000,
    }
    fields.update(report)
    shown = bundle.BundleReport(**fields)  # type: ignore[arg-type]
    view = _view(
        pick_save_path=lambda parent, suggested: dest,
        jobs=lambda work, done, failed: done(shown),
    )
    view.save_for_support()
    return view.status.text()


def test_a_report_that_left_nothing_out_says_nothing_of_it(qapp: object, tmp_path: Path) -> None:
    assert "left out" not in _status(tmp_path / "s.zip")


def test_logs_dropped_for_size_are_counted_with_the_reason(qapp: object, tmp_path: Path) -> None:
    """Mutation: stop reading `report.dropped` in `_saved()` and this loses the count."""
    text = _status(tmp_path / "s.zip", dropped=("snapshots/a.log", "snapshots/b.log"))
    assert "2 logs left out to keep the file small" in text, text
    assert "MANIFEST.txt" in text


def test_a_log_the_cleaner_could_not_vouch_for_is_counted_with_the_reason(
    qapp: object, tmp_path: Path
) -> None:
    """Mutation: stop reading `report.unvouched` in `_saved()` and this loses the sentence."""
    text = _status(tmp_path / "s.zip", unvouched=("live/wow-tbc.log",))
    assert "1 log left out because the cleaner could not vouch for it" in text, text
    assert "Send this file" in text


def test_both_reasons_are_told_in_one_line(qapp: object, tmp_path: Path) -> None:
    text = _status(tmp_path / "s.zip", dropped=("snapshots/a.log",), unvouched=("live/a", "live/b"))
    assert "1 log left out to keep the file small" in text, text
    assert "2 logs left out because the cleaner could not vouch for them" in text, text
    assert "\n" not in text


def test_the_short_password_warning_still_names_what_was_left_out(
    qapp: object, tmp_path: Path
) -> None:
    text = _status(tmp_path / "s.zip", unvouched=("live/a",), short_passwords=("account OWNER",))
    assert "1 log left out because the cleaner could not vouch for it" in text, text


@pytest.mark.parametrize("n", [1, 3])
def test_the_count_agrees_with_the_manifest_lists(qapp: object, tmp_path: Path, n: int) -> None:
    names = tuple(f"live/{i}.log" for i in range(n))
    text = _status(tmp_path / "s.zip", unvouched=names)
    assert text.count("left out") == 1
    assert f"{n} log" in text
