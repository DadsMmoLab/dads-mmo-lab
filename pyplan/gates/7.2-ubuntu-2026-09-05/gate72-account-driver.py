"""7.1 clause 13: make a game account through the app's OWN seam.

Nothing here types at the worldserver console. The account is written by
`ControllerServices.create_account` -- the exact callable the Server tab's
Accounts tile invokes (`ControllerView.create_account`, controller_view.py:1448
`self.services.create_account(name, password, gm_level)`), wired by
`ControllerServices.for_entry(entry, SERVER_DIR)`, which is what `main.py`
builds when a real install is opened.

The row is then read back by a DIFFERENT route (docker exec mysql) so the proof
does not come from the same object that made the claim.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/pk/gate72/pylauncher")

from yulon.catalog.catalog import load_catalog  # noqa: E402
from yulon.ui.controller_view import ControllerServices  # noqa: E402

SERVER_DIR = Path("/home/pk/wowserver")
USER = "yulon"
PASSWORD = "yulon1"
GM = 3

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")


def say(text: str = "") -> None:
    print(text, flush=True)


def mysql(sql: str) -> None:
    """Read the database back by a route that is not the app."""
    proc = subprocess.run(
        ["docker", "exec", "ac-database", "mysql", "-uroot", "-ppassword", "-N", "-B", "-e", sql],
        capture_output=True,
        text=True,
    )
    say(f"$ docker exec ac-database mysql -e {sql!r}")
    say(f"exit {proc.returncode}")
    say(proc.stdout.rstrip())
    if proc.stderr.strip():
        say(proc.stderr.rstrip())
    say()


entry = load_catalog().get("wow-wotlk")
say(f"catalog entry: {entry.id} / {entry.name}")
services = ControllerServices.for_entry(entry, SERVER_DIR)
say(f"services: {type(services).__name__} from ControllerServices.for_entry -- the GUI's own object")
say(f"services.create_account -> {services.create_account!r}")
say()

say("=== THE CALL: services.create_account('yulon', <password>, 3)")
result = services.create_account(USER, PASSWORD, GM)
say(f"AccountResult: {result!r}")
say()

say("=== the row, read by a different route")
mysql("SELECT id, username FROM acore_auth.account WHERE username='YULON';")
mysql("SELECT id, username FROM acore_auth.account ORDER BY id DESC LIMIT 5;")
mysql("SELECT COUNT(*) FROM acore_auth.account;")
mysql("SELECT id, gmlevel, RealmID FROM acore_auth.account_access WHERE id=(SELECT id FROM acore_auth.account WHERE username='YULON');")

say("=== the SAME call again: convergence, not a second account")
again = services.create_account(USER, PASSWORD, GM)
say(f"AccountResult: {again!r}")
mysql("SELECT COUNT(*) FROM acore_auth.account WHERE username='YULON';")
