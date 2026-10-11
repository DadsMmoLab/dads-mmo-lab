"""What may not be cut off, and what is said when it is running (T690).

A server tab is torn down by several doors: the window's close, the tray's Quit,
the self-update's restart, a changed WSL distro, a changed client folder, a made
or deleted ready-to-play client, and "Remove from Yu'lon". Each used to ask its
own, shorter question, and a Restore, a backup or a Start/Stop/Restart went
unseen by most of them; a tab torn down in the middle of one blocked the window
for minutes and then left the job half done.

`ControllerView.teardown_work()` is the ONE list of what must not be cut off,
and it answers with one of the kinds below. Every door asks it through
`ControllerView.teardown_refusal()` (or `forget_refusal()`, which words the same
answer for a removal). This module only holds the kinds and the plain-words
sentence for each, Qt-free so a test can read every sentence without a widget.

A door REFUSES, and does not ask "stop it anyway?" -- except the doors that end Yu'lon
(`yulon.ui.quit_anyway`): a refusal with no way past it would make a flag stuck by a bug
unquittable, so a refused quit or close also offers "Quit anyway", with its cost in plain
words. The client-folder doors keep the plain refusal; a player can always wait there.
A Stop may be quit over (`STOP_JOB`). A restore stopped half-way
leaves the databases half-written, and the jobs here cannot be stopped from the
dialog anyway; `busy_reason()`'s import, reset and uninstall already refuse in
the same way, so a player meets one behaviour for "Yu'lon is still working".
"""

from __future__ import annotations

UPDATE_BACKUP = "update_backup"
BACKUP = "backup"
RESTORE = "restore"
MOVE = "move"
MODULE = "module"
NETWORK = "network"
TUNING = "tuning"
PANEL = "panel"
ACTION = "action"

STOP_JOB = "Stop"
"""The Server tab's label for a Stop, the one action a quit may leave running behind it.

A Stop carries on in Docker after Yu'lon quits (the tray's own "Quit now" box says so),
and T158's `_stop_abandon` ends a stop waiting for a world to load. Start, Restart and
the rest are cut off by a quit, so they refuse it.
"""

KINDS: tuple[str, ...] = (
    UPDATE_BACKUP,
    BACKUP,
    RESTORE,
    MOVE,
    MODULE,
    NETWORK,
    TUNING,
    PANEL,
    ACTION,
)
"""Every kind `ControllerView.teardown_work()` can answer, in the order it checks them."""


class Refusal(str):
    """A refusal's sentence that also knows which kind of work it names (T690).

    A `str`, so every caller that shows or compares it is unchanged; "Quit anyway" reads
    `kind` to say what quitting leaves behind (`CARRIES_ON`). A sentence that carries no
    kind (`busy_reason()`'s, the Catalog's) is the cautious "cut off" one.
    """

    kind: str | None

    def __new__(cls, text: str, kind: str | None = None) -> Refusal:
        made = super().__new__(cls, text)
        made.kind = kind
        return made


CARRIES_ON: frozenset[str] = frozenset({RESTORE, UPDATE_BACKUP, MOVE})
"""Work whose `docker exec` child outlives Yu'lon: Quit anyway does not stop it.

The restore's load is recorded in the restore marker (`load_pid`), so the next start
knows it still runs; the others are named in the box ("do not start Yu'lon again until
it has finished"). Everything else is cut off by the exit.
"""

_WAIT = "Wait for it to finish, then try again."

_SENTENCES: dict[str, str] = {
    UPDATE_BACKUP: (
        "Yu'lon is backing this server up before an update. Stopping now would leave a "
        f"half-made backup. {_WAIT}"
    ),
    BACKUP: (
        "A backup of this server is running on its Maintenance tab. Stopping it now "
        f"would lose the backup. {_WAIT}"
    ),
    RESTORE: (
        "A restore is writing into this server's databases on its Maintenance tab. "
        f"Stopping it now would leave them half-written. {_WAIT}"
    ),
    MOVE: (
        "Accounts and characters are being packed or brought in on this server's "
        "Maintenance tab. Stopping now would leave its databases half-written. "
        f"{_WAIT}"
    ),
    NETWORK: (
        "A network change is being applied on this server's Networking tab. Stopping "
        f"it now would leave the settings half-changed. {_WAIT}"
    ),
    TUNING: (
        "A Tuning change is being saved or put back on this server's Tuning tab. "
        f"Stopping it now would leave a settings file half-written. {_WAIT}"
    ),
    PANEL: (
        "A rebuild, a database update or an adopt is running on this server's Modules "
        f"tab. Stopping it now would leave the server half-built. {_WAIT}"
    ),
}


def sentence(kind: str, *, module_job: str = "", server_job: str = "") -> Refusal:
    """The plain-words reason for `kind`, naming what is running where it can.

    `module_job` is the Modules tab's own name for its job ("install mod-ah-bot");
    `server_job` is the Server tab's label for the action ("Start", "Restart").
    """
    if kind == MODULE:
        what = module_job or "a Modules tab action"
        return Refusal(
            f'"{what}" is running on this server\'s Modules tab. Stopping it now '
            f"could leave the module half-installed. {_WAIT}",
            kind,
        )
    if kind == ACTION:
        what = server_job or "Another action"
        return Refusal(
            f"{what} is running on this server's Server tab. Stopping it now would cut "
            f"it off half-way. {_WAIT}",
            kind,
        )
    return Refusal(_SENTENCES[kind], kind)


CATALOG_INSTALL = (
    "An install is running on the Catalog tab. Closing now would cancel it half-way. "
    "Wait for it to finish, or press Stop in its log first, then try again."
)
"""The window-level reason: a Catalog install belongs to no server tab."""
