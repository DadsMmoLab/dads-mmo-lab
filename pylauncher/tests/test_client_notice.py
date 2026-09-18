"""T62: before installing a module that also changes the game client, the user is told.

Owner, 2026-09-15: "Before installing any modules that needs a client they should
get known about it." `Applier._client()` skips a `client` step when the install
has no client folder and says so only in the report written AFTER the server half
has landed, so `mod-arac` put its SQL and DBCs in, left `Patch-A.MPQ` out, and the
user learned nothing. These tests press the Modules tab's own routes over the real
factory wiring (`ControllerServices.for_entry()`), with only the applier and the
client-folder write replaced by recorders.

Every dialog answer a test hands back is `int(StandardButton.X)` — what PySide6's
`exec()` really returns — and one test clicks the real dialog, because a fake
returning the enum member is exactly how T33's "Yes reads as No" survived.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox

from yulon.apply import ApplyReport
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import (
    SET_CLIENT_DIR_LABEL,
    ControllerServices,
    ControllerView,
    ask_to_set_client_dir,
)
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
_REAL_EXEC = QMessageBox.exec
_POLL_MS = 20
_BOUND_MS = 5000

ARAC = wotlk_modules.load_module(
    wotlk_modules.BUNDLED_MANIFESTS_DIR / "wow-wotlk" / "modules" / "mod-arac.json"
)
PLAIN = "mod-aoe-loot"
"""A shipped module with no `client` step and no prompt without a default."""


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


class _Applier:
    """Records installs; anything else the tab might call is a test failure."""

    def __init__(self) -> None:
        self.installed: list[str] = []

    def install(self, manifest: Manifest, values: object = None) -> ApplyReport:
        self.installed.append(manifest.id)
        return ApplyReport("install", manifest.id, family=manifest.type, done=("clone",))

    def remove(self, manifest: Manifest, values: object = None) -> ApplyReport:
        raise AssertionError("no test here removes")

    def update(self, manifest: Manifest, values: object = None) -> ApplyReport:
        raise AssertionError("no test here updates")


class _Dialogs:
    """`QMessageBox.exec`, answering `answer` and keeping what each box said."""

    def __init__(self, answer: QMessageBox.StandardButton) -> None:
        self.answer = int(answer)  # the type the real exec() returns (T33)
        self.shown: list[dict[str, Any]] = []

    def __call__(self, box: QMessageBox) -> int:
        self.shown.append(
            {
                "title": box.windowTitle(),
                "text": box.text(),
                "buttons": sorted(b.text() for b in box.buttons()),
            }
        )
        return self.answer


def _view(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    client_dir: Path | None = None,
    answer: QMessageBox.StandardButton = QMessageBox.StandardButton.Cancel,
    pick: Callable[..., Path | None] = lambda *_: None,
    writable: bool = True,
    **view_kw: Any,
) -> tuple[ControllerView, _Applier, _Dialogs, list[Path | None]]:
    server_dir = tmp_path / "server"
    server_dir.mkdir(exist_ok=True)
    services = ControllerServices.for_entry(WOTLK, server_dir, client_dir=client_dir)
    applier = _Applier()
    services.applier = applier  # type: ignore[assignment]
    written: list[Path | None] = []
    services.set_client_dir = written.append if writable else None
    dialogs = _Dialogs(answer)
    monkeypatch.setattr(QMessageBox, "exec", lambda box: dialogs(box))
    view = ControllerView(WOTLK, services, status_poll_ms=0, pick_client_dir=pick, **view_kw)
    return view, applier, dialogs, written


def _a_client_folder(tmp_path: Path) -> Path:
    client = tmp_path / "wow-3.3.5a"
    (client / "Data").mkdir(parents=True)
    return client


def test_install_of_a_client_module_with_no_client_folder_asks_and_cancel_installs_nothing(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The owner's case: ARAC, no client folder, Install — the notice, and nothing runs."""
    view, applier, dialogs, written = _view(monkeypatch, tmp_path)

    view._row_install("mod-arac")

    assert applier.installed == [], "the install ran before the user was told"
    assert len(dialogs.shown) == 1, dialogs.shown
    shown = dialogs.shown[0]
    assert shown["title"] == "All Races All Classes (ARAC) needs your game client"
    assert "also changes your WoW game client, not only the server" in shown["text"]
    assert "Patch-A.MPQ" in shown["text"]
    assert "No client folder is set for this install" in shown["text"]
    assert "Nothing has been installed yet." in shown["text"]
    assert shown["buttons"] == sorted([SET_CLIENT_DIR_LABEL, "Cancel"])
    assert view.module_report.toPlainText() == (
        "install mod-arac: not started — All Races All Classes (ARAC) also changes your game "
        "client, and no client folder is set for this install. Nothing on this machine was "
        "changed."
    )
    assert written == []


def test_set_client_folder_on_the_notice_is_the_server_tab_s_own_press(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ "Set client folder…" opens the picker and records the folder; the install waits.

    The tab is rebuilt on `client_dir_changed`, so the press ends there and the
    user presses Install again on a tab where the folder is set.
    """
    client = _a_client_folder(tmp_path)
    picked: list[object] = []

    def pick(*args: object) -> Path:
        picked.append(args)
        return client

    view, applier, dialogs, written = _view(
        monkeypatch, tmp_path, answer=QMessageBox.StandardButton.Yes, pick=pick
    )
    changed: list[tuple[object, ...]] = []
    view.client_dir_changed.connect(lambda *a: changed.append(a))

    view._row_install("mod-arac")

    assert len(dialogs.shown) == 1
    assert len(picked) == 1, "Set client folder… did not open the picker"
    assert written == [client]
    assert changed and changed[0][2] == client
    assert applier.installed == [], "installed in the same press, on a tab about to be rebuilt"


def test_with_a_client_folder_set_the_install_asks_nothing(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view, applier, dialogs, _written = _view(
        monkeypatch, tmp_path, client_dir=_a_client_folder(tmp_path)
    )

    view._row_install("mod-arac")

    assert dialogs.shown == []
    assert applier.installed == ["mod-arac"]


def test_a_module_with_no_client_step_asks_nothing_even_with_no_folder(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The notice is about the module, not about the install lacking a folder."""
    view, applier, dialogs, _written = _view(monkeypatch, tmp_path)
    plain = next(m for m in wotlk_modules.store().load_all("module") if m.id == PLAIN)
    assert not plain.client

    view._row_install(PLAIN)

    assert dialogs.shown == []
    assert applier.installed == [PLAIN]


def test_the_row_menu_s_install_asks_too(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The context menu reaches the same handler; it must not be a way around the notice."""
    view, applier, dialogs, _written = _view(monkeypatch, tmp_path)
    view.modules_panel.select("mod-arac")
    menu = view._module_menu("mod-arac")
    install = next(a for a in menu.actions() if a.text() == "Install Selected Module")

    install.trigger()

    assert len(dialogs.shown) == 1
    assert applier.installed == []


def test_a_custom_module_carrying_a_client_step_is_asked_about_too(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The link route. Derived manifests carry no `client` today; the gate does not assume so."""
    installed: list[str] = []
    derived = ARAC.model_copy(update={"id": "mod-my-arac", "name": "My ARAC"})
    view, applier, dialogs, _written = _view(
        monkeypatch,
        tmp_path,
        link_asker=lambda parent, title: "https://github.com/you/mod-my-arac",
    )
    view.services.module_from_link = lambda text: derived
    view.services.module_install_custom = lambda manifest, folder: (
        installed.append(manifest.id) or ApplyReport("install", manifest.id, family=manifest.type)
    )
    view._set_custom_module_buttons()

    view.install_module_from_link()

    assert len(dialogs.shown) == 1
    assert "My ARAC also changes your WoW game client" in dialogs.shown[0]["text"]
    assert installed == [] and applier.installed == []
    assert view.module_report.toPlainText().startswith("install from link mod-my-arac: not started")


def test_with_no_way_to_set_a_folder_the_notice_is_still_given(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A tab with no client-folder write seam has no button to offer; it still says why."""
    told: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda parent, title, text, *a: told.append((title, text))
    )
    view, applier, dialogs, _written = _view(monkeypatch, tmp_path, writable=False)

    view._row_install("mod-arac")

    assert dialogs.shown == []
    assert len(told) == 1 and "Patch-A.MPQ" in told[0][1]
    assert applier.installed == []


# ------------------------------------------------------------ the real dialog


def _click_when_up(label: str, clicked: list[str], deadline: object) -> None:
    from PySide6.QtCore import QDeadlineTimer, QTimer
    from PySide6.QtWidgets import QApplication

    assert isinstance(deadline, QDeadlineTimer)
    widget = QApplication.activeModalWidget()
    if isinstance(widget, QMessageBox):
        button = next(b for b in widget.buttons() if b.text() == label)
        clicked.append(label)
        button.click()
        return
    if not deadline.hasExpired():
        QTimer.singleShot(_POLL_MS, lambda: _click_when_up(label, clicked, deadline))


@pytest.mark.parametrize(("label", "expected"), [(SET_CLIENT_DIR_LABEL, True), ("Cancel", False)])
def test_the_real_dialog_reads_each_button_as_what_it_says(
    qapp: object, monkeypatch: pytest.MonkeyPatch, label: str, expected: bool
) -> None:
    """No fake `exec`: a real modal, a real click, and the answer read the way the app reads it.

    The only test here that can see the return TYPE of `exec()`. With `is
    StandardButton.Yes` in place of `said_yes()` the Set press reads False.
    """
    from PySide6.QtCore import QDeadlineTimer, QTimer
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(QMessageBox, "exec", _REAL_EXEC)
    clicked: list[str] = []
    _click_when_up(label, clicked, QDeadlineTimer(_BOUND_MS))
    giveup = QTimer()
    giveup.setSingleShot(True)
    giveup.timeout.connect(
        lambda: isinstance(QApplication.activeModalWidget(), QMessageBox)
        and QApplication.activeModalWidget().close()  # type: ignore[union-attr]
    )
    giveup.start(_BOUND_MS)
    try:
        answer = ask_to_set_client_dir(None, ARAC)  # type: ignore[arg-type]
    finally:
        giveup.stop()

    assert clicked == [label], "the dialog never became the active modal"
    assert answer is expected
