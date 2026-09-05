"""7.10's last install-half item: the cancel copy arriving from a REAL cancelled install.

`pyplan/gates/7.10-gaps/README.md` (2026-09-04) drove `LogPanel`'s Stop against a
log follow and rendered `cancelled_install_message()` by hand, and said plainly
that the copy had not been seen arriving from an install a person cancelled,
because that box refused every install at preflight (free space + a running
server). This box is `clean-ssh` after 7.2's press 1: Docker installed, no
server, 76 GB free -- so an install STARTS here, and can be stopped.

What this does, with real widgets and real clicks (`QTest.mouseClick`):

  1. builds the real `CatalogView` over a real `LogPanel`, with the real
     `installer_for_app` factory `main.py` passes; only the folder picker is
     injected so the folder is a throwaway and not a modal file dialog;
  2. clicks the WotLK tile's `Install`;
  3. waits until the engine's own transcript says it is cloning the core
     (`clone-core` is the first stage and takes ~5 min on this box);
  4. clicks the panel's `Stop`, and records the time;
  5. waits for `install_finished`, records what the modal dialog said, what the
     signal carried, what the panel says, and what is in the folder;
  6. renders `cancelled_install_message()` for the folder AS IT IS THEN and
     reports whether the copy the widget showed is that string.

It does NOT decide whether the cancel was quick. `git.py` has no cancel: the
containerized clone runs to its end and the spine's `_check_cancel()` raises
between stages, so the time from Stop to the dialog is a measurement, written
down, not a pass/fail. `docker ps` is read 5 s after Stop for the same reason:
if the clone container is still there, that is what a user's Stop costs.

Run under `sg docker -c` (press 1 joined the group; this shell was open before)
with QT_QPA_PLATFORM=offscreen.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/home/pk/gate72/pylauncher")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from yulon.catalog.catalog import load_catalog  # noqa: E402
from yulon.catalog.installer import cancelled_install_message  # noqa: E402
from yulon.catalog.installer import compose_file  # noqa: E402
from yulon.install_wiring import installer_for_app  # noqa: E402
from yulon.ui.catalog_view import CatalogView  # noqa: E402
from yulon.ui.widgets.log_panel import LogPanel  # noqa: E402

CHOSEN = Path("/home/pk/gate72-cancel-install")
CLONE_LINE = "Cloning mod-playerbots/azerothcore-wotlk"
SECONDS_INTO_CLONE_BEFORE_STOP = 20.0

PASSES = 0
FAILS = 0
MODALS: list[tuple[str, str, str]] = []  # (stamp, title, text)


def stamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def say(text: str = "") -> None:
    print(f"{stamp()} {text}" if text else "", flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSES, FAILS
    if ok:
        PASSES += 1
        say(f"[OK]   {label}" + (f" -- {detail}" if detail else ""))
    else:
        FAILS += 1
        say(f"[FAIL] {label}" + (f" -- {detail}" if detail else ""))


def sh(argv: list[str]) -> str:
    done = subprocess.run(argv, capture_output=True, text=True)
    return (done.stdout + done.stderr).strip()


def pump(predicate, timeout_s: float, label: str) -> bool:
    app = QApplication.instance()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            app.processEvents()
            return True
        time.sleep(0.05)
    say(f"       (timed out after {timeout_s}s waiting for {label})")
    return False


def panel_text(panel: LogPanel) -> str:
    value = panel.text
    return value if isinstance(value, str) else value()


def panel_flag(panel: LogPanel, name: str) -> bool:
    value = getattr(panel, name)
    return bool(value) if isinstance(value, bool) else bool(value())


def button_named(widget, text: str) -> QPushButton | None:
    for b in widget.findChildren(QPushButton):
        if b.text() == text:
            return b
    return None


def install_modal_watcher(app) -> QTimer:
    """Dismiss modal dialogs the way a user would -- by clicking a real button --
    and keep every word they showed."""
    timer = QTimer()
    timer.setInterval(150)

    def tick() -> None:
        modal = app.activeModalWidget()
        if modal is None:
            return
        title = modal.windowTitle()
        text = modal.text() if isinstance(modal, QMessageBox) else "<not a QMessageBox>"
        MODALS.append((stamp(), title, text))
        say(f"       [modal] title={title!r}")
        for line in text.splitlines():
            say(f"       [modal] {line}")
        if isinstance(modal, QMessageBox):
            btn = modal.defaultButton() or (modal.buttons()[0] if modal.buttons() else None)
            if btn is not None:
                say(f"       [modal] clicking {btn.text()!r}")
                QTest.mouseClick(btn, Qt.MouseButton.LeftButton)
                return
        modal.close()

    timer.timeout.connect(tick)
    timer.start()
    return timer


def folder_listing(path: Path) -> str:
    if not path.exists():
        return "<folder does not exist>"
    names = sorted(p.name for p in path.iterdir())
    return f"{len(names)} entries: {names[:12]}{' ...' if len(names) > 12 else ''}"


def main() -> int:
    app = QApplication([])
    watcher = install_modal_watcher(app)
    catalog = load_catalog()
    entry = catalog.get("wow-wotlk")

    say("=" * 78)
    say("7.10 widget cancel: a real install, started by the tile's Install, stopped by the panel's Stop")
    say("=" * 78)
    say(f"       checkout /home/pk/gate72/pylauncher; folder {CHOSEN}")
    say(f"       id -Gn -> {sh(['id', '-Gn'])}")
    say(f"       docker images before -> {sh(['docker', 'images', '--format', '{{.Repository}}:{{.Tag}}'])!r}")
    CHOSEN.mkdir(exist_ok=True)
    say(f"       folder before the click: {folder_listing(CHOSEN)}")

    panel = LogPanel()
    picked: list[Path] = []

    def pick_dir(_parent, _title, _start):
        picked.append(CHOSEN)
        return CHOSEN

    def ask_suggestion(_parent, _game, _suggested):
        return False

    view = CatalogView(catalog, installer_for_app, panel, pick_dir=pick_dir, ask_suggestion=ask_suggestion)
    view.resize(900, 700)
    view.show()
    app.processEvents()

    results: list[tuple[str, bool, str]] = []
    started: list[str] = []
    view.install_finished.connect(lambda g, ok, m: results.append((g, ok, m)))
    view.install_started.connect(lambda g: started.append(g))

    install_button = view.button_for("wow-wotlk")
    check("the WotLK tile's Install is a real, enabled QPushButton",
          install_button.text() == "Install" and install_button.isEnabled(),
          f"text={install_button.text()!r}")
    stop_button = button_named(panel, "Stop")
    check("the panel's Stop exists and is dead while idle",
          stop_button is not None and not stop_button.isEnabled())

    say("\n-- click Install")
    t_click = time.time()
    QTest.mouseClick(install_button, Qt.MouseButton.LeftButton)
    pump(lambda: bool(started) or bool(results), 30, "install_started")
    check("the click reached the folder picker and started a job",
          picked == [CHOSEN] and started == ["wow-wotlk"], f"picked={picked} started={started}")
    check("Stop went live once the install was running", stop_button.isEnabled())

    say("\n-- wait for the engine's own clone-core line in the panel")
    reached = pump(lambda: CLONE_LINE in panel_text(panel) or bool(results), 600, "the clone-core line")
    t_clone = time.time()
    if results and not reached:
        say("       the install ended BEFORE the clone began; whatever it said:")
        for line in results[0][2].splitlines():
            say(f"         {line}")
    check("the engine reached clone-core (its transcript says so)", CLONE_LINE in panel_text(panel),
          f"{t_clone - t_click:.1f}s after the click")
    say("       panel so far:")
    for line in [ln for ln in panel_text(panel).splitlines() if ln.strip()][-8:]:
        say(f"         | {line[:200]}")

    say(f"\n-- let the clone run {SECONDS_INTO_CLONE_BEFORE_STOP:.0f}s, then click Stop")
    pump(lambda: bool(results), SECONDS_INTO_CLONE_BEFORE_STOP, "nothing (a deliberate pause)")
    say(f"       folder at Stop: {folder_listing(CHOSEN)}")
    say(f"       docker ps at Stop: {sh(['docker', 'ps', '--format', '{{.Names}} {{.Image}} {{.Status}}'])!r}")
    check("Stop is enabled at the moment of the click", stop_button.isEnabled())
    t_stop = time.time()
    say(f"       CLICK Stop at {stamp()}")
    QTest.mouseClick(stop_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    say(f"       panel.cancelled right after the click: {panel_flag(panel, 'cancelled')}; status: {panel.status_text()!r}")
    pump(lambda: bool(results), 5, "5 s after Stop")
    say(f"       docker ps 5 s after Stop: {sh(['docker', 'ps', '--format', '{{.Names}} {{.Image}} {{.Status}}'])!r}")

    say("\n-- wait for install_finished (the clone cannot be interrupted; the spine raises after it)")
    got = pump(lambda: bool(results), 1200, "install_finished")
    t_done = time.time()
    check("install_finished arrived", got, f"{t_done - t_stop:.1f}s after Stop")
    say(f"       docker ps after finish: {sh(['docker', 'ps', '-a', '--format', '{{.Names}} {{.Image}} {{.Status}}'])!r}")
    say("       panel tail:")
    for line in [ln for ln in panel_text(panel).splitlines() if ln.strip()][-12:]:
        say(f"         | {line[:200]}")
    say(f"       panel.running={panel_flag(panel, 'running')} panel.cancelled={panel_flag(panel, 'cancelled')} status={panel.status_text()!r}")
    check("the panel reports the job as CANCELLED", panel_flag(panel, "cancelled"))

    if results:
        game_id, ok, message = results[0]
        say(f"       install_finished -> game={game_id!r} ok={ok}")
        say("       message carried by the signal:")
        for line in message.splitlines():
            say(f"         {line}")
        check("the signal says NOT installed (ok=False)", ok is False)
        say(f"       folder after: {folder_listing(CHOSEN)}")
        say(f"       compose file there: {compose_file(CHOSEN)}")
        expected = cancelled_install_message(entry.name, CHOSEN)
        say("       cancelled_install_message() for the folder AS IT IS NOW:")
        for line in expected.splitlines():
            say(f"         {line}")
        check("the message the widget emitted IS cancelled_install_message() for this folder state",
              message == expected)
        cancel_modals = [(s, t, x) for s, t, x in MODALS if t == "Install cancelled"]
        check("a modal titled 'Install cancelled' was shown to the user",
              bool(cancel_modals), f"modal titles seen: {[t for _, t, _ in MODALS]}")
        if cancel_modals:
            check("the modal's text is that same copy", cancel_modals[0][2] == expected)
        check("the tile still reads Install (nothing remembered)",
              view.button_for("wow-wotlk").text() == "Install",
              f"reads {view.button_for('wow-wotlk').text()!r}")
        check("the copy names the folder", str(CHOSEN) in message)
        check("the copy says Stop undid nothing", "undoes nothing" in message)
    else:
        check("install_finished was emitted", False, "no result at all")

    watcher.stop()
    say("")
    say("=" * 78)
    say(f"RESULT: {PASSES} OK, {FAILS} FAIL   (click->clone {t_clone - t_click:.1f}s, Stop->finished {t_done - t_stop:.1f}s)")
    say("=" * 78)
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
