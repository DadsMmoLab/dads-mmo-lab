"""The line after Make a support file says what it left out of the zip (T609).

A log left out for size, or because the cleaner could not vouch for it, was told only in
MANIFEST.txt inside the zip, while the player was told "Send this file". The cleaner removes
tokens and home paths as well as passwords, so the words say "secrets".
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from tests.test_logs_view import _view
from tests.test_support_bundle import _cut_fails, _noisy_installs, _noisy_live, _seams
from yulon import platform
from yulon.support import bundle
from yulon.support.sources import Sources


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
    assert "MANIFEST.txt names it." in text, text
    assert "Send this file" in text
    assert "live/wow-tbc.log" not in text, "the line counts the logs; the zip's list names them"


def test_both_reasons_are_told_in_one_line(qapp: object, tmp_path: Path) -> None:
    text = _status(tmp_path / "s.zip", dropped=("snapshots/a.log",), unvouched=("live/a", "live/b"))
    assert "1 log left out to keep the file small" in text, text
    assert "2 logs left out because the cleaner could not vouch for them" in text, text
    assert "MANIFEST.txt names them." in text, text
    assert "live/a" not in text and "snapshots/a.log" not in text
    assert "\n" not in text


def test_the_short_password_warning_still_names_what_was_left_out(
    qapp: object, tmp_path: Path
) -> None:
    text = _status(tmp_path / "s.zip", unvouched=("live/a",), short_passwords=("account OWNER",))
    assert "1 log left out because the cleaner could not vouch for it" in text, text


@pytest.mark.parametrize("floor_kib", [10**9, 600], ids=["first-cut-fails", "second-cut-fails"])
def test_the_count_is_the_length_of_the_list_in_the_manifest_the_bundle_wrote(
    qapp: object, tmp_path: Path, floor_kib: int
) -> None:
    """A real bundle, its real MANIFEST.txt: the line's number is the list's length.

    Mutation: count `report.cut` in place of `report.unvouched` and the numbers part.
    """
    dest = tmp_path / "s.zip"
    report = bundle.build(
        dest,
        Sources(platform.config_dir(), None, _noisy_installs(tmp_path, 3, 1000)),
        _cut_fails(floor_kib),
        seams=_seams(live_logs=_noisy_live(1280 * 1024)),
        cap_bytes=3_000_000,
    )
    with zipfile.ZipFile(dest) as archive:
        manifest = archive.read("MANIFEST.txt").decode("utf-8")
    _, _, rest = manifest.partition("could not vouch for the shorter text:")
    listed = [line.strip() for line in rest.split("\n\n", 1)[0].splitlines() if line.strip()]
    assert listed and len(listed) == len(report.unvouched)

    text = _status(dest, unvouched=report.unvouched, dropped=report.dropped)

    word = "log" if len(listed) == 1 else "logs"
    assert f"{len(listed)} {word} left out because the cleaner could not vouch for" in text, text
    assert not any(name in text for name in listed)
