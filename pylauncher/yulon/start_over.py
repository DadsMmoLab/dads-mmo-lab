"""T830: "Start over with a plain install" for a folder a bring-in did not finish.

A whole-server bring-in (`move_server.MovedInInstall`) that stops part-way leaves its record,
`.yulon-move-in.json`, and the Catalog's Install refuses that folder (T601): a plain install
would carry on from the install record the bring-in left and boot whatever it had loaded so far
(the data step replaces the databases one by one) as if it were a fresh server. Renaming the
record away was the first answer and was refused in review for exactly that.

So starting over is a RESET of the folder, one transaction behind one question, and only then
the plain install:

1. **Reserved.** This process counts the folder as being reset (`move_server.starting_over`,
   which a bring-in of this process is refused by), and the server is reserved across processes
   (`docker.server_claim`, the reservation Install and Bring-in take), for the whole reset and
   the plain install after it. Either refused, nothing is changed.
2. **Re-read.** Under the reservation the bring-in's record is read again: it must be the one
   the player was asked about, unfinished, and not named by a bring-in that is still running
   (`move_server.press_running`). The reset's own record (`RESET_FILE`) is written next, and the
   bring-in's record is read a second time: a bring-in that looked before it existed has written
   its name by then, and one that looks after it refuses (`MovedInInstall.run`).
3. **A copy first.** With the server stopped, every database the server has is dumped by the
   Maintenance tab's own backup (`maintenance.backup`), into the backups folder that then moves
   beside the server folder. A database that cannot be copied stops the press with nothing
   removed. A server that has no database yet has nothing to copy.
4. **Removed.** The Uninstall's own steps (`purge.Uninstaller`, "keep my characters" unticked):
   the containers, EVERY volume of the server's compose project, the built images, the files
   modules put into the game client, the channel's records of the database that went, and every
   file in the folder, with the backups moved beside it first. Two things differ from an
   Uninstall: the folder itself is kept with its id file and the two records (the reset is still
   working from them), and an unfinished restore is not a refusal (it is the cut-off data step
   this clears) unless its load is still writing.
5. **Checked.** No container and no volume of the compose project is left (the project the
   folder names AND the one the plain install will use, `composegen.project_name`, so the
   install cannot mount an old database), the install record is gone, the folder holds nothing
   but those three files, and the bring-in's record is still there.
6. **Set aside, then installed.** Only then does the bring-in's record move, to a name nobody
   has, in the folder of kept backups beside the server folder (never inside it: the plain
   install's clone empties the server folder). The reset's record goes last, and the plain
   install runs inside the same reservation.

Every step is recorded in `RESET_FILE` when it is done, so a reset that stopped part-way
(a crash, a refusal, Stop) is carried on by the next Install press and never repeats its copy.
While that record is there, Bring-in refuses the folder (`move_server.folder_refusal`), and a
reset that stopped before anything was removed takes its record away again, leaving the
bring-in exactly as it was. Every failure after removal began keeps the bring-in's record where
it is: the folder is never mistaken for a plain install that finished.
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from yulon import docker, kept_backups, move_server, purge, rmtree
from yulon.backup_shelf import START_OVER_LABEL
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_wotlk import maintenance
from yulon.log import get_logger
from yulon.move_flows import MoveError, MoveWorld

logger = get_logger(__name__)

PRESS_START_OVER = "Start over with a plain install"
"""The press, as the question names it and another Yu'lon's refusal quotes it."""

RESET_FILE = move_server.RESET_FILE
MOVE_IN_FILE = move_server.MOVE_IN_FILE

SET_ASIDE_PREFIX = ".yulon-move-in.set-aside-"
"""The bring-in's record, set aside: `<prefix><UTC stamp>[-n].json`, never onto another file."""

KEPT_IN_THE_FOLDER = (MOVE_IN_FILE, RESET_FILE, docker.FOLDER_ID_FILE)
"""What the reset leaves in the server folder until its last step: the two records it works from
and the id file its reservation is named by (`docker.server_claim`)."""

STEPS = ("project", "stopped", "copy", "removed", "verified", "set-aside")
"""The reset's steps, in order; each is recorded in `RESET_FILE` when it is done."""

QUESTION_TITLE = "Start over with a plain install?"
QUESTION = (
    "A bring-in on this folder is not finished.\n\n"
    "The unfinished bring-in and everything it loaded into this server will be removed, then a "
    "plain install starts. Nothing from the other computer is kept. A copy of what is there is "
    "kept first.\n\n"
    "The server is then built from the start, as on a first install. Choose No to keep the "
    "bring-in: press Bring from another computer… again with the same file and folder to carry "
    "it on."
)
FINISH_TITLE = "Finish starting over?"
FINISH_QUESTION = (
    "Starting this folder over with a plain install did not finish. Finish it now? Whatever of "
    "the bring-in is still there is removed (a copy is kept first), then a plain install starts."
)

NOTHING_TO_START_OVER = (
    "This folder holds no unfinished bring-in, so there is nothing to start over. Nothing was "
    "changed."
)
RECORD_UNREADABLE = (
    "The bring-in's record in this folder cannot be read, so Yu'lon cannot tell what it loaded "
    "and will not remove anything. Nothing was changed."
)
RECORD_CHANGED = (
    "The bring-in in this folder changed after you were asked, so nothing was changed. Press "
    "Install again to see it as it is now."
)
RECORD_GONE = (
    "The bring-in's record left this folder before starting over was done, so Yu'lon will not "
    "carry it on. Nothing more was changed."
)
NO_PROJECT = (
    "Yu'lon cannot tell which Docker project belongs to this folder, so it will not remove "
    "anything. Nothing was changed."
)
COPY_FAILED = (
    "Yu'lon could not keep a copy of this server's databases first, so it removed nothing and "
    "the bring-in is as it was."
)
STOPPED = "Stopped."
LOST = (
    "This server's reservation in Docker ended from elsewhere (another Yu'lon, or Docker "
    "restarting)."
)
KEPT_RECORD = (
    "The bring-in's record is kept and nothing was started: press Install on this tile again to "
    "finish starting over."
)
UNCHANGED = "Nothing was removed and the bring-in is as it was."
READY = (
    "The folder is clean: nothing of the bring-in is left in it or in Docker. The plain install "
    "starts now."
)


def reset_unreadable(path: Path) -> str:
    return (
        f"The record of an earlier start over, {path}, cannot be read, so Yu'lon will not carry "
        "it on. Nothing was changed."
    )


class StartOverError(MoveError):
    """A start over refused or stopped, in the sentence the player reads."""


# ------------------------------------------------------------------------------ the record


@dataclass
class _Reset:
    """`RESET_FILE`: which bring-in is being reset, and what of the reset is done."""

    server_dir: Path
    package: str
    started_at: str
    done: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    copies: list[str] = field(default_factory=list)
    kept: str | None = None
    set_aside: str | None = None
    removing: bool = False
    """Set before the first thing is removed: from then on a failure keeps both records."""

    @classmethod
    def read(cls, server_dir: Path) -> _Reset | None:
        """The record, None when there is none; one that cannot be read refuses."""
        path = server_dir / RESET_FILE
        if not os.path.lexists(path):
            return None
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StartOverError(reset_unreadable(path)) from exc
        if not isinstance(body, dict) or not isinstance(body.get("package"), str):
            raise StartOverError(reset_unreadable(path))

        def strings(key: str) -> list[str]:
            value = body.get(key)
            return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []

        kept, aside = body.get("kept"), body.get("set_aside")
        return cls(
            server_dir=server_dir,
            package=body["package"],
            started_at=str(body.get("started_at") or ""),
            done=strings("done"),
            projects=strings("projects"),
            copies=strings("copies"),
            kept=kept if isinstance(kept, str) else None,
            set_aside=aside if isinstance(aside, str) else None,
            removing=bool(body.get("removing")),
        )

    def save(self) -> None:
        """Atomic: a `.partial` beside the record, then a rename onto it."""
        body = {
            "version": 1,
            "package": self.package,
            "started_at": self.started_at,
            "done": self.done,
            "projects": self.projects,
            "copies": self.copies,
            "kept": self.kept,
            "set_aside": self.set_aside,
            "removing": self.removing,
        }
        target = self.server_dir / RESET_FILE
        partial = target.with_name(target.name + ".partial")
        partial.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
        os.replace(partial, target)

    def finished(self, step: str) -> None:
        self.done.append(step)
        self.save()

    def forget(self) -> None:
        (self.server_dir / RESET_FILE).unlink(missing_ok=True)


# ------------------------------------------------------------------------------ the seams


@dataclass(frozen=True)
class ResetSeams:
    """Every reach into Docker and the databases the reset makes; `app_seams()` binds the real."""

    claim: Callable[[], AbstractContextManager[object]]
    """The server's reservation across processes; refused with `docker.ServerHeldError`."""
    projects: Callable[[], tuple[str, ...]]
    """Every compose project whose objects must be gone: the folder's and the plain install's."""
    volumes_of: Callable[[str], list[str] | None]
    containers_of: Callable[[str], list[str] | None]
    server_up: Callable[[], bool]
    stop: Callable[[], object]
    running_load: Callable[[], str | None]
    """The sentence while a restore's load still writes into the databases, else None."""
    database_session: Callable[[], AbstractContextManager[object]]
    """The database up on its own, leased and held, for the copy."""
    schemas: Callable[[], tuple[str, ...]]
    copy: Callable[[], maintenance.BackupReport]
    uninstall_refusal: Callable[[], str]
    """Why the Uninstall's own steps would refuse this folder now ("" when they would not);
    asked before anything is removed (`purge.Uninstaller.plan`, which reads only)."""
    uninstall: Callable[[], purge.PurgeReport]
    """The Uninstall's own steps, for a folder that has its install record."""
    empty_folder: Callable[[], Path | None]
    """For a folder with no install record: backups moved aside (where to), the rest removed."""


@dataclass(frozen=True)
class Removed:
    kept: Path | None
    warnings: tuple[str, ...] = ()


# ------------------------------------------------------------------------------ the press


class StartOver:
    """The engine a Catalog tile runs for "Start over with a plain install" (an `InstallRun`).

    `plain` is the tile's own install engine, run once the reset is done, inside the same
    reservation. `seams` is called on the job's thread, so nothing slow runs on the window's.
    `expected` is the bring-in's record as the player was asked about it: one that changed
    since is refused.
    """

    def __init__(
        self,
        server_dir: Path,
        *,
        plain: Any,
        seams: Callable[[], ResetSeams],
        expected: Mapping[str, object] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.server_dir = server_dir
        self._plain = plain
        self._seams = seams
        self._expected = expected
        self._now = now

    def run(
        self,
        options: Any = None,
        *,
        cancel: Any = None,
        ask: Any = None,
    ) -> Iterator[str]:
        server_dir = getattr(options, "server_dir", None)
        if server_dir is None or Path(server_dir) != self.server_dir:
            raise StartOverError(
                "The folder this would start over is not the one you were asked about. Nothing "
                "was changed."
            )
        with move_server.starting_over(self.server_dir), contextlib.ExitStack() as reserved:
            try:
                seams = self._seams()
            except MoveError:
                raise
            except Exception as exc:  # noqa: BLE001 - said in one sentence, the cause in detail
                raise StartOverError(
                    "Yu'lon could not get ready to start this folder over. Nothing was changed.",
                    detail=str(exc),
                ) from exc
            try:
                held = reserved.enter_context(seams.claim())
            except docker.ServerHeldError as exc:
                raise StartOverError(str(exc)) from exc
            yield from self._reset(seams, held, cancel)
            yield READY
            yield from self._plain.run(options, cancel=cancel, ask=ask)

    # -- the reset -----------------------------------------------------------------------

    def _reset(self, seams: ResetSeams, held: object, cancel: Any) -> Iterator[str]:
        reset, resuming = self._begin()
        yield (
            "Carrying on starting this folder over with a plain install."
            if resuming
            else "Starting this folder over with a plain install."
        )
        try:
            yield from self._steps(seams, held, cancel, reset)
        except Exception as exc:
            if not reset.removing:
                # Nothing was removed: the folder is the bring-in's as it was, so its record goes
                # and Bring-in may carry it on again.
                reset.forget()
                if isinstance(exc, StartOverError):
                    raise
                raise StartOverError(f"{exc} {UNCHANGED}") from exc
            raise StartOverError(
                f"Starting over stopped part-way. {exc} {KEPT_RECORD}",
                detail=str(getattr(exc, "detail", "") or ""),
            ) from exc
        reset.forget()

    def _begin(self) -> tuple[_Reset, bool]:
        """The reset's record: carried on when there is one, else written after the checks."""
        server_dir = self.server_dir
        existing = _Reset.read(server_dir)
        there = os.path.lexists(server_dir / MOVE_IN_FILE)
        marker = move_server.read_marker(server_dir) if there else None
        if existing is not None:
            if there:
                if marker is None or marker.get("package") != existing.package:
                    raise StartOverError(RECORD_CHANGED)
                self._refuse_a_running_press(marker)
            elif "set-aside" not in existing.done:
                raise StartOverError(RECORD_GONE)
            return existing, True
        if not there:
            raise StartOverError(NOTHING_TO_START_OVER)
        if marker is None:
            raise StartOverError(RECORD_UNREADABLE)
        if move_server.move_in_finished(marker):
            raise StartOverError(NOTHING_TO_START_OVER)
        if self._expected is not None and (
            marker.get("package") != self._expected.get("package")
            or marker.get("done") != self._expected.get("done")
        ):
            raise StartOverError(RECORD_CHANGED)
        self._refuse_a_running_press(marker)
        reset = _Reset(
            server_dir=server_dir,
            package=str(marker["package"]),
            started_at=self._now().isoformat(),
        )
        reset.save()
        # A bring-in that passed its own look before the record above existed has saved its
        # name in its record by now; one that looks after it sees this one and refuses.
        again = move_server.read_marker(server_dir)
        try:
            if again is None or again.get("package") != reset.package:
                raise StartOverError(RECORD_CHANGED)
            self._refuse_a_running_press(again)
        except StartOverError:
            reset.forget()
            raise
        return reset, False

    def _refuse_a_running_press(self, marker: Mapping[str, object]) -> None:
        said = move_server.press_running(marker.get("pressing"), self.server_dir)
        if said is not None:
            raise StartOverError(said)

    def _check(self, held: object, cancel: Any) -> None:
        """Between two steps: Stop pressed, or the reservation lost. Said with what is left."""
        if cancel is not None and cancel.is_set():
            raise MoveError(STOPPED)
        lost = getattr(held, "lost", None)
        if lost is not None and lost.is_set():
            raise MoveError(LOST)

    def _steps(self, seams: ResetSeams, held: object, cancel: Any, reset: _Reset) -> Iterator[str]:
        if "project" not in reset.done:
            projects = seams.projects()
            if not projects:
                raise StartOverError(NO_PROJECT)
            reset.projects = list(projects)
            reset.finished("project")
        self._check(held, cancel)
        if "stopped" not in reset.done:
            said = seams.running_load()
            if said is not None:
                raise StartOverError(said)
            if seams.server_up():
                yield "Stopping the server."
                seams.stop()
            reset.finished("stopped")
        self._check(held, cancel)
        if "copy" not in reset.done:
            yield from self._copy(seams, reset)
            reset.finished("copy")
        self._check(held, cancel)
        if "removed" not in reset.done:
            recorded = (self.server_dir / native.STATE_FILE).exists()
            self._refuse_removal(seams, reset, recorded=recorded)
            reset.removing = True
            reset.save()
            yield (
                "Removing the unfinished bring-in: the server's containers, its database "
                "volumes, its built images and every file in its folder."
            )
            removed = self._remove(seams, recorded=recorded)
            if removed.kept is not None:
                reset.kept = str(removed.kept)
            for warning in removed.warnings:
                yield f"!! {warning}"
            reset.finished("removed")
        self._check(held, cancel)
        if "verified" not in reset.done:
            problems = self._baseline(seams, reset)
            if problems:
                raise StartOverError(
                    f"After the removal {'; '.join(problems)}, so the plain install was not "
                    "started."
                )
            reset.finished("verified")
        if "set-aside" not in reset.done:
            aside = self._set_aside(reset)
            reset.set_aside = str(aside)
            reset.finished("set-aside")
            yield f"The bring-in's record is kept in {aside}."
        if reset.kept:
            yield f"The backups, with the copy taken first, are kept in {reset.kept}."

    def _projects_objects(
        self, seams: ResetSeams, projects: Sequence[str]
    ) -> tuple[list[str], list[str]]:
        """`(what is left in Docker, what Docker would not say)` for these projects."""
        left: list[str] = []
        unsaid: list[str] = []
        for project in projects:
            volumes = seams.volumes_of(project)
            containers = seams.containers_of(project)
            if volumes is None or containers is None:
                unsaid.append(project)
                continue
            left.extend([*containers, *volumes])
        return left, unsaid

    def _copy(self, seams: ResetSeams, reset: _Reset) -> Iterator[str]:
        """Every database the server has, dumped before anything is removed; a failure refuses.

        Skipped only when Docker has nothing at all of the compose projects: no database
        volume and no container, so no database can have been made. A container with no
        database volume (a database kept somewhere else) is copied like any other.
        """
        left, unsaid = self._projects_objects(seams, reset.projects)
        if unsaid:
            raise StartOverError(
                f"Yu'lon could not ask Docker what {', '.join(unsaid)} has, so it removed "
                "nothing and the bring-in is as it was."
            )
        if not left:
            yield "This server has no database yet, so there is nothing to copy."
            return
        try:
            with seams.database_session():
                schemas = seams.schemas()
                if not schemas:
                    yield "This server's database holds nothing yet, so there is nothing to copy."
                    return
                yield f"Keeping a copy of {', '.join(schemas)} first."
                report = seams.copy()
        except Exception as exc:  # noqa: BLE001 - any failure to copy refuses, in one sentence
            raise StartOverError(COPY_FAILED, detail=str(exc)) from exc
        missing = sorted(set(schemas) - set(report.databases))
        if missing:
            raise StartOverError(COPY_FAILED, detail=f"not copied: {', '.join(missing)}")
        reset.copies = [str(d.path) for d in report.dumps]
        yield f"A copy of {', '.join(report.databases)} is kept first."

    def _refuse_removal(self, seams: ResetSeams, reset: _Reset, *, recorded: bool) -> None:
        """Every refusal of the removal, asked before its first step (it reads only)."""
        if recorded:
            refusal = seams.uninstall_refusal()
            if refusal:
                raise StartOverError(refusal)
            return
        # No install record: nothing in Docker can be proved this folder's, so nothing may be
        # there (an earlier press of this reset removed it, or the install never got that far).
        left, unsaid = self._projects_objects(seams, reset.projects)
        if unsaid:
            raise StartOverError(
                f"Yu'lon could not ask Docker what {', '.join(unsaid)} still has, so it removed "
                "nothing."
            )
        if left:
            raise StartOverError(
                f"This folder has no install record, and Docker still has {', '.join(left)} for "
                "it, which Yu'lon cannot prove are this folder's, so it removed nothing."
            )

    def _remove(self, seams: ResetSeams, *, recorded: bool) -> Removed:
        if recorded:
            report = seams.uninstall()
            return Removed(kept=report.backups_kept, warnings=report.warnings)
        return Removed(kept=seams.empty_folder())

    def _baseline(self, seams: ResetSeams, reset: _Reset) -> list[str]:
        """What is not clean, in the player's words; empty when the folder may be installed."""
        problems: list[str] = []
        left, unsaid = self._projects_objects(seams, reset.projects)
        if unsaid:
            problems.append(f"Docker would not say what {', '.join(unsaid)} still has")
        if left:
            problems.append(f"Docker still has {', '.join(left)}")
        if (self.server_dir / native.STATE_FILE).exists():
            problems.append("the install record is still in the folder")
        try:
            rest = native._listing(self.server_dir, ignoring=KEPT_IN_THE_FOLDER)
        except InstallerError:
            problems.append("the folder could not be listed")
        else:
            if rest:
                problems.append(f"the folder still holds {', '.join(sorted(rest)[:5])}")
        if not os.path.lexists(self.server_dir / MOVE_IN_FILE):
            problems.append("the bring-in's record is missing")
        return problems

    def _set_aside(self, reset: _Reset) -> Path:
        """The bring-in's record, moved to a new name beside the server folder; never over a file.

        Created with O_EXCL under a name with the time in it, `-2`, `-3`... when that is taken,
        written whole, and only then is the record in the server folder removed.
        """
        source = self.server_dir / MOVE_IN_FILE
        data = source.read_bytes()
        folder = Path(reset.kept) if reset.kept else None
        if folder is None or not folder.is_dir():
            folder = _new_kept_folder(self.server_dir)
            reset.kept = str(folder)
        stamp = self._now().strftime("%Y%m%dT%H%M%SZ")
        number = 1
        while True:
            suffix = "" if number == 1 else f"-{number}"
            target = folder / f"{SET_ASIDE_PREFIX}{stamp}{suffix}.json"
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                number += 1
                continue
            break
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        source.unlink()
        return target


def _new_kept_folder(server_dir: Path) -> Path:
    """A new `<server folder> - kept backups` beside the server folder (`kept_backups`)."""
    while True:
        folder = kept_backups.kept_backups_path(server_dir)
        try:
            folder.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return folder


# ------------------------------------------------------------------------------ the real seams


def running_load_refusal(server_dir: Path) -> str | None:
    """The Uninstall's restore refusal for a start over: only a load that still writes refuses.

    An unfinished restore is what a bring-in cut off in its data step leaves, and its databases
    are what this removes; its record moves with the backups. A load another Yu'lon left
    writing would be cut off by the containers' removal, so that is still refused.
    """
    try:
        record = maintenance.interrupted_restore(server_dir)
    except OSError as exc:  # fail closed, as the Uninstall does
        return f"Yu'lon could not read this server's restore record ({exc}). Nothing was removed."
    if record is not None and record.load_running:
        return (
            "A restore is still writing into this server's databases in Docker. Wait for it to "
            "finish, then try again. Nothing was removed."
        )
    return None


def empty_the_folder(server_dir: Path) -> None:
    """Every file and folder in `server_dir` but `KEPT_IN_THE_FOLDER`; records last (`purge`)."""
    rmtree.empty_tree_last(server_dir, purge.REMOVED_LAST, keep=KEPT_IN_THE_FOLDER)


def empty_without_record(server_dir: Path) -> Path | None:
    """A folder with no install record: its backups moved beside it as an Uninstall's are, then
    the rest removed. Returns where the backups went, None when it had none."""
    found = purge.find_backups(server_dir)
    state = purge.KeptState()
    purge.prepare_backups(found, state)
    purge.keep_backups(found, state)
    purge.release_backups_link(server_dir)
    empty_the_folder(server_dir)
    return state.where


def app_seams(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    world: MoveWorld,
    image_refs: Sequence[str],
    take_back_client_files: Callable[[], tuple[list[str], list[str]]] | None = None,
    logs_dir: Path | None = None,
) -> ResetSeams:
    """The real seams for one Catalog folder, over the Maintenance tab's own `world`."""
    from yulon import move_flows, platform

    if world.wsl_distro:
        # The Catalog's Install and Bring-in never build a server inside a WSL distro.
        raise StartOverError(
            f"This server lives inside the WSL distro {world.wsl_distro}, and Yu'lon cannot "
            "start such a folder over. Nothing was changed."
        )
    spec = world.spec or entry.container_spec()
    planned = composegen.project_name(entry.id, server_dir)
    avoided = purge.removable_images(tuple(image_refs))

    def resolved() -> str | None:
        return docker.install_project(spec, server_dir)

    def projects() -> tuple[str, ...]:
        return tuple(dict.fromkeys(p for p in (resolved(), planned) if p))

    uninstaller = purge.Uninstaller(
        game=entry.id,
        server_dir=server_dir,
        spec=spec,
        image_refs=image_refs,
        logs_dir=logs_dir if logs_dir is not None else platform.config_dir() / "logs",
        forget=lambda: None,  # an unfinished bring-in is never one of the remembered servers
        project_of=lambda: resolved() or planned,
        remove_folder=empty_the_folder,
        restore_refusal=running_load_refusal,
        take_back_client_files=take_back_client_files,
        # Taken inside the reset's own reservation of the server, so it is shared, not refused.
        hold_server=lambda press: docker.server_hold(
            server_dir, press, spec=spec, label=entry.name, avoid_images=avoided
        ),
    )
    mysql = cast(maintenance.MysqlDocker, world.mysql)

    def copy() -> maintenance.BackupReport:
        return maintenance.backup(
            server_dir,
            mysql,
            game=world.game,
            label=START_OVER_LABEL,
            spec=spec,
            core_databases=entry.core_databases(),
            running=world.running,
            # The databases a cut-off load left are what this copies: the backup's refusal of
            # an unfinished restore (T746) is for a copy listed as good, which this never is.
            restore_safety_copy=True,
        )

    def session() -> AbstractContextManager[object]:
        from yulon import forgetting

        return move_flows._database_session(
            world,
            because="a copy of the databases is kept before starting over",
            undone=UNCHANGED,
            press=PRESS_START_OVER,
            leased=forgetting.START_OVER_HOLDS_THE_DATABASES,
            held=forgetting.START_OVER_HOLDS_THE_SERVER,
            for_a_move=forgetting.START_OVER_HOLDS_A_MOVE,
        )

    return ResetSeams(
        claim=lambda: docker.server_claim(
            server_dir,
            press=PRESS_START_OVER,
            spec=spec,
            label=entry.name,
            # The reservation must not run from an image the removal takes (T622).
            avoid_images=avoided,
        ),
        projects=projects,
        volumes_of=lambda project: docker.project_volumes(project),
        containers_of=lambda project: docker.project_containers(project),
        # Any of its containers, the database alone included: the removal refuses a running one.
        server_up=lambda: bool({spec.db, spec.auth, spec.world} & set(world.running())),
        stop=world.stop_server,
        running_load=lambda: running_load_refusal(server_dir),
        database_session=session,
        schemas=lambda: maintenance.server_databases(mysql),
        copy=copy,
        uninstall_refusal=lambda: uninstaller.plan().refusal,
        uninstall=lambda: uninstaller.run(keep_characters=False),
        empty_folder=lambda: empty_without_record(server_dir),
    )


__all__ = [
    "FINISH_QUESTION",
    "FINISH_TITLE",
    "PRESS_START_OVER",
    "QUESTION",
    "QUESTION_TITLE",
    "ResetSeams",
    "StartOver",
    "StartOverError",
    "app_seams",
    "empty_the_folder",
    "running_load_refusal",
]
