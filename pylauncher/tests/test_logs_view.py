"""The Logs tab (T93), offscreen: what it shows is redacted, and its buttons do what they say."""

from __future__ import annotations

import json
import logging
import secrets
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.conftest import HANG_BOUND, process_events, pump_until
from yulon import platform
from yulon.catalog.catalog import load_catalog
from yulon.support import bundle, runlog
from yulon.support import sources as support_sources
from yulon.support.sources import InstallFacts, LiveLog
from yulon.ui.logs_view import COPY_LINES, OPEN_FOLDER_TIP, READING, LogsView
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


def test_the_open_folder_tooltip_says_those_files_are_not_cleaned(qapp: object) -> None:
    """Lead ruling: a player must not read "log folder" as "safe to send"."""
    view = _view()
    assert view.open_folder_button.toolTip() == OPEN_FOLDER_TIP
    assert OPEN_FOLDER_TIP == (
        "Your run logs and server snapshots, as written \u2014 passwords are NOT taken out "
        "of these. Send the support file instead; Yu'lon's own log goes into it too."
    )


Held = list[tuple[Callable[[], object], Callable[[object], None], Callable[[object], None]]]


def _holding(held: Held) -> Callable[..., None]:
    return lambda work, done, failed: held.append((work, done, failed))


def test_reading_shows_until_the_read_lands_and_copy_waits_for_it(qapp: object) -> None:
    _seed_app_log(1)
    held: Held = []
    copied: list[str] = []
    view = _view(jobs=_holding(held), clipboard=copied.append)
    view.refresh()
    assert view.shown_text() == READING
    view.copy_last_lines()
    assert copied == [], "copied the placeholder while the read was in flight"
    work, done, _failed = held[0]
    done(work())
    assert view.shown_text().splitlines() == ["line 0 ***"]
    view.copy_last_lines()
    assert copied == ["line 0 ***"]


def test_a_slower_earlier_read_never_paints_over_a_newer_one(qapp: object) -> None:
    config = platform.config_dir()
    config.mkdir(parents=True, exist_ok=True)
    (config / "yulon.log").write_text("newer\n", encoding="utf-8")
    held: Held = []
    view = _view(jobs=_holding(held))
    view.refresh()
    view.refresh()
    work, done, _failed = held[1]
    done(work())
    assert view.shown_text().splitlines() == ["newer"]
    (config / "yulon.log").write_text("stale\n", encoding="utf-8")
    work, done, _failed = held[0]
    done(work())
    assert view.shown_text().splitlines() == ["newer"], "the older read painted over the newer one"


def test_a_secret_stored_after_the_first_read_is_masked_after_a_picker_change(
    qapp: object,
) -> None:
    """Lead ruling: every read rebuilds the redactor, a picker change included."""
    _seed_app_log(1)
    late = "Chan" + secrets.token_hex(8)
    runs = runlog.runs_dir()
    runs.mkdir(parents=True)
    (runs / "install-wow-vanilla-20260922T101010Z.log").write_text(
        f"settled {late}\n", encoding="utf-8"
    )
    view = _view()
    view.refresh()
    assert view.source_picker.currentText() == "App log (yulon.log)"
    # What the end of an install does: a channel credential appears.
    (platform.config_dir() / "credentials" / "wow-vanilla-0badc0de.json").write_text(
        json.dumps({"account": "OWNER", "password": late, "host": "localhost", "port": 7878}),
        encoding="utf-8",
    )
    view.source_picker.setCurrentIndex(
        view.source_picker.findText("Run: ", Qt.MatchFlag.MatchStartsWith)
    )
    assert view.source_picker.currentText().startswith("Run: ")
    assert view.shown_text().splitlines() == ["settled ***"]


def test_no_source_is_read_on_the_gui_thread(qapp: object, monkeypatch: pytest.MonkeyPatch) -> None:
    """A WSL server's folder boots its distro, a network drive blocks: never on the GUI thread."""
    _seed_app_log(1)
    seen: list[threading.Thread] = []
    real_sources, real_known = support_sources.sources_for_app, support_sources.gather_known

    def sources_for_app(*args: Any, **kwargs: Any) -> Any:
        seen.append(threading.current_thread())
        return real_sources(*args, **kwargs)

    def gather_known(*args: Any, **kwargs: Any) -> Any:
        seen.append(threading.current_thread())
        return real_known(*args, **kwargs)

    monkeypatch.setattr(support_sources, "sources_for_app", sources_for_app)
    monkeypatch.setattr(support_sources, "gather_known", gather_known)
    view = LogsView(lambda: [], CATALOG, bundle_seams=_seams())
    view.show()
    try:
        pump_until(lambda: view.shown_text().splitlines() == ["line 0 ***"], "the read lands")
    finally:
        view.hide()
    assert len(seen) >= 2
    assert all(thread is not threading.main_thread() for thread in seen), seen


def test_a_read_that_lands_after_the_tab_is_gone_is_dropped_quietly(
    qapp: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The view is deleted mid-read (the window closed): the late result must reach nothing."""
    import shiboken6

    _seed_app_log(1)
    started, gate, returned = threading.Event(), threading.Event(), threading.Event()
    real_known = support_sources.gather_known

    def gather_known(*args: Any, **kwargs: Any) -> Any:
        started.set()
        gate.wait(HANG_BOUND)
        try:
            return real_known(*args, **kwargs)
        finally:
            returned.set()

    monkeypatch.setattr(support_sources, "gather_known", gather_known)
    raised: list[object] = []
    monkeypatch.setattr(sys, "excepthook", lambda kind, value, tb: raised.append(value))
    monkeypatch.setattr(threading, "excepthook", lambda hook: raised.append(hook.exc_value))
    monkeypatch.setattr(sys, "unraisablehook", lambda hook: raised.append(hook.exc_value))
    view = LogsView(lambda: [], CATALOG, bundle_seams=_seams())
    view.refresh()
    pump_until(started.is_set, "the read starts")
    shiboken6.delete(view)
    assert not shiboken6.isValid(view)
    gate.set()
    pump_until(returned.is_set, "the read finishes")
    process_events(50)
    assert raised == []
