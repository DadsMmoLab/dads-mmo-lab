"""Drive a wow-tbc install on m910q for the 7.4c interrupted-import gate.

The gate asks for `import` -> `partial` -> reset -> re-run, and every part of
that has to start from a genuinely half-written database. The honest way to get
one is to interrupt a real import rather than to hand-carve a state that looks
like the aftermath of one -- a carved state proves the probe can read what we
wrote, which is not the question.

So this is an ordinary install into an empty folder. The watcher beside it
(`watch-74c.py`) kills this process once the import is provably underway.
"""

import sys
from pathlib import Path

sys.path.insert(0, "/home/pk/yulon-74c/pylauncher")

from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError
from yulon.catalog.installer import InstallOptions
from yulon.install_wiring import _terminal_prompter, installer_for_app
from yulon.log import get_logger, use_utf8_streams

use_utf8_streams()
logger = get_logger(__name__)

game = "wow-tbc"
server_dir = Path("/home/pk/tbc-7.4c")
client_dir = Path("/home/pk/clients/WoW-Client-2.4.3")

entry = load_catalog().get(game)
options = InstallOptions(server_dir=server_dir, client_dir=client_dir)
engine = installer_for_app(entry)
try:
    for line in engine.run(options, ask=_terminal_prompter):
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
except InstallerError as exc:
    logger.error(f"install failed: {exc}")
    sys.stderr.write(f"install failed: {exc}\n")
    raise SystemExit(1) from exc
print("INSTALL RETURNED CLEANLY")
