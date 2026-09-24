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
  server back that way (T95, 2026-09-24). Installing into the same folder
  again is NOT offered: the gate found the install preflight's disk check
  refusing it before the installer ever read its resume record (T112), and an
  adopted folder, with no record, is refused outright.
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
NETWORK_RUNNING = (
    "A network change is being applied on this server's Networking tab. Wait for it to "
    "finish, then try again. Nothing was removed."
)


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
    stop_first: bool


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
    goes = (
        f"Yu'lon stops listing {facts.name} at {facts.server_dir}: its tab closes and the "
        "Catalog forgets it."
    )
    if facts.folder_gone:
        return "\n\n".join((goes, _FOLDER_GONE.format(server_dir=facts.server_dir)))
    parts = [
        goes,
        "Nothing is deleted: the server folder, its database volume (your characters) and its "
        "Docker images all stay where they are.",
    ]
    if facts.stop_first:
        parts.append(
            "It is running, or Yu'lon could not tell, so it is stopped first. Its containers are "
            "kept."
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
