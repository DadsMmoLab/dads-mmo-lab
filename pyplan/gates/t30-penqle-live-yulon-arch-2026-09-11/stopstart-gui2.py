"""T30 Half 2 live: Stop and Start through the app on :0.

Longer wait for the controller to poll, then press the buttons.
"""
import os, sys, time, subprocess
from pathlib import Path
os.environ["DISPLAY"] = ":0"
os.environ["XAUTHORITY"] = "/home/pk/.Xauthority"
sys.path.insert(0, "/home/pk/y8-t30/pylauncher")
from yulon.log import configure, use_utf8_streams
use_utf8_streams()
configure()
from yulon.state import KnownInstall, load_state, save_state

state = load_state()
state.remember(KnownInstall(
    game="wow-tortoise", server_dir=Path("/home/pk/tortoise-penqle"),
    client_dir=Path("/home/pk/clients/TurtleWoW")))
save_state(state)

from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtCore import QTimer
app = QApplication(sys.argv)
from main import build_window
window = build_window()
window.show()
OUT = Path("/home/pk/t30h2-live")

step = [0]

def poll():
    app.processEvents()
    step[0] += 1
    tabs = window.property("tabs")
    
    if step[0] < 10:
        # First 10 seconds: let the controller settle
        return
    
    # Find controller tab for tortoise-penqle
    ctrl_idx = -1
    for i in range(tabs.count()):
        t = tabs.tabText(i)
        if "penqle" in t.lower():
            ctrl_idx = i
            break
    if ctrl_idx == -1:
        for i in range(tabs.count() - 1, 0, -1):
            if tabs.tabText(i) != "Catalog":
                ctrl_idx = i
                break
    tabs.setCurrentIndex(ctrl_idx)
    view = tabs.widget(ctrl_idx)
    app.processEvents()
    
    screen = app.primaryScreen()
    
    if step[0] == 10:
        # Frame the running server
        pixmap = screen.grabWindow(window.winId())
        pixmap.save(str(OUT / "frame-05-server-running.png"))
        print(f"step {step[0]}: frame-05 saved (server running)")
        # List all buttons and their state
        for btn in view.findChildren(QPushButton):
            print(f"  button: {btn.text()!r} enabled={btn.isEnabled()} name={btn.objectName()}")
    
    if step[0] == 12:
        # Click Stop
        for btn in view.findChildren(QPushButton):
            if btn.text() == "Stop" and btn.isEnabled():
                print(f"step {step[0]}: clicking Stop")
                btn.click()
                break
        else:
            print(f"step {step[0]}: Stop not enabled, trying anyway")
            for btn in view.findChildren(QPushButton):
                if btn.text() == "Stop":
                    btn.setEnabled(True)
                    btn.click()
                    break
    
    if step[0] == 30:
        # Frame the stopped server
        pixmap = screen.grabWindow(window.winId())
        pixmap.save(str(OUT / "frame-06-server-stopped.png"))
        print(f"step {step[0]}: frame-06 saved (server stopped)")
        # Click Start
        for btn in view.findChildren(QPushButton):
            if btn.text() == "Start":
                print(f"step {step[0]}: clicking Start")
                btn.click()
                break
    
    if step[0] == 90:
        # Frame the restarted server
        pixmap = screen.grabWindow(window.winId())
        pixmap.save(str(OUT / "frame-07-server-restarted.png"))
        print(f"step {step[0]}: frame-07 saved (server restarted)")
    
    if step[0] >= 95:
        app.quit()

timer = QTimer()
timer.timeout.connect(poll)
timer.start(1000)
sys.exit(app.exec())
