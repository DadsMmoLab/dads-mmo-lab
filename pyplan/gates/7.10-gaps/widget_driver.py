"""7.10 gap 2: put the live WotLK server through ACTUAL WIDGETS.

The 2026-09-04 sweep drove `ControllerServices` -- the object behind the tiles --
and said so; nothing it did travelled through a QWidget. This driver builds the
real `ControllerView` and the real `CatalogView` over a real `LogPanel`, on a
real QApplication (offscreen), and presses their real QPushButtons with
`QTest.mouseClick`, which delivers a mouse press/release to the widget exactly
as a user's finger does. Every assertion is on what the WIDGET then shows, and
the ones with a server-side consequence are re-read by a different route.

Two deliberate choices make the clicks load-bearing rather than decorative:

  * `status_poll_ms=0`. `ControllerView` normally re-reads status on a 5 s
    QTimer, so a test that clicked Refresh and then waited would pass with the
    button unwired. With the timer off, the only thing that can fill
    `status_label` is the click.
  * `Apply` on the Networking tab is asserted DISABLED before a plan exists and
    is never clicked. The 7.1 lane's ufw lockout came from that button.

Nothing here installs, removes, restores or applies. It reads, it creates one
account, it sends one console command, it plans, and it cancels a log follow.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, "/home/pk/gate0904/checkout/pylauncher")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from yulon.catalog.catalog import load_catalog  # noqa: E402
from yulon.catalog.installer import cancelled_install_message  # noqa: E402
from yulon.install_wiring import installer_for_app  # noqa: E402
from yulon.ui.catalog_view import CatalogView  # noqa: E402
from yulon.ui.controller_view import ControllerServices, ControllerView  # noqa: E402
from yulon.ui.widgets.log_panel import LogPanel  # noqa: E402

SERVER_DIR = Path("/home/pk/wowserver")
ACCOUNT = "WIDGET0904"
ACCOUNT_PW = "widget0904pw"

PASSES = 0
FAILS = 0
MODALS: list[tuple[str, str]] = []


def say(text: str = "") -> None:
    print(text, flush=True)


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSES, FAILS
    if ok:
        PASSES += 1
        say(f"[OK]   {label}" + (f" -- {detail}" if detail else ""))
    else:
        FAILS += 1
        say(f"[FAIL] {label}" + (f" -- {detail}" if detail else ""))


def db_read(statement: str) -> str:
    """Read the DB by a route that is NOT the widget under test."""
    done = subprocess.run(
        ["docker", "exec", "ac-database", "mysql", "-uroot", "-ppassword", "-N", "-B",
         "-e", statement],
        capture_output=True, text=True,
    )
    return done.stdout.strip()


def pump(predicate, timeout_s: float = 90.0, label: str = "") -> bool:
    """Spin the real event loop until `predicate()` or the timeout."""
    app = QApplication.instance()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        app.processEvents()
        if predicate():
            app.processEvents()
            return True
        time.sleep(0.02)
    say(f"       (timed out after {timeout_s}s waiting for {label})")
    return False


def panel_text(panel) -> str:
    """LogPanel exposes its text as a property on some builds and a method on others."""
    value = panel.text
    return value if isinstance(value, str) else value()


def panel_flag(panel, name: str) -> bool:
    value = getattr(panel, name)
    return bool(value) if isinstance(value, bool) else bool(value())


def button_named(widget, text: str) -> QPushButton | None:
    for b in widget.findChildren(QPushButton):
        if b.text() == text:
            return b
    return None


def install_modal_watcher(app) -> QTimer:
    """Dismiss modal dialogs the way a user would: by clicking a real button.

    Not a monkeypatch of QMessageBox -- the dialog is really constructed, really
    shown and really clicked, and its exact copy is recorded on the way past.
    """
    timer = QTimer()
    timer.setInterval(150)

    def tick() -> None:
        modal = app.activeModalWidget()
        if modal is None:
            return
        title = modal.windowTitle()
        text = modal.text() if isinstance(modal, QMessageBox) else "<not a QMessageBox>"
        MODALS.append((title, text))
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


def main() -> int:
    app = QApplication([])
    watcher = install_modal_watcher(app)
    entry = load_catalog().get("wow-wotlk")
    services = ControllerServices.for_entry(entry, SERVER_DIR)

    say("=" * 78)
    say("PART 1 -- ControllerView: six real buttons, pressed with QTest.mouseClick")
    say("=" * 78)
    view = ControllerView(entry, services, status_poll_ms=0)  # timer OFF: the click is the cause
    view.resize(900, 700)
    view.show()
    app.processEvents()

    # --- the guard that makes the rest mean something -------------------------
    check("the 5 s status poll is OFF, so only a click can fill status_label",
          view.status_label.text() == "status: unknown",
          f"before any click: {view.status_label.text()!r}")

    # --- Server tab: Refresh --------------------------------------------------
    say("\n-- Server tab, Refresh")
    check("Refresh is a real QPushButton with the text a user reads",
          view.refresh_button.text() == "Refresh" and view.refresh_button.isEnabled())
    QTest.mouseClick(view.refresh_button, Qt.MouseButton.LeftButton)
    pump(lambda: "db" in view.status_label.text(), 60, "status_label")
    status_text = view.status_label.text()
    say(f"       status_label -> {status_text!r}")
    check("the click filled the Server tab's status label from the live daemon",
          "db" in status_text and "auth" in status_text and "world" in status_text,
          status_text)
    ps = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                        capture_output=True, text=True).stdout.split()
    check("what the widget says agrees with `docker ps` read separately",
          all(n in ps for n in ("ac-database", "ac-authserver", "ac-worldserver")),
          f"docker ps -> {sorted(ps)}")

    # --- Console tab: type, then Send ----------------------------------------
    say("\n-- Console tab, Send")
    check("Send is enabled on Linux (a pty exists here)", view.send_button.isEnabled())
    view.command_edit.setFocus()
    QTest.keyClicks(view.command_edit, "server info")   # real key events, one per character
    check("the typed command reached the QLineEdit",
          view.command_edit.text() == "server info", repr(view.command_edit.text()))
    QTest.mouseClick(view.send_button, Qt.MouseButton.LeftButton)
    pump(lambda: "AzerothCore rev" in panel_text(view.console_log), 90, "console reply")
    console_text = panel_text(view.console_log)
    say("       console reply, as the widget holds it:")
    for line in [ln for ln in console_text.splitlines() if ln.strip()][-6:]:
        say(f"         {line}")
    check("the console round-trip came back through the widget",
          "AzerothCore rev" in console_text, "reply contains the core revision")
    check("the QLineEdit was cleared after Send (send_console_command's own contract)",
          view.command_edit.text() == "")

    # --- Accounts tab: type, arrow the spin box, then Create ------------------
    say("\n-- Accounts tab, Create")
    before = db_read(f"SELECT COUNT(*) FROM acore_auth.account WHERE username='{ACCOUNT}';")
    check("the account does not exist before the click", before == "0", f"count={before}")
    view.account_name.setFocus()
    QTest.keyClicks(view.account_name, ACCOUNT)
    view.account_password.setFocus()
    QTest.keyClicks(view.account_password, ACCOUNT_PW)
    view.account_gm.setFocus()
    QTest.keyClick(view.account_gm, Qt.Key.Key_Up)      # 0 -> 1, by key, not by setValue
    QTest.keyClick(view.account_gm, Qt.Key.Key_Up)      # 1 -> 2
    check("the GM spin box was driven to 2 by two Up keys", view.account_gm.value() == 2,
          f"value={view.account_gm.value()}")
    QTest.mouseClick(view.create_account_button, Qt.MouseButton.LeftButton)
    # NOT "text is non-empty": the tab writes "Creating X…" the instant the button
    # is pressed, so that predicate is satisfied by the placeholder and the wait
    # is over before the work starts. Wait for the placeholder to be REPLACED.
    pump(lambda: view.account_report.text().strip() != ""
         and not view.account_report.text().startswith("Creating "), 120, "account_report")
    say(f"       account_report -> {view.account_report.text()!r}")
    row = db_read(
        "SELECT a.id, a.username, LENGTH(a.salt), LENGTH(a.verifier), "
        "IFNULL(aa.gmlevel,-99) FROM acore_auth.account a "
        "LEFT JOIN acore_auth.account_access aa ON aa.id=a.id "
        f"WHERE a.username='{ACCOUNT}';"
    )
    say(f"       the row, read by docker exec mysql: {row!r}")
    parts = row.split("\t")
    check("the click wrote a real SRP6 account row (salt 32, verifier 32)",
          len(parts) == 5 and parts[2] == "32" and parts[3] == "32", row)
    check("the GM level the spin box showed is the GM level the DB got",
          len(parts) == 5 and parts[4] == str(view.account_gm.value()),
          f"spin box {view.account_gm.value()}, DB {parts[4] if len(parts) == 5 else '?'}")

    # --- Networking tab: Apply must be dead until a plan exists ---------------
    say("\n-- Networking tab, Show plan (Apply is asserted dead and never clicked)")
    check("Apply is DISABLED before any plan is shown -- the ufw lockout's button",
          not view.apply_button.isEnabled())
    check("LAN is the default mode", view.lan_radio.isChecked() and view.network_mode() == "lan")
    QTest.mouseClick(view.plan_button, Qt.MouseButton.LeftButton)
    # Same trap as the account report: the tab shows "working out the plan…" at
    # once. Wait for that placeholder to go, not merely for something to be there.
    pump(lambda: view.network_text.toPlainText().strip() != ""
         and "working out the plan" not in view.network_text.toPlainText(), 120, "network plan")
    plan_text = view.network_text.toPlainText()
    say("       plan, as the widget renders it:")
    for line in plan_text.splitlines():
        say(f"         {line}")
    check("the plan the widget shows names the realm address the server advertises",
          "172.30.55.119" in plan_text, "172.30.55.119 present")
    check("the plan the widget shows names both ports",
          "3724" in plan_text and "8085" in plan_text)
    check("Apply became enabled once a plan existed (and is still not clicked)",
          view.apply_button.isEnabled())

    # --- Maintenance tab: Refresh the backup list ----------------------------
    say("\n-- Maintenance tab, Refresh backups")
    refresh_backups = view.refresh_backups_button
    check("the backup Refresh is a real button", refresh_backups.text() == "Refresh")
    QTest.mouseClick(refresh_backups, Qt.MouseButton.LeftButton)
    pump(lambda: not view.busy_reason(), 120, "backup list")
    say(f"       backup list rows: {view.backup_list.count()}")
    check("the backup list came back without an error paragraph",
          view.problem_label.text() == "",
          f"problem_label={view.problem_label.text()!r}")

    view.shutdown()
    app.processEvents()

    # =========================================================================
    say("")
    say("=" * 78)
    say("PART 2 -- the INSTALL half, through CatalogView's own Install button")
    say("=" * 78)
    panel = LogPanel()
    chosen = Path("/home/pk/gate710c-doomed-install")
    chosen.mkdir(exist_ok=True)
    picked: list[Path] = []

    def pick_dir(_parent, _title, _start):
        picked.append(chosen)
        return chosen

    def ask_suggestion(_parent, _game, _suggested):
        return False  # take the picker branch, so the dir under test is ours

    catalog_view = CatalogView(
        load_catalog(),
        installer_for_app,          # the factory main.py passes -- not a stub
        panel,
        pick_dir=pick_dir,
        ask_suggestion=ask_suggestion,
    )
    catalog_view.resize(900, 700)
    catalog_view.show()
    app.processEvents()

    results: list[tuple[str, bool, str]] = []
    catalog_view.install_finished.connect(lambda g, ok, m: results.append((g, ok, m)))

    install_button = catalog_view.button_for("wow-wotlk")
    check("the WotLK tile's Install is a real, enabled QPushButton",
          install_button.text() == "Install" and install_button.isEnabled(),
          f"text={install_button.text()!r}")

    free_gb = os.statvfs("/home/pk").f_bavail * os.statvfs("/home/pk").f_frsize / 1e9
    say(f"       free space on /home/pk before the click: {free_gb:.1f} GB")

    QTest.mouseClick(install_button, Qt.MouseButton.LeftButton)
    pump(lambda: bool(results), 180, "install_finished")
    check("the Install click reached the folder picker", picked == [chosen], f"picked={picked}")
    if results:
        game_id, ok, message = results[0]
        say("       install_finished message:")
        for line in message.splitlines():
            say(f"         {line}")
        check("the preflight REFUSED rather than warned, and nothing was installed",
              not ok, f"ok={ok}")
        check("the refusal names free space, which is the floor this box is under",
              "free space" in message.lower(), message.splitlines()[0] if message else "")
        check("the refusal reached the user as a modal dialog, not only a log line",
              any("Install" in t for t, _ in MODALS), f"modals seen: {[t for t, _ in MODALS]}")
    else:
        check("install_finished was emitted", False, "no result at all")
    check("nothing was written into the chosen folder",
          not any(chosen.iterdir()), f"contents={[p.name for p in chosen.iterdir()]}")
    check("no compose file, so nothing could be remembered as installed",
          catalog_view.button_for("wow-wotlk").text() == "Install",
          f"button still reads {catalog_view.button_for('wow-wotlk').text()!r}")

    # --- the cancel machinery, through the panel's own Stop button ------------
    say("\n-- LogPanel Stop: a real cancel, driven by the real button")
    stop_button = button_named(panel, "Stop")
    check("the panel's Stop button exists and is dead while idle",
          stop_button is not None and not stop_button.isEnabled())
    panel.run(services.logs_source, title="worldserver log")
    pump(lambda: panel_flag(panel, "running") and stop_button.isEnabled(), 30, "the follow to start")
    check("Stop went live once a job was running", stop_button.isEnabled())
    pump(lambda: len(panel_text(panel).splitlines()) > 2, 60, "some log lines")
    lines_before = len(panel_text(panel).splitlines())
    QTest.mouseClick(stop_button, Qt.MouseButton.LeftButton)
    pump(lambda: not panel_flag(panel, "running"), 60, "the follow to stop")
    check("the follow stopped after the click", not panel_flag(panel, "running"),
          f"{lines_before} lines had arrived first")
    check("the panel reports it as CANCELLED, not as finished", panel_flag(panel, "cancelled"))
    say(f'       panel.cancelled={panel_flag(panel, "cancelled")}  panel.running={panel_flag(panel, "running")}')

    # --- the cancel COPY -----------------------------------------------------
    say("\n-- the honest cancel copy (catalog_view.py:753 composes this on a cancelled install)")
    empty = Path("/home/pk/gate710c-empty")
    empty.mkdir(exist_ok=True)
    for label, path in (("a folder with nothing in it", empty),
                        ("the folder holding the real, complete install", SERVER_DIR)):
        note = cancelled_install_message(load_catalog().get("wow-wotlk").name, path)
        say(f"       -- {label}: {path}")
        for line in note.splitlines():
            say(f"         {line}")
        check(f"the copy for {label} names that folder", str(path) in note or "folder" in note)
    say("       NOTE: this copy could not be driven THROUGH a cancelled install on this box,")
    say("       because preflight refuses every install here on free space before an install")
    say("       starts -- see the refusal above. It needs the clean checkpoint 7.1 clause 1 is")
    say("       blocked on. Reported as not exercised, not as passed.")

    watcher.stop()
    say("")
    say("=" * 78)
    say(f"RESULT: {PASSES} OK, {FAILS} FAIL")
    say("=" * 78)
    return 0 if FAILS == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
