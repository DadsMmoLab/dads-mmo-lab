"""What removing a server from Yu'lon says, and the facts it is said from (T95).

Removing is not uninstalling (owner decision 1, 2026-09-23). The folder, the
database volume, the images and the (stopped) containers all stay. What goes is
Yu'lon's record of the server: its tab, its `state.json` row, and the Catalog
tile's "Installed".

Qt-free for `dashboard.line()`'s reason. Every sentence a person reads before
letting go of a server is asserted here without a widget, and `main.py` only
chooses which one to show.

How to bring a server back depends on the install type, and a sentence that is
true of one type is false of another:

* On this host, built by Yu'lon or adopted: "Use existing…" on the Catalog
  tile, pointed at the same folder. The m910q gate brought a removed TBC
  server back that way (T95, 2026-09-24). That is the way back this dialog
  offers, because it works for both. Installing into the same folder again
  also brings back a server Yu'lon BUILT -- the install resumes from its
  record, and since T112 its preflight no longer refuses the disk space the
  resume will not spend. T163/T164 sent players down that route to get
  compose files or images made again; since T170 one press does each
  (Repair server files…, Rebuild the server…). An adopted folder, with no
  record, is refused by the install outright, so this dialog does not offer
  that route.
* In a WSL distro: "Find in WSL…".
* Folder gone: nothing to bring back, and T34's promise about Docker, word
  for word.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

BUTTON_LABEL = "Remove from Yu'lon…"
"""The tab menu's entry and the Server tab's button (`controller_view.REMOVE_FROM_YULON`)."""

TITLE = "Remove this server from Yu'lon?"
REFUSED_TITLE = "This server cannot be removed yet"
STOP_FAILED_TITLE = "The server could not be stopped"
SAVE_FAILED_TITLE = "Yu'lon could not forget this server"

# Why a removal is refused, one sentence per kind of job (`ControllerView.forget_refusal()`).
# Removing drops the tab, and dropping it joins that tab's jobs for a bounded
# time only; a restore stopped half-way leaves its databases half-written.

UPDATE_BACKUP_RUNNING = (
    "This server is being backed up before an update. Wait for the backup to finish, "
    "then try again. Nothing was removed."
)
PANEL_RUNNING = (
    "A rebuild, a database update or an adopt is running on this server's Modules tab. "
    "Wait for it to finish, then try again. Nothing was removed."
)
SERVER_ACTION_RUNNING = (
    "Another action is running on this server's tab. Wait for it to finish on the "
    "Server tab, then try again. Nothing was removed."
)
BACKUP_RUNNING = (
    "A backup of this server is running on its Maintenance tab. Wait for it to finish, "
    "then try again. Nothing was removed."
)
RESTORE_RUNNING = (
    "A restore is writing into this server's databases on its Maintenance tab, and "
    "stopping the server now would leave them half-written. Wait for it to finish, then "
    "try again. Nothing was removed."
)
# Why a Start, Stop, Restart or recreate is refused while the Maintenance tab holds the
# server (`docker.hold_the_server()`, T216). Siblings of the two above: the same jobs, asked
# about by a different press, so they say "nothing was started or stopped" instead.
RESTORE_HOLDS_THE_SERVER = (
    "A restore is writing into this server's databases on its Maintenance tab. Starting "
    "the server now would let the world save over what is being restored, and stopping it "
    "would leave the databases half-written. Wait for the restore to finish, then try "
    "again. Nothing was started or stopped."
)
BACKUP_HOLDS_THE_SERVER = (
    "A backup is running on this server's Maintenance tab with the database started on its "
    "own for it, and the database is stopped again when the backup ends. Wait for the "
    "backup to finish, then try again. Nothing was started or stopped."
)
# What a Backup or Restore refused by `docker.maintenance_lease()` is told: the running job's
# own sentence, to which the refused one adds what it did not do (T216 review round 3).
BACKUP_HOLDS_THE_DATABASES = (
    "A backup of this server is running on its Maintenance tab. Wait for it to finish, "
    "then try again."
)
RESTORE_HOLDS_THE_DATABASES = (
    "A restore is writing into this server's databases on its Maintenance tab. Wait for "
    "it to finish, then try again."
)
NETWORK_RUNNING = (
    "A network change is being applied on this server's Networking tab. Wait for it to "
    "finish, then try again. Nothing was removed."
)


# T568: what a press refused by another Yu'lon's reservation of the server says
# (`docker.server_claim()`). Each leads with who holds it and ends with what to do.
PRESS_START = "Start"
PRESS_STOP = "Stop"
"""The two press names the reservation's own lifecycle commands carry; the sentence below
says "starting" and "stopping" for them and quotes any other press by name."""


def _doing(press: str, label: str) -> str:
    if press == PRESS_START:
        return f"is starting {label} right now"
    if press == PRESS_STOP:
        return f"is stopping {label} right now"
    return f"is working on {label} right now: \u201c{press or 'a job'}\u201d"


def server_busy_elsewhere(
    label: str, press: str, since: str, who: str, this_press: str, *, anyway: bool = False
) -> str:
    """The refusal when another Yu'lon's reservation holds the server (T568 section 6).

    `since` is what the daemon's own creation stamp reads as ("14:02 (3 minutes ago)"), or
    empty when it could not be read; `who` is "user@host (OS)". `anyway` is the Stop's own
    question: it asks instead of refusing, and says what stopping now ends.
    """
    started = f", started {since}" if since else ""
    by = f" by {who}" if who else ""
    if anyway:
        return (
            f"Another Yu'lon {_doing(press, label)}{started}{by}. Stopping now ends that too, "
            "wherever it is. A database file it is running may be left part-done, and that "
            "Yu'lon will say what it left."
        )
    return (
        f"Another Yu'lon {_doing(press, label)}{started}{by}. Nothing was changed. Wait "
        f"for it to finish, then press \u201c{this_press}\u201d again."
    )


def server_reservation_left(label: str, name: str, this_press: str) -> str:
    """This user's own reservation, left in Docker by a crash Docker kept (T568 section 6)."""
    return (
        f"An earlier run of this Yu'lon left its reservation of {label} in Docker ({name}), "
        f"so nothing was changed. Remove it with the command below, then press "
        f"\u201c{this_press}\u201d again.\ndocker rm -f {name}"
    )


def server_reservation_unsaid(label: str, name: str, this_press: str) -> str:
    """Docker refused the name and then would not say whose it is (T543's wording, per server)."""
    return (
        f"{label} is reserved in Docker ({name}), and Docker would not say by whom. Nothing "
        "was changed. Wait for any other Yu'lon's job on it to finish, then press "
        f"\u201c{this_press}\u201d again."
    )


def server_reservation_unavailable(label: str, said: str) -> str:
    """No reservation could be made at all: no Docker, no image, a daemon that would not answer."""
    return f"Yu'lon could not reserve {label} in Docker. {said} Nothing was changed."


def module_running(what: str) -> str:
    """The Modules tab's job, by the name its own report line gives it ("install mod-ah-bot")."""
    return (
        f'"{what}" is running on this server\'s Modules tab. Wait for it to finish, then try '
        "again. Nothing was removed."
    )


_FOLDER_GONE = (
    "{server_dir} no longer exists. Any Docker containers, volumes or images named for it are "
    "NOT touched, because without the folder Yu'lon cannot prove which ones were its own; "
    "remove those from Docker yourself if they remain."
)


@dataclass(frozen=True)
class Facts:
    """Everything the question depends on, read once, at the press."""

    name: str
    server_dir: Path
    wsl_distro: str | None
    folder_gone: bool
    running: bool | None
    """What the last status poll saw: True running, False stopped, None unknown.

    Words only. A removal stops every server whose folder exists whatever this
    says, because a poll's "stopped" is trusted until the next poll begins and
    a server started outside Yu'lon in that gap would be forgotten while it ran
    (Codex, final review, T95)."""
    play_client_dir: Path | None = None
    """This server's ready-to-play client (T181), named because it is left behind too."""


def way_back(facts: Facts) -> str:
    """How to bring this server back, and only what is true for this install type."""
    if facts.wsl_distro is not None:
        return (
            f'To bring it back: press "Find in WSL…" on the {facts.name} tile in the Catalog '
            f"and pick it in {facts.wsl_distro}."
        )
    return (
        f'To bring it back: press "Use existing…" on the {facts.name} tile in the Catalog and '
        "pick this folder."
    )


def question(facts: Facts) -> str:
    """The one Yes/No a removal asks: what goes, what stays, the stop, the way back."""
    goes = f"Yu'lon stops listing {facts.name} at {facts.server_dir}, and its tab closes."
    play = (
        f"Its ready-to-play client at {facts.play_client_dir} is left where it is too; "
        "delete that folder by hand if you no longer want it."
        if facts.play_client_dir is not None
        else None
    )
    if facts.folder_gone:
        gone = (goes, _FOLDER_GONE.format(server_dir=facts.server_dir))
        return "\n\n".join((*gone, play) if play is not None else gone)
    parts = [
        goes,
        "Nothing is deleted: the server folder, its database volume (your characters) and its "
        "Docker images all stay where they are.",
    ]
    if play is not None:
        parts.append(play)
    parts.append(
        "It is running, so it is stopped first. Its containers are kept."
        if facts.running
        else "If it is running, it is stopped first. Its containers are kept."
    )
    parts.append(way_back(facts))
    return "\n\n".join(parts)


def stop_failed_question(name: str, why: str) -> str:
    """The second question, asked only when the stop before a removal failed. Default No."""
    return (
        f"{name} could not be stopped: {why}\n\nRemove it from Yu'lon anyway? If it is still "
        "running, it keeps running with nothing in Yu'lon managing it, and you would have to "
        "stop it from Docker yourself."
    )


def save_failed(server_dir: Path, exc: OSError) -> str:
    """Why the tab is still there: the record could not be written, so it was put back."""
    return (
        f"{server_dir} was not forgotten: state.json could not be written ({exc}). "
        "Its tab stays open."
    )
