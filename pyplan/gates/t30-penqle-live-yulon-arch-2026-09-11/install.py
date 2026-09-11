"""T30 Half 2 live: Fresh Tortoise install into ~/tortoise-penqle.

installer_for_app(entry).run() is the engine behind the tab's Install button,
run headless off hand-t30 at bed545b8. Named deviation: the GUI dialog was
bypassed; the engine and its validation are the app's own code.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/pk/y8-t30/pylauncher")

from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.install_wiring import installer_for_app
from yulon.log import configure, use_utf8_streams

use_utf8_streams()
configure()

GAME = "wow-tortoise"
SERVER_DIR = Path("/home/pk/tortoise-penqle")
CLIENT_DIR = Path("/home/pk/clients/TurtleWoW")


def stamp(line: str) -> None:
    now = time.strftime("%H:%M:%SZ", time.gmtime())
    sys.stdout.write("[" + now + "] " + line + "\n")
    sys.stdout.flush()


def ask(prompt: str) -> str:
    low = prompt.lower()
    if "password" in low:
        stamp(f"ASKED (password): {prompt!r} -> answered with the VM default account password")
        return "yulon"
    stamp(f"ASKED: {prompt!r} -> answered y")
    return "y"


entry = load_catalog().get(GAME)
for s in entry.emulator.sources:
    stamp(f"source {s.repo} branch={s.branch} rev={s.rev} -> {s.dest}")
stamp(f"channel={entry.operations.channel}")
stamp(f"server_dir={SERVER_DIR} client_dir={CLIENT_DIR} (exists={CLIENT_DIR.is_dir()})")

engine = installer_for_app(entry)
try:
    for line in engine.run(InstallOptions(server_dir=SERVER_DIR, client_dir=CLIENT_DIR), ask=ask):
        stamp(line)
except InstallerError as exc:
    stamp(f"INSTALL FAILED: {exc}")
    raise SystemExit(1) from exc

stamp("INSTALL RETURNED CLEANLY")
