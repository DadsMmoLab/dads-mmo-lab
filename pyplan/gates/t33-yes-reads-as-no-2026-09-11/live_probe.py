"""T33 reproduction on a real box: the app's own `_qt_suggestion_asker`, a QTimer pressing Yes."""
import os, sys, subprocess
from pathlib import Path
sys.path.insert(0, sys.argv[1])  # <tree>/pylauncher
from PySide6 import __version__ as pv
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from yulon.ui.catalog_view import _qt_suggestion_asker
app = QApplication([])
SB = QMessageBox.StandardButton
def click(which, tries=[0]):
    w = app.activeModalWidget()
    if isinstance(w, QMessageBox):
        w.button(which).click()
    elif tries[0] < 200:
        tries[0] += 1; QTimer.singleShot(25, lambda: click(which))
    else:
        print("no dialog appeared", flush=True); app.quit()
sha = subprocess.run(["git", "-C", sys.argv[1], "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
for which in (SB.Yes, SB.No):
    QTimer.singleShot(25, lambda w=which: click(w))
    got = _qt_suggestion_asker(None, "WoW WotLK", Path.home() / "wow-server-playerbots")
    print(f"{os.uname().nodename} py{sys.version.split()[0]} PySide6 {pv} tree {sha} platform={app.platformName()}: pressed {which.name} -> asker returned {got}", flush=True)
