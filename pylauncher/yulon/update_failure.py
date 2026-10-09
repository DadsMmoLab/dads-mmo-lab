"""The plain sentence for a world server that stopped at an update it could not apply (T600).

tortoise-wow's start-time updater (`AutoUpdater.cpp`) runs each migration file in a
transaction. A file whose statement fails is rolled back, the core logs

    [DB Auto-Updater] Migration <name> with hash <hash> failed to apply.

and the server ends (`DB AutoUpdater FAILED, cancelling server.`). Before this app's
patch of the core the world instead sat in a read of its console, so that second line
was never printed and the first was all there was (`catalog/installers/wow-tortoise/
patches/updater-no-wait-for-enter.patch`). Either way the log says which file and,
a few lines up, what MariaDB answered, and this module reads both.

**What the sentence does not say.** It never says the database is unchanged: MariaDB
commits implicitly at DDL (ALTER, CREATE, DROP), so a file whose ALTER ran and whose
later INSERT failed leaves the ALTER applied, and the next start fails differently.

Pure and text-only, so the Server tab, the install's waits and the tests all read one
function.
"""

from __future__ import annotations

import re

FAILED = re.compile(r"Migration (\S+) with hash (\S+) failed to apply\.")
"""The core's line for the file that failed (`AutoUpdater.cpp:236`)."""

CLOSED = re.compile(r"DB AutoUpdater FAILED")
"""What the core prints when it then gives up (`World.cpp:1950`); absent while it is stuck."""

ATTEMPT = re.compile(
    r"Attempting to execute update (\S+?)(?: for module (\S+?))?, hash (\S+?)\.?\s*$"
)
"""The line before each file runs, the only one that names the module (`AutoUpdater.cpp:285`)."""

MARIADB = re.compile(r"^\[(\d{3,5})\] (.+)$")
"""MariaDB's own answer as the core logs it: `[1062] Duplicate entry '1' for key 'PRIMARY'`."""

PROBE = "[DB Auto-Updater] Migration 0_world with hash 0 failed to apply."
"""A line `explain()` reads as a failure: how a catalog's `ready.fatal` is asked if it covers it."""

OPENING = "The world server stopped"
"""How every sentence `explain()` returns begins: tells one from a quoted log line."""

_TAIL = "It will not come up until that update applies or is removed."


def explain(log: str) -> str:
    """The sentence for the first failed update in `log`, or `""` when there is none.

    Names the update's file (`<name>.sql`) and its module (the core when none), and quotes
    the last MariaDB error between that file's `Attempting` line and its failure line. Falls
    back to pointing at the world log when the error line was not in the text read.
    """
    lines = [line.strip() for line in log.splitlines()]
    for at, text in enumerate(lines):
        failed = FAILED.search(text)
        if failed is None:
            continue
        name, digest = failed.group(1), failed.group(2)
        module, error = "", ""
        for before in reversed(lines[:at]):
            attempt = ATTEMPT.search(before)
            if attempt is not None and attempt.group(1) == name and attempt.group(3) == digest:
                module = attempt.group(2) or ""
                break
            if not error:
                reading = MARIADB.search(before)
                if reading is not None:
                    error = f"[{reading.group(1)}] {reading.group(2)}"
            if FAILED.search(before):
                break
        where = f"the {module} module" if module else "the core"
        head = f"{OPENING} at a database update it could not apply: {name}.sql ({where})"
        if error:
            return f"{head}. MariaDB said {error}. {_TAIL}"
        return f"{head}. MariaDB's message is in the world log. {_TAIL}"
    if any(CLOSED.search(text) for text in lines):
        return (
            f"{OPENING} because a database update failed to apply. The file and "
            f"MariaDB's message are in the world log. {_TAIL}"
        )
    return ""
