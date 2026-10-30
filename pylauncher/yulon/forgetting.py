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

from yulon import server_build_presses
from yulon.server_build_presses import MAINTENANCE_TAB

BUTTON_LABEL = "Remove from Yu'lon…"
"""The tab menu's entry (`controller_view.REMOVE_FROM_YULON`)."""

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
    "A rebuild, a database update or an adopt is running on this server's "
    f"{MAINTENANCE_TAB} tab. Wait for it to finish, then try again. Nothing was removed."
)
SERVER_ACTION_RUNNING = (
    "Another action is running on this server's tab. Wait for it to finish on the "
    "Server tab, then try again. Nothing was removed."
)
BACKUP_RUNNING = (
    f"A backup of this server is running on its {MAINTENANCE_TAB} tab. Wait for it to "
    "finish, then try again. Nothing was removed."
)
RESTORE_RUNNING = (
    f"A restore is writing into this server's databases on its {MAINTENANCE_TAB} tab, and "
    "stopping the server now would leave them half-written. Wait for it to finish, then "
    "try again. Nothing was removed."
)
# Why a Start, Stop, Restart or recreate is refused while the Maintenance tab holds the
# server (`docker.hold_the_server()`, T216). Siblings of the two above: the same jobs, asked
# about by a different press, so they say "nothing was started or stopped" instead.
RESTORE_HOLDS_THE_SERVER = (
    f"A restore is writing into this server's databases on its {MAINTENANCE_TAB} tab. "
    "Starting the server now would let the world save over what is being restored, and "
    "stopping it would leave the databases half-written. Wait for the restore to finish, "
    "then try again. Nothing was started or stopped."
)
# T737: what a Pack or Bring in is told while that restore holds the server (`move_flows`'s
# `move_held_reason()`). The sentence above is about starting and stopping, which a move asks
# neither of; a move's own reason is what it would read or write.
RESTORE_HOLDS_A_MOVE = (
    f"A restore is writing into this server's databases on its {MAINTENANCE_TAB} tab. "
    "Packing them now would take a half-restored copy, and bringing a move in would write "
    "over what is being restored. Wait for the restore to finish, then try again. Nothing "
    "was packed or brought in."
)
BACKUP_HOLDS_THE_SERVER = (
    f"A backup is running on this server's {MAINTENANCE_TAB} tab with the database started "
    "on its own for it, and the database is stopped again when the backup ends. Wait for "
    "the backup to finish, then try again. Nothing was started or stopped."
)
# What a Backup or Restore refused by `docker.maintenance_lease()` is told: the running job's
# own sentence, to which the refused one adds what it did not do (T216 review round 3).
BACKUP_HOLDS_THE_DATABASES = (
    f"A backup of this server is running on its {MAINTENANCE_TAB} tab. Wait for it to "
    "finish, then try again."
)
RESTORE_HOLDS_THE_DATABASES = (
    f"A restore is writing into this server's databases on its {MAINTENANCE_TAB} tab. Wait "
    "for it to finish, then try again."
)
# T681: what every other press of this Yu'lon that would write to the server is told while a
# Restore loads (`docker.hold_the_server(alone=...)`): a Modules job's SQL, an account, a network
# change, a bot rebuild. Nothing it would have written was written.
RESTORE_WRITES_ALONE = (
    f"A restore is writing into this server's databases on its {MAINTENANCE_TAB} tab, and "
    "nothing else may write to them until it has finished. Wait for the restore to finish, "
    "then try again. Nothing was changed."
)


MOVE_WRITES_ALONE = (
    f"Accounts and characters are being brought in on this server's {MAINTENANCE_TAB} tab, "
    "and nothing else may write to its databases until that has finished. Wait for it to "
    "finish, then try again. Nothing was changed."
)
"""T681 review: a bring-in's load is a restore's load; every other writer waits for it."""


def another_press_writes(press: str) -> str:
    """Why a Restore is refused while another press of this Yu'lon works on the server (T681)."""
    return (
        f"{press} is working on this server right now, and a restore must be the only thing "
        "writing to its databases. Wait for it to finish, then try again."
    )


MOVE_HOLDS_THE_SERVER = (
    "Accounts and characters are being packed or brought in on this server's "
    f"{MAINTENANCE_TAB} tab, with the database started on its own for it. Wait for that "
    "to finish, then try again. Nothing was started or stopped."
)
MOVE_HOLDS_THE_DATABASES = (
    "Accounts and characters are being packed or brought in on this server's "
    f"{MAINTENANCE_TAB} tab. Wait for that to finish, then try again."
)
# T737: the same three for a whole-server bring-in (the Catalog tile's "Bring from another
# computer…") while it puts the databases in: it is not on the Maintenance tab, and it brings
# in the whole server, not accounts and characters.
MOVE_IN_HOLDS_THE_SERVER = (
    "This server is being brought in from another computer, and its databases are being put "
    "in with the database started on its own for it. Wait for that to finish, then try again. "
    "Nothing was started or stopped."
)
# T738: what a Pack, a Bring in, or a second "Bring from another computer…" on the same folder
# is told during that load (`move_flows.move_held_reason()`). The sentence above ends in a
# Start's or Stop's words; a move neither starts nor stops anything.
MOVE_IN_HOLDS_A_MOVE = (
    "This server is being brought in from another computer, and its databases are being put "
    "in. Packing them now would take a half-made copy, and bringing a move in would write "
    "over what is being put in. Wait for that to finish, then try again. Nothing was packed "
    "or brought in."
)
MOVE_IN_HOLDS_THE_DATABASES = (
    "This server is being brought in from another computer, and its databases are being put "
    "in. Wait for that to finish, then try again."
)
MOVE_IN_WRITES_ALONE = (
    "This server is being brought in from another computer, and nothing else may write to its "
    "databases while they are being put in. Wait for that to finish, then try again. Nothing "
    "was changed."
)
# T830: the same three while "Start over with a plain install" takes its copy of the databases a
# bring-in left, with the database started on its own for it.
START_OVER_HOLDS_THE_SERVER = (
    "An unfinished bring-in on this server is being started over, and a copy of its databases "
    "is being taken first. Wait for that to finish, then try again. Nothing was started or "
    "stopped."
)
START_OVER_HOLDS_A_MOVE = (
    "An unfinished bring-in on this server is being started over, and a copy of its databases "
    "is being taken first. Wait for that to finish, then try again. Nothing was packed or "
    "brought in."
)
START_OVER_HOLDS_THE_DATABASES = (
    "An unfinished bring-in on this server is being started over, and a copy of its databases "
    "is being taken first. Wait for that to finish, then try again."
)
MOVE_RUNNING = (
    "Accounts and characters are being packed or brought in on this server's "
    f"{MAINTENANCE_TAB} tab, and removing the server now would leave its databases "
    "half-written. Wait for it to finish, then try again. Nothing was removed."
)
# T604: the Maintenance tab deleting old backups holds the databases' lease like a Backup does,
# so a dump cannot start into a folder being cleared.
PRESS_DELETE_BACKUPS = "Delete backups"
DELETE_HOLDS_THE_DATABASES = (
    f"Old backups are being deleted on this server's {MAINTENANCE_TAB} tab. Wait for that "
    "to finish, then try again."
)
TUNING_RUNNING = (
    "A Tuning change is being saved or put back on this server's Tuning tab. Wait for it "
    "to finish, then try again. Nothing was removed."
)
NETWORK_RUNNING = (
    "A network change is being applied on this server's Networking tab. Wait for it to "
    "finish, then try again. Nothing was removed."
)


# T568: what a press refused by another Yu'lon's reservation of the server says
# (`docker.server_claim()`). Each leads with who holds it and ends with what to do.
PRESS_START = "Start"
PRESS_STOP = "Stop"
PRESS_BACKUP = "Backup"
PRESS_RESTORE = "Restore"
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
        worse = {
            PRESS_RESTORE: " A restore that is loading the databases would be left half-loaded:"
            " restore it again afterwards.",
            PRESS_BACKUP: " A backup being taken would be left incomplete:"
            " take it again afterwards.",
        }.get(press, "")
        return (
            f"Another Yu'lon {_doing(press, label)}{started}{by}. Stopping now ends that too, "
            "wherever it is. A database file it is running may be left part-done, and that "
            f"Yu'lon will say what it left.{worse}"
        )
    return (
        f"Another Yu'lon {_doing(press, label)}{started}{by}. Nothing was changed. Wait "
        f"for it to finish, then press \u201c{this_press}\u201d again."
    )


def corrections_held_elsewhere(label: str, press: str, since: str, who: str) -> str:
    """The Server tab's banner while another Yu'lon holds the server (T607, T568 plan 6).

    A sentence and no button: its world updates may still be running, and a retry offered over
    them would race them. The banner is asked again, so it goes when the holder does.
    """
    started = f", started {since}" if since else ""
    by = f" by {who}" if who else ""
    return (
        f"Another Yu'lon is working on {label} ({press or 'a job'}){started}{by}; its world "
        "updates may still be running, so no retry is offered. Nothing changes until it is "
        "done."
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


def reservation_lost_line(press: str) -> str:
    """What a press says last when its reservation ended from elsewhere while it ran (T568)."""
    return (
        f"This server's reservation in Docker ended from elsewhere while \u201c{press}\u201d was "
        "running (another Yu'lon stopped it, or Docker restarted), so the job ended and "
        f"started nothing after that. Press Start, or \u201c{server_build_presses.REBUILD}\u201d, "
        "to bring the server up on what is on disk."
    )


SQL_HOLD_LOST = (
    "Another Yu'lon stopped this server while this was being applied, so no more SQL was sent. "
    "The statements before that already ran: press the module's button again once the server "
    "is stopped."
)


ACTION_HOLD_LOST = (
    "Another Yu'lon stopped this server while this was being done, so the rest was not done. "
    "What ran before that stays as it is: press the module's button again once the other "
    "Yu'lon is done."
)
"""A Modules action ended between two of its steps by another Yu'lon's "Stop anyway" (T607)."""


def server_reservation_unavailable(label: str, said: str) -> str:
    """No reservation could be made at all: no Docker, no image, a daemon that would not answer."""
    return f"Yu'lon could not reserve {label} in Docker. {said} Nothing was changed."


def wsl_docker_unreachable(distro: str, why: str) -> str:
    """Why a WSL distro's Docker did not answer, and what fits that cause (`wsl.BootResult`)."""
    if why == "no-docker":
        return (
            f"The WSL distro {distro} has no Docker program. If it uses Docker Desktop, start "
            f"Docker Desktop and turn on its WSL integration for {distro}; otherwise install "
            "Docker in it."
        )
    if why == "desktop":
        return (
            f"The WSL distro {distro} needs Docker Desktop. Start Docker Desktop and turn on its "
            f"WSL integration for {distro} (Settings, Resources, WSL integration)."
        )
    if why == "permission":
        return (
            f"The WSL distro {distro} will not let your user use Docker (permission denied). Add "
            "your user to the docker group in that distro, then open a new session and try again."
        )
    if why == "silent":
        return (
            f"Docker is not running in the WSL distro {distro}. Start it there "
            "(for example: sudo systemctl start docker), then try again."
        )
    if why == "gone":
        return f"Windows says the WSL distro {distro} no longer exists."
    if why == "cancelled":
        return f"Yu'lon stopped waiting for the WSL distro {distro}."
    return (
        f"The WSL distro {distro} was starting and its Docker did not answer in time; wait a "
        "moment and try again."
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
