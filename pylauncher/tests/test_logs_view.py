"""The Logs tab (T93), offscreen: what it shows is redacted, and its buttons do what they say."""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import platform
from yulon.catalog.catalog import load_catalog
from yulon.support import bundle, runlog
from yulon.support.sources import InstallFacts, LiveLog
from yulon.ui.logs_view import COPY_LINES, LogsView
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()


def _no_docker(install: InstallFacts, silent: set[str | None]) -> list[LiveLog]:
    return [LiveLog("w", "up\n")]


def _seams() -> bundle.Seams:
    return bundle.Seams(live_logs=_no_docker, docker_version=lambda distro: None)


def _view(**kwargs: object) -> LogsView:
    base: dict[str, object] = {"jobs": run_inline, "bundle_seams": _seams()}
    base.update(kwargs)
    return LogsView(lambda: [], CATALOG, **base)  # type: ignore[arg-type]


def _seed_app_log(lines: int) -> str:
    """An app log whose every line carries a channel password the tab must hide."""
    config = platform.config_dir()
    secret = "Soap" + secrets.token_hex(8)
    (config / "credentials").mkdir(parents=True, exist_ok=True)
    (config / "credentials" / "wow-tbc-0badc0de.json").write_text(
        json.dumps({"account": "OWNER", "password": secret, "host": "localhost", "port": 7878}),
        encoding="utf-8",
    )
    (config / "yulon.log").write_text(
        "".join(f"line {n} {secret}\n" for n in range(lines)), encoding="utf-8"
    )
    return secret


def _report(dest: Path, size: int) -> bundle.BundleReport:
    return bundle.BundleReport(
        path=dest, included=("MANIFEST.txt",), skipped=(), dropped=(), size=size
    )


def test_the_tab_reads_nothing_until_it_is_shown(qapp: object) -> None:
    """Built before a test points `config_dir()` anywhere: the constructor must not read."""
    _seed_app_log(1)
    view = _view()
    assert view.source_picker.count() == 0
    view.show()
    try:
        assert view.source_picker.currentText() == "App log (yulon.log)"
    finally:
        view.hide()


def test_the_viewer_shows_the_app_log_redacted(qapp: object) -> None:
    secret = _seed_app_log(3)
    view = _view()
    view.refresh()
    assert view.source_picker.currentText() == "App log (yulon.log)"
    assert view.shown_text().splitlines() == ["line 0 ***", "line 1 ***", "line 2 ***"]
    assert secret not in view.shown_text()


def test_copy_puts_exactly_the_last_200_redacted_lines_on_the_clipboard(qapp: object) -> None:
    secret = _seed_app_log(250)
    copied: list[str] = []
    view = _view(clipboard=copied.append)
    view.refresh()
    view.copy_last_lines()
    assert copied == ["\n".join(f"line {n} ***" for n in range(250 - COPY_LINES, 250))]
    assert secret not in copied[0]


def test_a_cancelled_save_dialog_writes_nothing_and_logs_nothing_above_debug(
    qapp: object, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    ran: list[object] = []
    view = _view(pick_save_path=lambda parent, suggested: None, jobs=lambda w, d, e: ran.append(w))
    before = sorted(p for p in tmp_path.rglob("*"))
    with caplog.at_level(logging.DEBUG, logger="yulon"):
        assert view.save_for_support() is False
    assert ran == [], "a job started for a cancelled dialog"
    assert sorted(p for p in tmp_path.rglob("*")) == before
    assert [
        r for r in caplog.records if r.levelno > logging.DEBUG and r.name.startswith("yulon.ui")
    ] == []
    assert view.busy_reason() is None


def test_the_save_dialog_is_offered_a_dated_zip_name(qapp: object) -> None:
    offered: list[Path] = []

    def pick(parent: object, suggested: Path) -> Path | None:
        offered.append(suggested)
        return None

    view = _view(pick_save_path=pick)
    view.save_for_support()
    assert len(offered) == 1
    assert offered[0].name.startswith("yulon-support-") and offered[0].suffix == ".zip"


def test_save_writes_the_zip_and_says_where_and_how_big(qapp: object, tmp_path: Path) -> None:
    _seed_app_log(2)
    dest = tmp_path / "out.zip"
    view = _view(pick_save_path=lambda parent, suggested: dest)
    assert view.save_for_support() is True
    assert dest.is_file()
    text = view.status.text()
    assert str(dest) in text
    assert " KB" in text or " MB" in text, text
    assert "BundleReport" not in text, "the report's repr reached the status line"
    assert "larger than" not in text
    assert view.busy_reason() is None
    assert view.save_button.isEnabled()


def test_a_bundle_over_the_cap_warns_about_discord_and_points_at_the_manifest(
    qapp: object, tmp_path: Path
) -> None:
    dest = tmp_path / "big.zip"
    view = _view(
        pick_save_path=lambda parent, suggested: dest,
        jobs=lambda work, done, failed: done(_report(dest, bundle.ZIP_CAP + 1)),
    )
    assert view.save_for_support() is True
    text = view.status.text()
    assert str(dest) in text
    assert "Discord" in text and "MANIFEST.txt" in text, text


def test_a_bundle_at_the_cap_does_not_warn(qapp: object, tmp_path: Path) -> None:
    dest = tmp_path / "exact.zip"
    view = _view(
        pick_save_path=lambda parent, suggested: dest,
        jobs=lambda work, done, failed: done(_report(dest, bundle.ZIP_CAP)),
    )
    view.save_for_support()
    assert "Discord" not in view.status.text()


def test_a_save_in_flight_makes_the_tab_busy_until_it_ends(qapp: object, tmp_path: Path) -> None:
    held: list[tuple[Callable[[], object], Callable[[object], None], Callable[[object], None]]] = []
    view = _view(
        pick_save_path=lambda parent, suggested: tmp_path / "s.zip",
        jobs=lambda work, done, failed: held.append((work, done, failed)),
    )
    assert view.save_for_support() is True
    assert view.busy_reason() is not None
    assert not view.save_button.isEnabled()
    assert view.save_for_support() is False, "a second save started over the first"
    work, done, _failed = held[0]
    done(work())
    assert view.busy_reason() is None
    assert view.save_button.isEnabled()


def test_a_save_the_disk_refuses_says_what_to_do_and_frees_the_tab(
    qapp: object, tmp_path: Path
) -> None:
    """A real `OSError` out of `bundle.save`: the path's parent is a file."""
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    view = _view(pick_save_path=lambda parent, suggested: blocker / "s.zip")
    assert view.save_for_support() is True
    text = view.status.text()
    assert text.startswith("Could not save s.zip: "), text
    assert "Traceback" not in text
    assert view.busy_reason() is None
    assert view.save_button.isEnabled()


def test_a_save_that_fails_names_the_file_the_reason_and_the_likely_fix(
    qapp: object, tmp_path: Path
) -> None:
    """The failing seam: what a Windows share violation looks like when the old zip is open."""
    denied = PermissionError(13, "Permission denied", str(tmp_path / "support.zip"))
    view = _view(
        pick_save_path=lambda parent, suggested: tmp_path / "support.zip",
        jobs=lambda work, done, failed: failed(denied),
    )
    view.save_for_support()
    text = view.status.text()
    assert text.startswith("Could not save support.zip: Permission denied."), text
    assert "close it and try again" in text
    assert "PermissionError" not in text and "Errno" not in text


def test_open_log_folder_opens_the_logs_folder_and_never_the_config_dir(qapp: object) -> None:
    """Lead ruling: the config dir also holds `credentials/` and `db-secrets/` in clear text."""
    _seed_app_log(1)
    opened: list[Path] = []
    view = _view(open_folder=opened.append)
    view.open_log_folder()
    assert opened == [runlog.logs_dir()]
    assert opened[0] != platform.config_dir()
    assert opened[0].is_dir(), "the folder was opened before it existed"
    assert "support file" in view.open_folder_button.toolTip()


def test_a_long_run_log_name_does_not_widen_the_tab(qapp: object) -> None:
    runs = runlog.runs_dir()
    runs.mkdir(parents=True)
    (runs / "rebuild-wow-tortoise-0badc0de-20260922T101010Z-12.log").write_text(
        "x\n", encoding="utf-8"
    )
    view = _view()
    view.refresh()
    assert view.minimumSizeHint().width() <= 600, view.minimumSizeHint()
