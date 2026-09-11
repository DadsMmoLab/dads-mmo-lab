import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from PySide6 import __version__ as pv
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
SB = QMessageBox.StandardButton
def click(which):
    w = app.activeModalWidget()
    if isinstance(w, QMessageBox):
        w.button(which).click()
    else:
        QTimer.singleShot(20, lambda: click(which))
for which in (SB.Yes, SB.No):
    QTimer.singleShot(20, lambda w=which: click(w))
    r = QMessageBox.question(None, "t", "t", SB.Yes | SB.No, SB.Yes)
    print(f"static question(), clicked {which.name}: returned {r!r} type={type(r).__name__} is_Yes={r is SB.Yes} is_not_Yes={r is not SB.Yes} eq_Yes={r == SB.Yes}", flush=True)
print(f"py {sys.version.split()[0]} PySide6 {pv}", flush=True)
