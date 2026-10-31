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
   install's clone empties the server folder). The record names the archive before anything is
   touched (`set_aside_to`), so a death between writing the archive and removing the source is
   finished by the next press rather than stranding the folder with no bring-in record and no
   finished step. The reset's record STAYS, as the durable marker an install that never wrote
   its ownership record is still recognised by, and the plain install runs inside the same
   reservation.

Every step is recorded in `RESET_FILE` when it is done, so a reset that stopped part-way
(a crash, a refusal, Stop) is carried on by the next Install press and never repeats its copy.
A resumed reset proves every recorded dump is still there, uncut and the bytes it wrote, and
that together they cover every database the copy was for, before it removes a volume; a copy
that fails that proof (or one made again after the proof failed) refuses with nothing more
removed and is made again by the next press. While the record is there, Bring-in refuses the
folder (`move_server.folder_refusal`), and a reset that stopped before anything was removed
takes the record away again, leaving the bring-in exactly as it was. Every failure after
removal began keeps the bring-in's record where it is: the folder is never mistaken for a
plain install that finished, and a record rewound to the copy step keeps its `removing` flag
for the same reason (T830 S1: a copy that fails after part of the server was removed never
claims "the bring-in is as it was"). The marker goes only once the plain install has written
an ownership record that survives its first clone (T830 H1): a clone made while it is there
lands beside the folder and is moved in, so a death at any point in that clone leaves a folder
Install can carry on rather than one nothing accepts. That clone lands in a folder Yu'lon
makes beside the server folder and marks as its own (`prepare_staging`, T830 R2:
`<server folder>.yulon-clone-<8 hex>` holding `.yulon-staging`
with this folder's resolved path and this reset's id, recorded in the reset record); it is
emptied or removed only while that marker matches, and a folder at a recorded path without it
is refused by name, never touched. What the clone itself lands in is `<staging>/checkout`
(`staging_checkout`, T830 M1), one level below the marker: both clone seams delete their
destination before cloning, so the seam's wipe takes an unfinished checkout and never the
marker that makes the staging folder Yu'lon's.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
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

STAGING_MARKER = ".yulon-staging"
"""Inside a folder `clone-core` clones into beside the server folder: whose folder it is.

It names this server folder's resolved path and the id of the reset that made the folder, and
is the only thing that makes the folder removable (T830 R2): a folder at the recorded path
without a matching marker is not Yu'lon's to empty or delete.
"""

STAGING_SUFFIX = ".yulon-clone-"
"""The staging folder is `<server folder name>` + this + eight random hex digits."""

STAGING_CHECKOUT = "checkout"
"""Inside the staging folder: the destination `clone-core` is given (T830 M1).

Both real clone seams DELETE a destination that is not a checkout before cloning
(`git.RunnerGit.clone`, `git.ContainerGit.clone`), and the marker at the staging folder's top is
the only thing that makes the folder Yu'lon's to remove. So the clone lands one level down, at
`<staging>/checkout`: the seam's own wipe takes the checkout -- an unfinished one included --
and never the marker beside it.
"""

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


def damaged_copy(path: object) -> str:
    """A resumed reset's copy is missing, unreadable or not what was written (T830 H2)."""
    return (
        f"The copy Start over made of your databases is missing or damaged ({path}), so nothing "
        "was removed. Press Start over again to make a new copy first."
    )


def half_removed_copy_failed(reason: object) -> str:
    """A copy that could not be made after the removal began (T830 S1).

    The record stays: part of the server is gone, so the folder cannot go back to being the
    bring-in's, and nothing here says it was.
    """
    return (
        f"Start over had already removed part of this server, and the new copy of your "
        f"databases could not be made ({reason}). Nothing more was removed; press Start over "
        "again."
    )


def not_yulons_folder(path: object) -> str:
    """A folder at a recorded staging path that is not the one this reset made (T830 R2)."""
    return (
        f"Yu'lon will not remove {path}: it is not a folder Yu'lon made. Move or rename it, "
        "then press Install again."
    )


def staging_unmade(server_dir: object, why: object) -> str:
    """The staging folder for the clone could not be made or recorded (T830 R2)."""
    return (
        f"Yu'lon could not make a folder beside {server_dir} to clone this server into ({why}), "
        "so the folder was not touched. Press Install again to finish it."
    )


def _sha256(path: Path) -> str:
    """The whole file's SHA-256, read in chunks: a dump is far too large to read at once."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_move_in_record(path: Path) -> bool:
    """Whether `path` holds what `move_server.read_marker()` would take as a bring-in record.

    The same question, asked of a file rather than a server folder: a JSON object naming its
    package. A set-aside archive that answers yes is the bring-in's record, written whole.
    """
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(parsed, dict) and isinstance(parsed.get("package"), str)


def _write_whole(target: Path, data: bytes) -> None:
    """`target`, created exclusively with `data`, mode 0600, fsynced (T830 R1's archive).

    A file at a name this reset recorded that is not a bring-in record is this reset's own
    write cut off (it took the name when nothing was there): removed and written again. One
    that is a record is never replaced, and one taken in between is a `FileExistsError` rather
    than an overwrite.
    """
    if os.path.lexists(target) and not _is_move_in_record(target):
        target.unlink()
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


class StartOverError(MoveError):
    """A start over refused or stopped, in the sentence the player reads."""


class _CopyFailed(StartOverError):
    """The copy of the databases could not be made, and why (T830 S1).

    A kind of its own because what happens to it depends on how far the reset got: before the
    removal it is refused and the record goes (the bring-in is as it was), and after it began
    the record must stay and the refusal must not claim nothing was removed.
    """

    def __init__(self, message: str, *, reason: object, detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.reason = reason


# ------------------------------------------------------------------------------ the record


@dataclass(frozen=True)
class _Copy:
    """One dump the copy step made: where it is, what it holds, and what it was.

    `size` and `sha256` are recorded when the dump is written, so a resumed reset can prove
    the file it is about to remove databases in favour of is still the one it copied (T830 H2).
    """

    path: Path
    database: str
    size: int
    sha256: str

    def saved(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "database": self.database,
            "size": self.size,
            "sha256": self.sha256,
        }

    @classmethod
    def read(cls, raw: object) -> _Copy | None:
        """One saved copy, or None for an entry this build cannot vouch for."""
        if not isinstance(raw, dict):
            return None
        path, database = raw.get("path"), raw.get("database")
        size, digest = raw.get("size"), raw.get("sha256")
        if not isinstance(path, str) or not isinstance(database, str):
            return None
        if isinstance(size, bool) or not isinstance(size, int) or not isinstance(digest, str):
            return None
        return cls(path=Path(path), database=database, size=size, sha256=digest)


@dataclass
class _Reset:
    """`RESET_FILE`: which bring-in is being reset, and what of the reset is done."""

    server_dir: Path
    package: str
    started_at: str
    id: str = ""
    """This reset's own id, written with the record: what a staging folder's marker names, so a
    folder left beside the server folder is only ever emptied by the reset that made it."""
    done: list[str] = field(default_factory=list)
    projects: list[str] = field(default_factory=list)
    copies: list[_Copy] = field(default_factory=list)
    schemas: list[str] = field(default_factory=list)
    """The databases the copy step was asked to copy; every one must have a recorded dump."""
    copies_dir: str | None = None
    """Where the copy step put its dumps, for the refusal when no dump names a missing schema."""
    kept: str | None = None
    set_aside: str | None = None
    set_aside_to: str | None = None
    """The archive `_set_aside()` is about to write, saved before the source is touched.

    Written ahead so a death between writing the archive and removing the bring-in's record
    is finished by the next press instead of leaving a folder with neither (T830 R1)."""
    staging: str | None = None
    """The folder beside the server folder `clone-core` clones into, recorded before it clones
    (T830 R2), so a resumed press reuses the one this reset made."""
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

        raw_copies = body.get("copies")
        copies = [
            copy
            for raw in (raw_copies if isinstance(raw_copies, list) else [])
            if (copy := _Copy.read(raw)) is not None
        ]
        kept, aside = body.get("kept"), body.get("set_aside")
        copies_dir = body.get("copies_dir")
        ahead, staging = body.get("set_aside_to"), body.get("staging")
        ident = body.get("id")
        return cls(
            server_dir=server_dir,
            package=body["package"],
            started_at=str(body.get("started_at") or ""),
            id=ident if isinstance(ident, str) else "",
            done=strings("done"),
            projects=strings("projects"),
            copies=copies,
            schemas=strings("schemas"),
            copies_dir=copies_dir if isinstance(copies_dir, str) else None,
            kept=kept if isinstance(kept, str) else None,
            set_aside=aside if isinstance(aside, str) else None,
            set_aside_to=ahead if isinstance(ahead, str) else None,
            staging=staging if isinstance(staging, str) else None,
            removing=bool(body.get("removing")),
        )

    def save(self) -> None:
        """Atomic: a `.partial` beside the record, then a rename onto it."""
        body = {
            "version": 1,
            "package": self.package,
            "started_at": self.started_at,
            "id": self.id,
            "done": self.done,
            "projects": self.projects,
            "copies": [copy.saved() for copy in self.copies],
            "schemas": self.schemas,
            "copies_dir": self.copies_dir,
            "kept": self.kept,
            "set_aside": self.set_aside,
            "set_aside_to": self.set_aside_to,
            "staging": self.staging,
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
            # A finished plain install has its own ownership record, so the marker that guards
            # an install which never wrote one has done its work (T830 H1). The install's own
            # record writer removes it sooner; this is for an engine that finished without one.
            try:
                (self.server_dir / RESET_FILE).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(f"could not remove {self.server_dir / RESET_FILE}: {exc}")

    # -- the reset -----------------------------------------------------------------------

    def _reset(self, seams: ResetSeams, held: object, cancel: Any) -> Iterator[str]:
        reset, resuming = self._begin()
        if resuming:
            # Before any volume goes: a resumed reset trusts nothing it recorded about the
            # copy (T830 H2). Outside the try below, so the rewound record a failure writes
            # is not forgotten by the "nothing was removed" path.
            self._revalidate(seams, reset)
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
            detail = str(getattr(exc, "detail", "") or "")
            if isinstance(exc, _CopyFailed):
                # Part of this server was already removed, so the record must stay (T830 S1).
                # Its own "nothing was removed, the bring-in is as it was" is false here.
                raise StartOverError(half_removed_copy_failed(exc.reason), detail=detail) from exc
            raise StartOverError(
                f"Starting over stopped part-way. {exc} {KEPT_RECORD}", detail=detail
            ) from exc
        # The record stays: it is the durable marker that keeps a plain install's first clone
        # from stranding the folder, and is removed once that install has written its record.

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
                self._finish_set_aside(existing)
            if not existing.id:
                # A record written before the reset had an id (T830 R2): one now, so the
                # staging folder it may make from here on is provably this reset's.
                existing.id = secrets.token_hex(8)
                existing.save()
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
            id=secrets.token_hex(8),
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

    def _finish_set_aside(self, reset: _Reset) -> None:
        """A reset whose bring-in record is gone: the step is done if its archive is (T830 R1).

        The record named the archive before anything was touched (`set_aside_to`). One there
        that the bring-in's own reader would take is that archive, so the death was after the
        source was removed and the step only wants recording. Nothing else can prove the
        record was kept: the folder is refused as it was before (`RECORD_GONE`).
        """
        target = Path(reset.set_aside_to) if reset.set_aside_to else None
        if target is None or not _is_move_in_record(target):
            raise StartOverError(RECORD_GONE)
        reset.set_aside = str(target)
        reset.finished("set-aside")

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
                copies = [
                    _Copy(
                        path=dump.path,
                        database=dump.database,
                        size=dump.size_bytes,
                        sha256=_sha256(dump.path),
                    )
                    for dump in report.dumps
                ]
        except Exception as exc:  # noqa: BLE001 - any failure to copy refuses, in one sentence
            raise _CopyFailed(COPY_FAILED, reason=exc, detail=str(exc)) from exc
        missing = sorted(set(schemas) - set(report.databases))
        if missing:
            not_copied = f"not copied: {', '.join(missing)}"
            raise _CopyFailed(COPY_FAILED, reason=not_copied, detail=not_copied)
        reset.copies = copies
        reset.schemas = list(schemas)
        reset.copies_dir = str(report.directory)
        yield f"A copy of {', '.join(report.databases)} is kept first."

    def _revalidate(self, seams: ResetSeams, reset: _Reset) -> None:
        """A resumed reset proves its copy is still whole before it removes a volume (H2).

        Nothing is trusted from before the stop: every recorded dump must be there, readable,
        the size and hash it was written with, and together they must cover every database the
        copy step was asked for. Any failure refuses with nothing removed and rewinds the record
        to the copy step, so the next press makes a new copy first.

        `removing` is NOT cleared by the rewind (T830 S1). The removal began before this record
        was left, and a record that forgot it would let the next copy failure take the
        `forget()` path -- deleting the record of a folder part of which is already gone, which
        Bring-in would then accept.
        """
        if "copy" not in reset.done or "removed" in reset.done:
            return
        if not self._volumes_left(seams, reset):
            # Nothing left for the removal to take: no volume can be lost in favour of a copy.
            return
        problem = self._copy_problem(reset)
        if problem is None:
            return
        reset.done = [step for step in reset.done if step != "copy"]
        reset.copies = []
        reset.schemas = []
        reset.copies_dir = None
        reset.save()
        raise _CopyFailed(damaged_copy(problem), reason=problem)

    def _volumes_left(self, seams: ResetSeams, reset: _Reset) -> bool:
        """Whether Docker still has a volume of the reset's projects (unknown counts as yes)."""
        for project in reset.projects:
            volumes = seams.volumes_of(project)
            if volumes is None or volumes:
                return True
        return False

    def _copy_problem(self, reset: _Reset) -> object | None:
        """What is wrong with the recorded copy, or None when it is whole and covers everything."""
        for copy in reset.copies:
            try:
                if not copy.path.is_file():
                    return copy.path
                size = copy.path.stat().st_size
                digest = _sha256(copy.path)
            except OSError:
                return copy.path
            if size != copy.size or digest != copy.sha256:
                return copy.path
        missing = sorted(set(reset.schemas) - {copy.database for copy in reset.copies})
        if missing:
            if reset.copies_dir is not None:
                return Path(reset.copies_dir)
            return reset.server_dir / RESET_FILE
        return None

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
        """The bring-in's record, archived at the name the record named first (T830 R1).

        The order is the fix: `set_aside_to` is saved (atomically, with the record), then the
        archive is written whole, then the record in the server folder is removed, and only
        then is the step recorded. A death between any two is finished by the next press:
        with the source gone, `_begin()` records the step when the archive is whole; with the
        source still there, this method writes over a read that is absent or not a record.
        """
        source = self.server_dir / MOVE_IN_FILE
        target = Path(reset.set_aside_to) if reset.set_aside_to else self._plan_set_aside(reset)
        data = source.read_bytes()
        if not _is_move_in_record(target):
            # Not the archive: either a death after the name was chosen and before it was
            # written, or the write was cut off. The archive; when it is whole, wins.
            _write_whole(target, data)
        source.unlink()
        return target

    def _plan_set_aside(self, reset: _Reset) -> Path:
        """The archive's name, chosen and recorded before the source is touched (T830 R1).

        A name nobody has: the folder of kept backups beside the server folder (made when the
        server has none), the UTC time in the name, `-2`, `-3`... when that is taken. The
        write itself creates the file exclusively, so a name taken in between is a failure
        rather than an overwrite.
        """
        folder = Path(reset.kept) if reset.kept else None
        if folder is None or not folder.is_dir():
            folder = _new_kept_folder(self.server_dir)
            reset.kept = str(folder)
        stamp = self._now().strftime("%Y%m%dT%H%M%SZ")
        number = 1
        while True:
            suffix = "" if number == 1 else f"-{number}"
            target = folder / f"{SET_ASIDE_PREFIX}{stamp}{suffix}.json"
            if not os.path.lexists(target):
                break
            number += 1
        reset.set_aside_to = str(target)
        reset.save()
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


# -------------------------------------------------------------------- the clone's staging folder


def _same_folder(named: str, server_dir: Path) -> bool:
    """Whether `named` (as a marker recorded it) is `server_dir`, symlinks and case aside."""
    return os.path.normcase(os.path.realpath(named)) == os.path.normcase(
        os.path.realpath(server_dir)
    )


def _staged_here(folder: Path, server_dir: Path, reset_id: str) -> bool:
    """Whether `folder` is the staging folder THIS reset made, by its marker and nothing else.

    An absent or unreadable marker, one naming another server folder, or one naming another
    reset all answer no: the folder is then somebody's and is never emptied or removed
    (T830 R2).
    """
    try:
        body = json.loads((folder / STAGING_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(body, dict):
        return False
    here, ident = body.get("server_dir"), body.get("reset_id")
    return (
        isinstance(here, str)
        and isinstance(ident, str)
        and bool(reset_id)
        and ident == reset_id
        and _same_folder(here, server_dir)
    )


def _write_staging_marker(folder: Path, server_dir: Path, reset_id: str) -> None:
    """`.yulon-staging` in `folder`, atomic: this server folder's path and this reset's id."""
    body = {"server_dir": str(server_dir.resolve()), "reset_id": reset_id}
    target = folder / STAGING_MARKER
    partial = target.with_name(target.name + ".partial")
    partial.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, target)


def _new_staging(server_dir: Path) -> Path:
    """A folder beside `server_dir` under a name nobody has: eight random hex digits."""
    while True:
        folder = server_dir.with_name(f"{server_dir.name}{STAGING_SUFFIX}{secrets.token_hex(4)}")
        try:
            folder.mkdir(mode=0o700)
        except FileExistsError:
            continue
        return folder


def prepare_staging(server_dir: Path) -> tuple[Path, str]:
    """An empty folder beside `server_dir` to clone into, and the reset id it belongs to.

    T830 R2: the checkout of a folder mid-start-over lands beside it so the marker and the
    ownership record survive. The folder is one Yu'lon makes -- `<server folder>` + `-` and
    eight random hex digits, `mkdir` so it cannot be an existing folder -- and it holds
    `.yulon-staging` naming this server folder and this reset before anything is cloned into
    it. Its path is recorded in the reset record, so a resumed press reuses the folder it
    made (emptied first: it is this reset's) rather than piling up folders beside the server.

    T830 M1: what is cloned into is `staging_checkout()` under this folder, not the folder
    itself. The folder's marker is at its top, where this function and `remove_staging()` look
    for it, and the clone seams empty their destination before cloning: given the folder they
    would take the marker with everything else, and the folder would stop being Yu'lon's.

    Raises:
        StartOverError: the reset record cannot be read, or a folder at the recorded path is
            not the one this reset made -- refused by name and never touched.
    """
    server_dir = Path(server_dir)
    reset = _Reset.read(server_dir)
    if reset is None:
        raise StartOverError(reset_unreadable(server_dir / RESET_FILE))
    try:
        return _prepare_staging(server_dir, reset), reset.id
    except OSError as exc:
        raise StartOverError(staging_unmade(server_dir, exc)) from exc


def staging_checkout(staging: Path) -> Path:
    """`<staging>/checkout`: the destination a clone into a staging folder is given (T830 M1)."""
    return Path(staging) / STAGING_CHECKOUT


def _prepare_staging(server_dir: Path, reset: _Reset) -> Path:
    if not reset.id:
        reset.id = secrets.token_hex(8)
        reset.save()
    recorded = Path(reset.staging) if reset.staging else None
    if recorded is None:
        folder = _new_staging(server_dir)
        try:
            _write_staging_marker(folder, server_dir, reset.id)
            reset.staging = str(folder)
            reset.save()
        except OSError:
            # Ours, made a moment ago and named nowhere yet: a folder no record points at is
            # one nothing would ever take away.
            with contextlib.suppress(OSError):
                rmtree.remove_tree(folder)
            raise
        return folder
    if os.path.lexists(recorded):
        if not _staged_here(recorded, server_dir, reset.id):
            raise StartOverError(not_yulons_folder(recorded))
        # The marker says it is this reset's to empty: a clone that died in the checkout
        # (`staging_checkout()`) leaves a half one behind, and the seam would wipe it anyway.
        rmtree.remove_tree(recorded)
    recorded.mkdir(mode=0o700)
    _write_staging_marker(recorded, server_dir, reset.id)
    return recorded


def remove_staging(folder: Path, server_dir: Path, reset_id: str) -> bool:
    """Remove `folder` only while its marker says this server's reset made it (T830 R2).

    Answers whether it was removed. A folder that is not this reset's is left exactly as it
    is, contents and all: that is the whole of the ownership rule (the finally of a failed
    move goes through here too).
    """
    if not _staged_here(folder, server_dir, reset_id):
        return False
    rmtree.remove_tree(folder)
    return True


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
    "damaged_copy",
    "empty_the_folder",
    "half_removed_copy_failed",
    "not_yulons_folder",
    "prepare_staging",
    "remove_staging",
    "running_load_refusal",
    "staging_checkout",
    "staging_unmade",
]
