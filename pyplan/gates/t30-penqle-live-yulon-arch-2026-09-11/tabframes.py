"""T30 Half 2 live: Launch the app, capture frames of each sub-tab.

Uses Qt's own widget hierarchy to switch tabs programmatically and
take screenshots via QScreen.grabWindow(). No coordinate guessing.
"""
import os, sys, time
from pathlib import Path

os.environ["DISPLAY"] = ":0"
os.environ["XAUTHORITY"] = "/home/pk/.Xauthority"

sys.path.insert(0, "/home/pk/y8-t30/pylauncher")

from yulon.log import configure, use_utf8_streams
use_utf8_streams()
configure()

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

app = QApplication(sys.argv)

from main import build_window
window = build_window()
window.show()

OUT = Path("/home/pk/t30h2-live")
TABS_TO_CAPTURE = ["Server", "Modules", "Accounts", "Networking"]
captured = []

def capture_tabs():
    tabs_widget = window.property("tabs")
    if tabs_widget is None:
        print("ERROR: no tabs widget found")
        app.quit()
        return

    # Find the controller tab (second main tab after Catalog)
    controller_idx = -1
    for i in range(tabs_widget.count()):
        text = tabs_widget.tabText(i)
        if "tortoise" in text.lower() or "WoW" in text:
            controller_idx = i
            break

    if controller_idx == -1:
        print("ERROR: no controller tab found")
        for i in range(tabs_widget.count()):
            print(f"  tab {i}: {tabs_widget.tabText(i)}")
        app.quit()
        return

    tabs_widget.setCurrentIndex(controller_idx)
    controller_view = tabs_widget.widget(controller_idx)
    app.processEvents()
    time.sleep(0.3)

    # Find sub-tabs within the controller view
    from PySide6.QtWidgets import QTabWidget
    sub_tabs = controller_view.findChild(QTabWidget)
    if sub_tabs is None:
        print("ERROR: no sub-tabs found in controller view")
        app.quit()
        return

    print(f"Found {sub_tabs.count()} sub-tabs:")
    for i in range(sub_tabs.count()):
        print(f"  {i}: {sub_tabs.tabText(i)}")

    screen = app.primaryScreen()
    frame_num = 1
    for tab_name in TABS_TO_CAPTURE:
        found = False
        for i in range(sub_tabs.count()):
            if sub_tabs.tabText(i) == tab_name:
                sub_tabs.setCurrentIndex(i)
                app.processEvents()
                time.sleep(0.5)
                app.processEvents()

                # Grab window screenshot
                pixmap = screen.grabWindow(window.winId())
                fname = f"frame-{frame_num:02d}-{tab_name.lower()}-tab.png"
                fpath = OUT / fname
                pixmap.save(str(fpath))
                print(f"captured {fname}")
                captured.append(fname)
                frame_num += 1
                found = True
                break
        if not found:
            print(f"WARNING: tab '{tab_name}' not found")

    print(f"\nCaptured {len(captured)} frames")
    app.quit()

QTimer.singleShot(2000, capture_tabs)
sys.exit(app.exec())
