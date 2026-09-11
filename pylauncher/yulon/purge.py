"""Uninstall one install: its folder, its Docker project, its record (8.9a).

The design of record is `pyplan/phase8-decisions.md` (2026-08-31). One action per
install, scoped to exactly what Yu'lon created — **the server folder, that
install's compose project, and its entry in `state.json`** — and reaching
nothing else on the machine. An unticked "Keep my characters" decides whether
the database volume survives.

Split in two the way that page asks for, because the dialog must show real
numbers before anything is touched:

* `Uninstaller.plan()` resolves the project, lists its containers and volumes,
  measures the folder and reports what it could not determine. It **reads
  only**, and a refusal comes back as `PurgePlan.refusal` rather than as an
  exception, because a dialog has to show it.
* `Uninstaller.run()` executes, and raises `PurgeError` rather than doing half
  of it.

**The order is the specification, not an implementation detail.** The log
snapshot is taken FIRST and the record is forgotten LAST:

* first, because `remove_staged()` takes the container with it and the
  container's log with that — and because `logsnap.capture()` resolves the
  container through `compose ps` in the server dir, so it can only answer while
  the folder and its compose files are still there. So can
  `docker.install_project()`, which on a real install falls back to
  `compose config` in that directory. The project name is therefore resolved
  and captured into a variable before anything on disk is removed.
* last, because a failed uninstall must leave the record pointing at a server
  that is still there — the recoverable failure — rather than leave a folder no
  surface can reach. A record forgotten early makes an install nobody owns, and
  that is precisely the state the ownership refusal cannot recover from.

**Ownership is proved before anything is removed, and OWNED is the only value
that authorises a delete.** `UNKNOWN` refuses because it is the case the app
knows least about. `UNCLAIMED` refuses too, and this is the half that is easy
to get wrong: `state.json` records whatever folder "Use existing…" was pointed
at, so a user who adopted their own hand-built AzerothCore tree has a record
naming a folder Yu'lon never created and that carries no `.yulon-install.json`.
Written as `if claim is UNKNOWN: refuse`, that user loses their server. The two
refusals are worded differently because the advice differs.

**Every command is scoped to the compose project, and every object is
discovered rather than computed.** AzerothCore pins container names GLOBALLY
(`ac-worldserver`, not `<project>-worldserver`), so two installs of one game
share them: anything that went by name would tear down the neighbour's running
server. Volumes are listed by the project label and removed by the names Docker
gave back — not by sanitising a folder basename, which is how the bash prior
art (`guides/uninstall.sh:136`) came to hardcode
`wow-server-playerbots_ac-database`.

**Images are removed one ref at a time, never with a compose flag.** Measured
on yulon-ubuntu 2026-09-08: this project's images are the four
`yulon.local/ac-wotlk-*:native-<id>` builds plus `mysql:8.4`, which a second
WotLK install also uses. `--rmi all` would take the shared one; `--rmi local`
removes only untagged images and would take none of ours. So
`composegen.built_image_refs()`'s four refs are removed explicitly, and an
image another title is holding comes back as a warning rather than as a failed
uninstall. Nothing here prunes: `image prune`, `builder prune` and
`system prune` reach the whole daemon, and `alpine/git` — the sha256-pinned
image every install's clone stage uses — shows up untagged, which is to say it
looks exactly like something a prune should take.

**A ticked box keeps the password too, or keeps nothing.** The volume it keeps
was created with a password that, on every `generated` entry, lives in a file
inside the folder this action deletes — so "keep my characters" kept a database
nothing could open (the 8.9b lane, reading this module, 2026-09-08).
`yulon.dbsecret` holds that argument and the copy; here it is two lines in
`run()`, placed with the refusals and BEFORE the first destructive step,
because a copy that could not be made has to stop the press rather than be
discovered afterwards.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from yulon import dbsecret, docker, logsnap, platform
from yulon.catalog import composegen
from yulon.log import get_logger
from yulon.ownership import Ownership

logger = get_logger(__name__)


class PurgeError(RuntimeError):
    """An uninstall refused, or could not be finished. The message is user-readable."""


DB_VOLUME_SUFFIX = "_db-data"
"""How this install's character volume is spelled: `<compose project>_db-data`.

`db-data:` is declared in the family's base template with no `external:`, so
compose owns it and prefixes it with the project. Measured on yulon-ubuntu
2026-09-08 as `yulon-wow-wotlk-243c46e3_db-data` (2.848 GB, mounted at
`/var/lib/mysql` in `ac-database`), beside a second volume `_client-data`
(3.231 GB) that holds no player data at all.

It is a SUFFIX match over the names Docker listed for this project, not a name
this module builds and hands to `docker volume rm`. A computed name that misses
leaks the volume; a computed name that hits the wrong one deletes a database.
"""

CLIENT_VOLUME_SUFFIX = "_client-data"
"""The extracted client data (maps, vmaps, mmaps, dbc): `<compose project>_client-data`.

Declared next to `db-data:` in the base template, so compose names it the same
way. Measured at 3.2 GB on yulon-ubuntu 2026-09-08. Whether a ticked purge keeps
it was never decided by the design; the owner decided it on 2026-09-08 (answer
3, `pyplan/phase8-owner-answers-2026-09-08.md`): a TICKED purge keeps it, so
the reinstall that keeps the characters does not re-download 3.2 GB of maps;
an UNTICKED purge removes it, because "remove everything" that leaves 3.2 GB
behind is the surprise nobody wants. Until then the ticked press removed it
(8.9a's gate log, `stage1.log:102`).
"""


LEFT_BEHIND = (
    "your backups — an uninstall that deleted them would make "
    '"Keep my characters" pointless in the one case it matters',
    "your WoW client folder, including any files a module put into it",
    "firewall rules, port forwards and anything else on the host",
    "Docker, WSL, and every other dependency Yu'lon provisioned",
    "another install of this game in another folder — different path, "
    "different install id, different project",
    "this install's saved log snapshots, its stored server credential, and — "
    "if you keep your characters — the copy of the database password that "
    "opens the volume they are in, all under Yu'lon's own config directory",
)
"""What this action does not reach, in the words the dialog shows.

Owner answer 1 (2026-08-31) is a list of exclusions, and a destructive action
has to be able to state its whole blast radius before it is confirmed — which
is the one idea worth taking from `Uninstall-DML.ps1:120-130`.

The last line is the one no document decided. The credential file
(`credentials/<game>-<install_id>.json`) and the log snapshots are keyed by the
same path-derived install id, so a reinstall to the same folder inherits both.
After a TICKED purge that is right — same database, same account. After an
unticked one the credential is a secret for a database that no longer exists.
Removing it is not done here because owner answer 1 says "only the launcher's
own state record", and this module does not widen an owner's answer on its own;
it is named to the user instead, and named again in the gate plan as a question
for the owner.

The kept database password (`dbsecret`) is the third thing under that directory
and the only one this action WRITES. It is named in the same line rather than a
new one because a user reading the dialog is being told one thing — that a
directory of Yu'lon's own is not part of the blast radius — and because it is
written only on the ticked path, which is the path whose whole purpose is that
something survives.
"""


@dataclass(frozen=True)
class PurgePlan:
    """What an uninstall would remove, or why it will not run. Reads only.

    `refusal` non-empty is the whole answer: every other field is empty then,
    because nothing was resolved. A dialog shows one or the other and never has
    to decide which of two half-answers to believe — the same shape
    `logsnap.Snapshot` uses.
    """

    game: str
    server_dir: Path
    refusal: str = ""
    project: str = ""
    containers: tuple[str, ...] = ()
    volumes: tuple[str, ...] = ()
    character_volume: str | None = None
    client_volume: str | None = None
    images: tuple[str, ...] = ()
    folder_bytes: int = 0
    left_behind: tuple[str, ...] = LEFT_BEHIND
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class PurgeReport:
    """What an uninstall actually did."""

    snapshot: logsnap.Snapshot = field(default_factory=logsnap.Snapshot)
    secret_kept: Path | None = None
    """Where the kept volume's password was copied to, on a ticked purge that needed one.

    `None` on every unticked purge and on a `fixed` entry, which are the two
    cases where nothing had to be copied — not "it failed", because a copy that
    could not be made raises instead.
    """
    removed_containers: bool = False
    removed_volumes: tuple[str, ...] = ()
    kept_volumes: tuple[str, ...] = ()
    removed_images: tuple[str, ...] = ()
    folder_removed: bool = False
    record_forgotten: bool = False
    warnings: tuple[str, ...] = ()


def refusal_for(
    ownership: Ownership, server_dir: Path, reason: str = "", *, wsl_distro: str | None = None
) -> str:
    """The refusal an ownership answer earns, or `""` for the one that authorises.

    Three answers, three outcomes, and only `OWNED` is permission. The two
    refusals are deliberately not one sentence with a variable in it: `UNKNOWN`
    means a record is there and could not be read, and the honest advice is to
    look at it; `UNCLAIMED` means there is no record at all, and the honest
    advice is that this folder is not Yu'lon's to delete. A user shown the same
    words for both cannot act on either.

    `reason` carries the one `UNKNOWN` that is not damage — a record written by
    a NEWER build (`catalog.native.read_claim()`). The generic advice would tell
    that user to delete a working install's record.

    `UNCLAIMED` also covers a `server_dir` that does not exist at all — reading
    a record out of a folder that is not there answers exactly like reading one
    out of a folder that never had one (T34). That case alone earns a fourth
    sentence pointing at the way out this refusal itself cannot offer — unless
    `wsl_distro` names one, because the Forget control this points at is itself
    hidden for a distro install (`server_dir` there is a path on THIS process,
    not inside the distro, so `folder_is_gone()` cannot answer for it either;
    review, T34 round 2). Defaulted so every caller before this one is
    unchanged.
    """
    if ownership is Ownership.OWNED:
        return ""
    if ownership is Ownership.UNKNOWN:
        detail = f" {reason}" if reason else ""
        return (
            f"There is an install record in {server_dir} that Yu'lon cannot read, so it "
            f"cannot prove this install is its own.{detail} Nothing was removed."
        )
    sentence = (
        f"Nothing here says Yu'lon installed it: there is no install record in "
        f"{server_dir}, so this folder is not Yu'lon's to delete. Nothing was removed."
    )
    if wsl_distro is None and platform.folder_is_gone(server_dir):
        sentence += ' If the folder is gone for good, "Forget this install…" drops this tab.'
    return sentence


class Uninstaller:
    """One install's uninstall, with every reach into the world as a seam.

    Bound to one install rather than taking it per call, because every seam
    needs the same three facts (the spec, the folder, the distro) and a
    free function would thread them through eight signatures.

    The seams default to the real implementations and are what the tests
    replace, in the shape `Applier.__init__` established: a `None` means "build
    the production default", never "do nothing".
    """

    def __init__(
        self,
        *,
        game: str,
        server_dir: Path,
        spec: docker.ContainerSpec,
        image_refs: Sequence[str],
        forget: Callable[[], None],
        logs_dir: Path | None = None,
        wsl_distro: str | None = None,
        claim: Callable[[Path], Ownership] | None = None,
        reason_of: Callable[[Path], str] | None = None,
        project_of: Callable[[], str | None] | None = None,
        census: Callable[[str], docker.Running] | None = None,
        containers_of: Callable[[str], list[str] | None] | None = None,
        volumes_of: Callable[[str], list[str] | None] | None = None,
        folder_size: Callable[[Path], int] | None = None,
        db_secret: Callable[[], dbsecret.AtRisk] | None = None,
        keep_secret: Callable[[str, str], Path] | None = None,
        snapshot: Callable[[], logsnap.Snapshot] | None = None,
        remove_containers: Callable[[], bool] | None = None,
        remove_volume: Callable[[str], None] | None = None,
        remove_image: Callable[[str], str] | None = None,
        remove_folder: Callable[[Path], None] | None = None,
    ) -> None:
        self.game = game
        self.server_dir = server_dir
        self.spec = spec
        self.image_refs = tuple(image_refs)
        self.wsl_distro = wsl_distro
        self.forget = forget
        self._logs_dir = logs_dir
        self._claim = claim if claim is not None else _default_claim
        self._reason_of = reason_of if reason_of is not None else _default_reason
        self._project_of = project_of if project_of is not None else self._real_project
        self._census = census if census is not None else self._real_census
        self._containers_of = containers_of if containers_of is not None else self._real_containers
        self._volumes_of = volumes_of if volumes_of is not None else self._real_volumes
        self._folder_size = folder_size if folder_size is not None else folder_bytes
        self._db_secret = db_secret if db_secret is not None else self._real_db_secret
        self._keep_secret = keep_secret if keep_secret is not None else self._real_keep_secret
        self._snapshot = snapshot if snapshot is not None else self._real_snapshot
        self._remove_containers = (
            remove_containers if remove_containers is not None else self._real_remove_containers
        )
        self._remove_volume = remove_volume if remove_volume is not None else self._real_remove_vol
        self._remove_image = remove_image if remove_image is not None else self._real_remove_image
        self._remove_folder = remove_folder if remove_folder is not None else remove_tree

    # -- production defaults ----------------------------------------------

    def _real_project(self) -> str | None:
        return docker.install_project(self.spec, self.server_dir, wsl_distro=self.wsl_distro)

    def _real_census(self, project: str) -> docker.Running:
        return docker.running_census(self.spec, project, wsl_distro=self.wsl_distro)

    def _real_containers(self, project: str) -> list[str] | None:
        return docker.project_containers(project, wsl_distro=self.wsl_distro)

    def _real_volumes(self, project: str) -> list[str] | None:
        return docker.project_volumes(project, wsl_distro=self.wsl_distro)

    def _real_db_secret(self) -> dbsecret.AtRisk:
        """What this install's password plan says, looked up by game id.

        The catalog rather than a `CatalogEntry` field on this class, so that
        every existing construction of an `Uninstaller` gets the check without
        being rewritten — including the two the UI builds and the ones a future
        family will build. A game id the catalog does not know is reported as a
        problem and not as "nothing at risk": an uninstall that cannot find out
        what it would destroy must not tick the box on the user's behalf.
        """
        from yulon.catalog.catalog import load_catalog

        try:
            entry = load_catalog().get(self.game)
        except (OSError, ValueError, KeyError) as exc:
            return dbsecret.AtRisk(
                problem=f"Yu'lon could not read what game {self.game} keeps its password in ({exc})"
            )
        return dbsecret.at_risk(entry.install, self.server_dir)

    def _real_keep_secret(self, password: str, volume: str) -> Path:
        """Copy the password out of the folder, keyed the way a reinstall will ask."""
        return dbsecret.remember(
            self.game,
            composegen.install_id(self.server_dir),
            password=password,
            volume=volume,
        )

    def _real_snapshot(self) -> logsnap.Snapshot:
        logs = self._logs_dir if self._logs_dir is not None else platform.config_dir() / "logs"
        return logsnap.capture(
            self.spec,
            self.server_dir,
            game=self.game,
            logs_dir=logs,
            wsl_distro=self.wsl_distro,
        )

    def _real_remove_containers(self) -> bool:
        return docker.remove_staged(self.spec, self.server_dir, wsl_distro=self.wsl_distro)

    def _real_remove_vol(self, name: str) -> None:
        docker.remove_volume(name, wsl_distro=self.wsl_distro)

    def _real_remove_image(self, ref: str) -> str:
        return docker.remove_image(ref, wsl_distro=self.wsl_distro)

    # -- reading -----------------------------------------------------------

    def plan(self) -> PurgePlan:
        """What would be removed, and what could not be determined. Writes nothing."""
        targets, refusal = self._resolve()
        if targets is None:
            return PurgePlan(game=self.game, server_dir=self.server_dir, refusal=refusal)
        return PurgePlan(
            game=self.game,
            server_dir=self.server_dir,
            project=targets.project,
            containers=targets.containers,
            volumes=targets.volumes,
            character_volume=targets.character_volume,
            client_volume=targets.client_volume,
            images=self.image_refs,
            folder_bytes=self._folder_size(self.server_dir),
            problems=targets.problems,
        )

    def _resolve(self) -> tuple[_Targets | None, str]:
        """Every refusal, in the order that costs least and proves most first.

        Ownership before anything, because it is the only question whose wrong
        answer deletes a stranger's tree. The project before the census,
        because the census is scoped to it. The census before the listings,
        because a running server is a refusal and not a thing to enumerate.
        """
        ownership = self._claim(self.server_dir)
        refusal = refusal_for(
            ownership,
            self.server_dir,
            self._reason_of(self.server_dir),
            wsl_distro=self.wsl_distro,
        )
        if refusal:
            return None, refusal
        if self.wsl_distro:
            # The folder lives inside the distro and cannot be removed from
            # here. Refused whole rather than done in part: a purge that took
            # the Docker objects and left the folder would leave an install
            # whose next purge has nothing left to prove ownership from.
            return None, (
                f"This server lives inside the WSL distro {self.wsl_distro}, and Yu'lon "
                f"cannot yet delete a folder inside a distro. Nothing was removed."
            )
        project = self._project_of()
        if project is None:
            return None, (
                f"Yu'lon cannot tell which Docker project belongs to the install in "
                f"{self.server_dir}, so it will not remove anything. Nothing was removed."
            )
        running = self._census(project)
        if running.unreadable:
            return None, (
                f"Docker would not say which project owns {', '.join(running.unreadable)}, so "
                f"this install in {self.server_dir} cannot prove those containers are its own. "
                f"Nothing was removed."
            )
        if running.strangers:
            owners = ", ".join(f"{name} belongs to {owner}" for name, owner in running.strangers)
            return None, (
                f"{owners} — not to {project}. Yu'lon will not remove containers it cannot "
                f"prove are its own. Nothing was removed."
            )
        if running.ours:
            return None, (
                f"{', '.join(running.ours)}: still running. Stop the server first, then "
                f"uninstall. Nothing was removed."
            )
        containers = self._containers_of(project)
        if containers is None:
            return None, (
                f"Yu'lon could not ask Docker which containers {project} has, so nothing "
                f"was removed."
            )
        volumes = self._volumes_of(project)
        if volumes is None:
            return None, (
                f"Yu'lon could not ask Docker which volumes {project} has, so nothing was "
                f"removed."
            )
        character = next((v for v in volumes if v.endswith(DB_VOLUME_SUFFIX)), None)
        client = next((v for v in volumes if v.endswith(CLIENT_VOLUME_SUFFIX)), None)
        return (
            _Targets(
                project=project,
                containers=tuple(containers),
                volumes=tuple(volumes),
                character_volume=character,
                client_volume=client,
                problems=() if containers else ("this install has no containers left",),
            ),
            "",
        )

    # -- running -----------------------------------------------------------

    def run(self, *, keep_characters: bool) -> PurgeReport:
        """Remove this install. Raises `PurgeError` rather than doing half of it.

        Every refusal is re-asked here rather than taken from the plan the
        dialog showed: a plan is a photograph, and the server may have been
        started between the photograph and the press.
        """
        targets, refusal = self._resolve()
        if targets is None:
            raise PurgeError(refusal)
        if keep_characters and targets.character_volume is None:
            raise PurgeError(
                f"Yu'lon cannot identify this install's database volume — nothing named "
                f"{targets.project}{DB_VOLUME_SUFFIX} exists — so it will not promise to keep "
                f"the characters. Nothing was removed."
            )
        secret_kept = self._keep_the_password(targets, keep_characters=keep_characters)

        # --- everything below this line changes the machine ---------------
        snapshot = self._snapshot()
        if snapshot.problem:
            logger.warning(f"uninstall: no log snapshot ({snapshot.problem}); going ahead anyway")

        removed_containers = self._remove_containers()

        kept: list[str] = []
        removed_volumes: list[str] = []
        for name in targets.volumes:
            if keep_characters and name in (targets.character_volume, targets.client_volume):
                kept.append(name)
                continue
            self._remove_volume(name)
            removed_volumes.append(name)

        warnings: list[str] = []
        removed_images: list[str] = []
        for ref in self.image_refs:
            problem = self._remove_image(ref)
            if problem:
                warnings.append(f"{ref} was left behind: {problem}")
            else:
                removed_images.append(ref)

        self._remove_folder(self.server_dir)

        # LAST. A failure above leaves the record pointing at a server that is
        # still there, which is the failure a user can recover from.
        forgotten = True
        try:
            self.forget()
        except OSError as exc:
            forgotten = False
            warnings.append(
                f"the install was removed, but state.json could not be updated ({exc}), so "
                f"Yu'lon may still offer this server the next time it starts."
            )
        return PurgeReport(
            snapshot=snapshot,
            secret_kept=secret_kept,
            removed_containers=removed_containers,
            removed_volumes=tuple(removed_volumes),
            kept_volumes=tuple(kept),
            removed_images=tuple(removed_images),
            folder_removed=True,
            record_forgotten=forgotten,
            warnings=tuple(warnings),
        )

    def _keep_the_password(self, targets: _Targets, *, keep_characters: bool) -> Path | None:
        """Copy this install's database password out of the folder, or refuse the press.

        Called from `run()` among the refusals and ABOVE the line where the
        machine starts changing, which is where the argument for it lives:

        * a copy has to exist before the only other copy is deleted, and this
          is the last moment at which nothing has been lost if it cannot be
          made;
        * what it writes is one file in Yu'lon's own config directory, so a
          copy left beside an install that is still there — because a later
          step refused — costs nothing and opens the same volume it always did.

        Unticked returns `None` without asking anything: the volume is going
        with the folder, so nothing has to survive either.

        The refusal is deliberately not a warning. A ticked box that kept a
        volume whose only key had already been lost would leave characters in a
        database that cannot be opened again, and reporting that afterwards
        does not undo it — the user still has the folder at this point, and the
        file may still be recoverable from it.
        """
        if not keep_characters:
            return None
        at_risk = self._db_secret()
        if at_risk.problem:
            raise PurgeError(
                f'"Keep my characters" was ticked, but Yu\'lon cannot read the password this '
                f"install's database was created with ({at_risk.problem}). Keeping "
                f"{targets.character_volume} without it would keep characters that nothing can "
                f"open again, so nothing was removed. Put that file back, or untick the box to "
                f"remove the database along with the rest."
            )
        if not at_risk.password:
            # `fixed`: the password is in the catalog, so deleting the folder
            # does not lose it and there is nothing here to keep.
            return None
        assert targets.character_volume is not None  # `run()` refused None above
        try:
            return self._keep_secret(at_risk.password, targets.character_volume)
        except OSError as exc:
            raise PurgeError(
                f'"Keep my characters" was ticked, but Yu\'lon could not keep a copy of the '
                f"password that opens {targets.character_volume} ({exc}) — and this uninstall "
                f"deletes the folder holding the only other copy. Nothing was removed."
            ) from exc


@dataclass(frozen=True)
class _Targets:
    """What `_resolve()` proved, once it has proved all of it."""

    project: str
    containers: tuple[str, ...]
    volumes: tuple[str, ...]
    character_volume: str | None
    client_volume: str | None
    problems: tuple[str, ...] = ()


def _default_claim(server_dir: Path) -> Ownership:
    """`apply.server_dir_claim()`, imported here rather than at module scope.

    Same reason `apply` imports `catalog.native` inside its function: this
    module is reached from the UI, and naming the applier at module scope would
    drag the module engine in behind it for one JSON file at the server dir.
    """
    from yulon.apply import server_dir_claim

    return server_dir_claim(server_dir)


def _default_reason(server_dir: Path) -> str:
    """The `UNKNOWN` that is not damage, when there is one."""
    from yulon.catalog import native

    return native.read_claim(server_dir, valid=()).reason


def folder_bytes(path: Path) -> int:
    """How big the server folder is, for the dialog. Never raises.

    A recursive walk, and the decisions doc's open item 3 asks whether that is
    fast enough for a 40-50 GB checkout. Measured on yulon-ubuntu 2026-09-08 the
    real install is 2.3 GB, of which 1.3 GB is `.git`; the walk is over inode
    metadata rather than content, so the cost is the file COUNT rather than the
    size. It is called from `plan()`, which already runs off the GUI thread.

    Errors are swallowed by `os.walk`'s default and by the guard below: a folder
    whose size cannot be measured still has to be offered for removal, and a
    number that is short is better than a dialog that will not open.
    """
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except OSError:
                continue
    return total


def remove_tree(path: Path) -> None:
    """Delete a directory tree, or say why it could not be. Never silently partial.

    The plain `shutil.rmtree` first, because on Linux that is the whole story
    and a pre-emptive `chmod` pass over an AzerothCore checkout is a walk of
    every inode in a 1.3 GB `.git` for nothing.

    The retry is for Windows, where git writes packs and loose objects
    read-only: `shutil.rmtree` stops on the first of them and leaves a
    half-deleted checkout, which is worse than an undeleted one — the
    `.yulon-install.json` may already be gone, so the NEXT purge answers
    UNCLAIMED and refuses forever. `_clear_read_only()` clears the bit and the
    delete is tried again.

    And if it still cannot finish, it RAISES. The Rust prior art's
    `remove_title_fs()` is `let _ = std::fs::remove_dir_all(...)` on every
    branch, and Rust's `remove_dir_all` does not clear
    `FILE_ATTRIBUTE_READONLY` — so on Windows that launcher reports a
    successful uninstall having deleted nothing. That is the failure this
    function exists to make impossible: the message names the path and says
    what to look at.

    The Windows half is unproven from Linux. The retry is exercised here through
    the POSIX shape of the same stop (a directory with its write bit cleared),
    and the Windows spelling is owed a press on the gate box.
    """
    try:
        shutil.rmtree(path)
    except OSError:
        logger.info(f"{path} would not delete; clearing read-only flags and retrying")
        _clear_read_only(path)
        _remove_unenterable(path)
        try:
            shutil.rmtree(path)
        except OSError as exc:
            raise PurgeError(_undeletable(path, exc)) from exc
    if path.exists():
        raise PurgeError(_undeletable(path, "it is still there afterwards"))


def _remove_unenterable(path: Path) -> None:
    """`os.rmdir` every directory entry under `path` that a walk cannot enter.

    The second stop the Windows press found, after the read-only one -- measured
    on `yulon-win11` 2026-09-08 on a real WotLK install
    (`pyplan/gates/8.9a-wotlk-yulon-win11-2026-09-08/`): AzerothCore's clone
    stage runs git inside a Linux container with the server dir bind-mounted,
    so every symlink in the repository lands on NTFS as an
    `IO_REPARSE_TAG_LX_SYMLINK` reparse point (`0xa000001d`). Python does not
    know that tag: `entry.is_symlink()` answers False and
    `entry.is_dir(follow_symlinks=False)` answers True, so `shutil.rmtree`
    recurses into it and dies with `[WinError 1920] The file cannot be accessed
    by the system` -- on both presses, with the containers, volumes and images
    already gone and the folder stuck at 12,405 entries. `_clear_read_only()`
    cannot help; the entry is not read-only, it is unreadable.

    What the box answered when asked (`probe_lxsymlink.py`): `os.rmdir` removes
    the reparse point itself, and with it gone `rmtree` walks the rest. The
    POSIX shape of the same stop is a directory whose mode refuses `scandir`,
    which `os.rmdir` also takes when it is empty; a non-empty one it cannot
    take is left for the retry to name. Never follows anything: `rmdir` on a
    link removes the link, and that is the property that makes this safe to
    run over a checkout that may point outside itself.
    """
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except OSError:
            if current == path:
                return
            try:
                os.rmdir(current)
                logger.info(f"removed an entry the walk could not enter: {current}")
            except OSError as exc:
                logger.info(f"could not remove {current}, leaving it for the retry: {exc}")
            continue
        for entry in children:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                stack.append(Path(entry.path))


def _undeletable(path: Path, problem: object) -> str:
    """The sentence shown when the folder is the step that failed.

    It used to end "Nothing else was changed and this install is still
    recorded" — and by the time this is reached, `run()` has already taken the
    snapshot, removed the containers, removed every selected volume (the
    database one included, on a purge that was not ticked to keep it) and
    removed the images. So the reassuring half was false in every case where
    the sentence could be shown, at the one moment a person most needs to know
    what state their machine is in (review, 2026-09-08).

    What is still true, and is what the sentence now says: the record is
    forgotten last, so it is still there, and a second press resumes from a
    folder that may itself be half-deleted rather than from the beginning.
    """
    return (
        f"{path} could not be deleted ({problem}). On Windows this is usually a read-only "
        f"file inside .git, or a program holding a file open in that folder. "
        f"EVERYTHING ELSE IN THE PLAN HAS ALREADY BEEN REMOVED — the containers, the "
        f"volumes it named, and the images. Only the folder is left, and it may be "
        f"partly deleted. This install is still recorded, so the uninstall can be run "
        f"again once the folder can be removed; it will not put back what is gone."
    )


def _clear_read_only(path: Path) -> None:
    """Add the write bit back to everything under `path`, top down.

    Directories first and then their contents, because on POSIX it is the
    DIRECTORY's write bit that refuses the unlink, and on Windows it is the
    FILE's read-only attribute that refuses the delete. Clearing both is one
    walk, and a failure on any single entry is left for the retry to report
    against the tree rather than raised against one file.
    """
    for root, dirs, files in os.walk(path):
        for name in (*dirs, *files):
            target = Path(root) / name
            try:
                os.chmod(target, target.lstat().st_mode | stat.S_IWRITE)
            except OSError:
                continue
    try:
        os.chmod(path, path.lstat().st_mode | stat.S_IWRITE)
    except OSError:
        pass
