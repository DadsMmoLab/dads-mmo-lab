"""Put a server's own settings files back to how Yu'lon installed them (T94).

**What "default" means here**, because the obvious answer is wrong. For TBC,
Vanilla and Tortoise the install writes the database logins, the world port,
SOAP on (T87), `Ra.Enable = 0`, the 500-bot population, the auction-house bot
and Tortoise's `Database.AutoUpdate.*` INTO the conf files, from each entry's
conf table (`catalog.json`). A copy of the upstream `.dist` would lock the
server out of its own database, switch the command channel off and drop the
bots to upstream's count. So the default is what a fresh install writes: the
image's template, patched by that same table with that same install's tokens,
through the same `conf.patch()` the install stage uses.

WotLK is the other case. Its install writes no conf keys, so each conf's
`.dist` sibling IS the as-installed default; its real settings -- the bot
population, and SOAP once the channel is switched on -- are container
environment in `docker-compose.override.yml`, which wins over the conf. That
file's default is what the install's compose stage renders (owner decision 4,
2026-09-23: a player broke it editing the timezone and could not log in), plus
the channel's own keys while its press is live.

**Only the server's own files.** A game's set is exactly its install conf
table, or for WotLK `AZEROTHCORE_CORE_FILES` plus the override. A module's own
conf file is never touched (the owner's decision); `reset()` refuses a file
outside the set as a caller bug. Inside a core conf, the keys an installed
module declares (the Tuning tab's own rows) keep their current value (owner
decision 5, `carry_module_keys()`).

Nothing here imports Qt. The Tuning tab (`ui/controller_view.py`) draws the
button and runs `reset()` on its job runner.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from yulon import channel_setup, dbsecret, docker, platform, resources, tuning
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families import conf
from yulon.catalog.families.cmangos import ETC_DIR, CmangosInstaller
from yulon.catalog.installer import InstallerError, installer_for
from yulon.catalog.native import Secrets
from yulon.log import get_logger

logger = get_logger(__name__)

AZEROTHCORE_ETC = "env/dist/etc"
"""Where an AzerothCore install's confs live, bound into its containers."""

AZEROTHCORE_CORE_FILES: tuple[str, ...] = (
    f"{AZEROTHCORE_ETC}/worldserver.conf",
    f"{AZEROTHCORE_ETC}/authserver.conf",
    f"{AZEROTHCORE_ETC}/modules/playerbots.conf",
)
"""WotLK's own CONF files. Was `controller_view.TUNING_CORE_FILES`; that name is now this tuple.

Here and not in the view so this module needs no Qt, and so the Tuning tab's
read-only list and the reset's conf set are one tuple. The override is NOT in
it: this tuple is also the raw editor's file list (`_tuning_files`), and the
override is not a file that editor shows. `core_files()` adds it.
"""


def core_files(entry: CatalogEntry) -> tuple[str, ...]:
    """This game's own settings files, relative to the server folder, in table order.

    A CMaNGOS game's are exactly its install conf table, under `etc/`; WotLK's
    are its three confs and the compose override. Any other family has none,
    and the tab draws no button.
    """
    native_block = entry.install.native
    if native_block is None:
        return ()
    if native_block.family == "azerothcore":
        return (*AZEROTHCORE_CORE_FILES, composegen.OVERRIDE_FILE)
    if native_block.family == "cmangos" and native_block.cmangos is not None:
        return tuple(f"{ETC_DIR}/{name}" for name in native_block.cmangos.conf.files)
    return ()


def label(file: str) -> str:
    """What the menu and the report call a file: its path under the game's etc folder."""
    for prefix in (f"{AZEROTHCORE_ETC}/", f"{ETC_DIR}/"):
        if file.startswith(prefix):
            return file[len(prefix) :]
    return file


IN_WSL = (
    "this server lives inside the WSL distro {distro}, and Yu'lon cannot read its image from "
    "here: it would ask Windows' own Docker. Reset it from inside {distro}, where its Docker is"
)
NO_PASSWORD = (
    "this install's database password could not be read from {file}, and these files carry it. "
    "Without it the reset would lock the server out of its own database"
)
IMAGE_GONE = (
    "the server's image ({image}) is not on this machine any more, and the default files are "
    "read out of it: rebuild the server first, then reset"
)
DOCKER_SILENT = (
    "Docker did not answer, so the server's image could not be read. Start Docker and try again"
)
COPY_FAILED = "the default files could not be copied out of the server's image: {exc}"
NO_TEMPLATE = "the server's image has no {source}/{template} to reset it from"
NO_DIST = "there is no {dist} beside it to reset it from"
UNREADABLE = "{what} could not be read ({exc})"
NOT_UTF8 = "{what} is not UTF-8 text ({exc})"
NO_DEFAULT = "Yu'lon does not know what {game} installs into this file"
OVERRIDE_UNRENDERABLE = "the install's compose settings could not be made again ({exc})"


def _host_bind_label(server_dir: Path) -> str:
    """The `:z`-or-nothing the install's compose stage computes for this folder.

    The same two questions `native.Seams.ask_selinux/ask_fs` ask
    (`native.py:2912-2927`), answered by `platform.bind_label()`, the one place
    allowed to decide what an unknown means.
    """
    return platform.bind_label(
        enforcing=platform.selinux_enforcing(), fs_type=platform.filesystem_type(server_dir)
    )


RESET_TAG = "reset"
"""A reset's backups: `<file>.<stamp>.reset-<press>.bak`."""
UNDO_TAG = "undo"
"""An undo's own backups: `<file>.<stamp>.undo-<press>.bak`, `<press>` being the RESET's."""


def new_press() -> str:
    """A fresh press id: eight hex characters, carried by every backup one press takes.

    What ties one reset's files together on disk (Codex review): stamps alone
    needed a guess at how far apart one press's backups can be, and a slow,
    network-mounted or scanned disk can stretch a press past any such guess.
    """
    return secrets.token_hex(4)


def reset_backup(path: Path, press: str, *, now: datetime | None = None) -> Path:
    """`tuning.backup()`, tagged as press `press`'s reset, so `last_reset_on_disk()` finds it.

    The same function the Tuning tab's saves use, with the tag between the
    stamp and `.bak`, which is what tells this backup from a save's -- and
    this press's from any other's -- once the window that made it has closed.
    """
    return tuning.backup(path, now=now, tag=f"{RESET_TAG}-{press}")


def undo_backup(path: Path, press: str, *, now: datetime | None = None) -> Path:
    """`tuning.backup()`, tagged as the undo of reset `press`: the file as the undo found it.

    Two jobs. Anything tuned into the file after the reset is kept beside it,
    not lost: the report names this backup. For a CMaNGOS conf an installed
    module keys settings in, the raw editor lists the file and its Revert puts
    back the newest backup -- this one; the WotLK confs are read-only there and
    the override and a CMaNGOS conf no module names are not listed, so for
    those a person copies it back by hand. And it is the on-disk record that
    reset `press` WAS undone, which `still_undoable()` reads by the press id --
    not by comparing clocks -- so an undone reset is never offered again after
    a later save changes the file.
    """
    return tuning.backup(path, now=now, tag=f"{UNDO_TAG}-{press}")


_TAGGED = re.compile(r"(\d{8}-\d{6}-\d{6})\.(reset|undo)-([0-9a-f]+)\.bak")


@dataclass(frozen=True)
class _Tagged:
    stamp: str
    kind: str
    press: str


def _tag_of(backup: Path, path: Path) -> _Tagged | None:
    """The press a reset or undo backup of `path` belongs to, from its name; else `None`.

    A save's backup (untagged), a person's own `.bak` and a name from any other
    file all answer `None`.
    """
    head = f"{path.name}."
    if not backup.name.startswith(head):
        return None
    found = _TAGGED.fullmatch(backup.name[len(head) :])
    return _Tagged(*found.groups()) if found else None


RESTORE_TEMP_SUFFIX = ".yulon-restore-tmp"
"""What a file being put back from its backup is called until the rename lands."""


def restore(from_backup: Path, target: Path) -> None:
    """Put a backup back over `target` atomically, for the rollback and Undo. Raises `OSError`.

    Not `tuning.restore()`: that is `shutil.copy2` straight onto the target,
    which truncates it first, so a copy that dies half-way (ENOSPC is the usual
    one) leaves half a conf -- neither the reset text nor the old one, with a
    report saying otherwise. Here the copy goes to a sibling
    (`tuning.private_copy`: owner-only while the bytes land, then the backup's
    mode and times -- the backup carries the original's, so the file keeps its
    own mode) and is `os.replace`d on; a failure removes the sibling and leaves
    `target` exactly as it was.
    """
    tmp = target.with_name(f"{target.name}{RESTORE_TEMP_SUFFIX}")
    try:
        # An interrupted run's leftover first: `private_copy` creates, never reuses.
        tmp.unlink(missing_ok=True)
        tuning.private_copy(from_backup, tmp)
        os.replace(tmp, target)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(f"could not remove {tmp}: {exc}")
        raise


@dataclass(frozen=True)
class Seams:
    """Everything a reset reaches outside itself through. Real by default; filled by tests.

    Import-bound defaults, as `native.Seams`'s are: a test hands in its own
    rather than monkeypatching a module attribute this object already holds.
    """

    copy_from_image: Callable[[str, str, Path], None] = docker.copy_from_image
    image_present: Callable[[Sequence[str]], bool | None] = docker.images_built
    platform_id: Callable[[], str] = platform.detect
    bind_label: Callable[[Path], str] = _host_bind_label
    write: Callable[..., None] = conf.replace_file
    """`(path, text)`, keeping the file's mode; `(path, text, mode=...)` to recreate one."""
    backup: Callable[[Path, str], Path] = reset_backup
    restore: Callable[[Path, Path], None] = restore


Built = tuple[dict[str, str], dict[str, str]]
"""(default text by file, reason by file). Every requested file is in exactly one of them."""


def default_texts(
    entry: CatalogEntry,
    server_dir: Path,
    files: Sequence[str],
    *,
    wsl_distro: str | None = None,
    seams: Seams | None = None,
) -> Built:
    """The as-installed text of each file, in memory. Changes none of the server's own files.

    A CMaNGOS build passes the image's templates through `RESET_STAGING` under
    the server folder and removes it before returning.

    The CMaNGOS texts carry the database password: the caller writes them and
    must never log or show them. A reason is a sentence a player reads.
    """
    seams = seams or Seams()
    native_block = entry.install.native
    family = native_block.family if native_block is not None else None
    if family == "azerothcore":
        confs = [file for file in files if file != composegen.OVERRIDE_FILE]
        texts, reasons = _from_dist(server_dir, confs)
        if composegen.OVERRIDE_FILE in files:
            try:
                texts[composegen.OVERRIDE_FILE] = _override_default(entry, server_dir, seams)
            except (composegen.ComposeGenError, OSError) as exc:
                reasons[composegen.OVERRIDE_FILE] = OVERRIDE_UNRENDERABLE.format(exc=exc)
        return texts, reasons
    if family == "cmangos":
        return _from_image(entry, server_dir, files, wsl_distro=wsl_distro, seams=seams)
    return {}, dict.fromkeys(files, NO_DEFAULT.format(game=entry.name))


def _read_text(path: Path) -> str:
    """The file's exact text: `newline=""`, so a CRLF file comes back CRLF (conf.py:363-384)."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _from_dist(server_dir: Path, files: Sequence[str]) -> Built:
    """WotLK confs: each file's `.dist` sibling, byte for byte. No docker, so no distro question."""
    texts: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for file in files:
        dist = server_dir / f"{file}{conf.DIST_SUFFIX}"
        try:
            texts[file] = _read_text(dist)
        except FileNotFoundError:
            reasons[file] = NO_DIST.format(dist=dist.name)
        except UnicodeDecodeError as exc:
            # Its own arm: a ValueError, which `except OSError` would let escape.
            reasons[file] = NOT_UTF8.format(what=dist.name, exc=exc)
        except OSError as exc:
            reasons[file] = UNREADABLE.format(what=dist.name, exc=exc)
    return texts, reasons


def channel_is_on(server_dir: Path) -> bool:
    """Whether this install's command-channel press is live (spec correction 19).

    `channel_setup.enable()` writes `<override>.before-channel` on the FIRST
    press and only `roll_back()` deletes it (`channel_setup.py:285-289`, `473`,
    `491`), so the file IS the channel's own record that a press stands.
    """
    marker = f"{composegen.OVERRIDE_FILE}{channel_setup.BACKUP_SUFFIX}"
    return (server_dir / marker).is_file()


def _override_default(entry: CatalogEntry, server_dir: Path, seams: Seams) -> str:
    """What the install writes as `docker-compose.override.yml`, plus the channel's env if on.

    The same five inputs as the install's own call (`native.py:5925-5932`). The
    channel layer is the merge `channel_setup._world_env()` makes
    (`channel_setup.py:534-543`), spelled from public parts; the test compares
    the result with the file `channel_setup.enable()` really writes.
    """
    operations = entry.operations
    extra = operations.enable_env if operations is not None and channel_is_on(server_dir) else {}
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        world_env={**composegen.world_env(entry), **extra} if extra else None,
        db_password=entry.install.db_password(server_dir),
        bind_label=seams.bind_label(server_dir),
        platform_id=seams.platform_id,
    )
    return plan.override


def _password(entry: CatalogEntry, server_dir: Path, seams: Seams) -> str | None:
    """The install's own password, or `None`. NEVER a new one (spec correction 3).

    The install's rule (`native.py`, `_secrets`), minus its mint: a fixed
    password is the catalog's; a generated one is its file's, and ONLY when
    that file is gone does the copy Yu'lon kept at uninstall stand in for it
    (`dbsecret.recall`). A file that is there but unreadable, not UTF-8 or
    empty answers `None`: the kept copy may be older than the file, and a stale
    password written into the confs locks the server out as surely as a new one.
    """
    plan = entry.install.password
    if plan.mode == "fixed":
        return plan.value
    if not plan.file:
        return None
    path = server_dir / plan.file
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        kept = dbsecret.recall(
            entry.id, composegen.install_id(server_dir, platform_id=seams.platform_id)
        )
        return None if kept is None else kept.password
    except (OSError, ValueError):
        # ValueError: `UnicodeDecodeError`, which `except OSError` would let escape.
        return None
    return text.strip() or None


RESET_STAGING = ".yulon-reset-staging"
"""Where the image's conf dir lands for the length of one reset, under the server folder.

Beside the server's own files and not in the system temp dir: snap-packaged
Docker runs with a private `/tmp`, so a `docker cp` into the host's `/tmp`
lands somewhere this process never sees. The server folder is one Docker
already writes into -- the install's `docker cp` goes to
`etc/.yulon-conf-dist` (`conf._STAGING_DIR`). Not inside `etc/`, because that
folder is the thing being reset. A leading dot and the app's name, so an
interrupted run leaves something a person can recognise and delete; the next
reset clears it first.
"""
STAGING_STUCK = "a folder left by an earlier reset, {path}, could not be cleared ({exc})"


def _clear_staging(path: Path) -> None:
    """Remove the staging entry, whatever an interrupted run left there. Raises `OSError`.

    `docker cp` needs a `dest` that does not exist -- it then makes `dest` the
    folder (`conf.py`, `materialise`); into an existing folder the files would
    land one level down, where no template is ever found. A symlink is unlinked,
    never followed: `rmtree` refuses one, and what it points at is not ours.
    """
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _from_image(
    entry: CatalogEntry,
    server_dir: Path,
    files: Sequence[str],
    *,
    wsl_distro: str | None,
    seams: Seams,
) -> Built:
    """CMaNGOS/Tortoise: the image's template, patched by the install's table and tokens.

    One `docker cp` of the whole source dir into `RESET_STAGING`, as
    `conf.materialise()` does into `etc/` -- but never into `etc/`, because
    that folder is the thing being reset. A file the game's table does not name
    is a `NO_DEFAULT` reason, never a lookup error. Local checks first (table,
    distro, password), then one question to the daemon (is the image here?),
    then the copy: the cheapest refusal is the one that runs.
    """
    engine = installer_for(entry, platform_id=seams.platform_id)
    if not isinstance(engine, CmangosInstaller):  # the catalog says cmangos; keeps mypy honest
        return {}, dict.fromkeys(files, NO_DEFAULT.format(game=entry.name))
    table = engine.conf_table()
    names: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for file in files:
        name = file.removeprefix(f"{ETC_DIR}/")
        if name != file and name in table.files:
            names[file] = name
        else:
            reasons[file] = NO_DEFAULT.format(game=entry.name)

    def every(reason: str) -> Built:
        return {}, {**reasons, **dict.fromkeys(names, reason)}

    if not names:
        return {}, reasons
    if wsl_distro is not None:
        return every(IN_WSL.format(distro=wsl_distro))
    password = _password(entry, server_dir, seams)
    if password is None:
        return every(NO_PASSWORD.format(file=entry.install.password.file or "its password file"))
    try:
        # Both are the conf stage's own bodies, and both refuse only on a
        # catalog the app itself got wrong. A reason, not a raise: `reset()`
        # catches nothing around this call, and its job runner would show a
        # traceback. Neither sentence carries a token's value.
        image = engine.conf_image_ref(server_dir)
        tokens = engine.conf_tokens(server_dir, Secrets(db_password=password))
    except InstallerError as exc:
        return every(str(exc))
    present = seams.image_present([image])
    if present is False:
        return every(IMAGE_GONE.format(image=image))
    if present is None:
        return every(DOCKER_SILENT)
    source = table.source_dir.rstrip("/")
    staged = server_dir / RESET_STAGING
    try:
        _clear_staging(staged)
    except OSError as exc:
        return every(STAGING_STUCK.format(path=staged, exc=exc))
    texts: dict[str, str] = {}
    try:
        try:
            seams.copy_from_image(image, source, staged)
        except docker.DockerCliMissingError as exc:
            # Ahead of its base class: it carries the one instruction a user can act on.
            return every(str(exc))
        except docker.DockerCommandError as exc:
            return every(COPY_FAILED.format(exc=exc))
        for file, name in names.items():
            patch = table.files[name]
            template = conf.template_of(name, patch)
            try:
                texts[file] = conf.patch(_read_text(staged / template), patch, tokens)
            except FileNotFoundError:
                reasons[file] = NO_TEMPLATE.format(source=source, template=template)
            except UnicodeDecodeError as exc:
                reasons[file] = NOT_UTF8.format(what=f"{source}/{template}", exc=exc)
            except OSError as exc:
                reasons[file] = UNREADABLE.format(what=f"{source}/{template}", exc=exc)
            except InstallerError as exc:
                # `patch()` names the KEY and the token, never a value.
                reasons[file] = str(exc)
    finally:
        try:
            _clear_staging(staged)
        except OSError as exc:
            # The image's own templates, no password: left behind, not fatal.
            logger.warning(f"could not remove {staged}: {exc}")
    return texts, reasons


Outcome = Literal[
    "reset", "recreated", "already", "absent", "foreign", "refused", "held", "restored"
]

WRITE_FAILED = (
    "it could not be written ({exc}). Every file this press had already reset was put back, "
    "so nothing changed"
)
ROLLBACK_FAILED = (
    "it was reset, then a later file failed, and copying {backup} back over it failed too "
    '({exc}). The file as it was is {backup}, beside it, and "Undo the last reset" tries again'
)
UNDO_FAILED = "copying {backup} back over it failed ({exc})"
UNMADE_FAILED = (
    "it was missing and was made again, then a later file failed, and removing it again "
    "failed too ({exc}); it is on disk as Yu'lon installs it"
)
UNDO_UNSAFE = "it could not be backed up first ({exc}), so it was left as it is"


@dataclass(frozen=True)
class FileResult:
    """What happened to one file. Never carries the file's text -- it holds the password."""

    file: str
    outcome: Outcome
    backup: Path | None = None
    reason: str = ""
    before: Path | None = None
    """An undo's own backup of the file as it found it (`undo_backup()`), for a "restored"."""

    def line(self) -> str:
        name = label(self.file)
        kept = self.backup.name if self.backup is not None else ""
        return {
            "reset": f"{name}: back to how Yu'lon installed it. The file as it was is kept "
            f"beside it as {kept}.",
            "recreated": f"{name}: it was missing, so it was made again as Yu'lon installs it. "
            "There was no older copy, so there is nothing to undo.",
            "already": f"{name}: already as Yu'lon installed it, so it was left alone "
            "(no backup made).",
            "absent": f"{name}: not on disk, and Yu'lon's install does not write it, so it was "
            "left absent, as installed.",
            "foreign": f"{name}: this server's compose files were not made by Yu'lon, so it "
            "was left alone.",
            # A refused file WITH a backup was written and could not be put
            # back (a failed rollback or a failed undo): "NOT reset" would be false.
            "refused": f"{name}: NOT {'put back' if self.backup else 'reset'}: {self.reason}.",
            "held": f"{name}: not reset. A reset is all or nothing, and another file could not "
            "be made.",
            "restored": f"{name}: put back from {kept}."
            + (
                f" The file as it was is kept beside it as {self.before.name}."
                if self.before is not None
                else ""
            ),
        }[self.outcome]


@dataclass(frozen=True)
class ResetReport:
    """One press's outcome, file by file, in the order the files were asked for.

    `undo` says which press: an undo's refusal is not a reset's, and says so.
    """

    results: tuple[FileResult, ...]
    undo: bool = False

    @property
    def written(self) -> tuple[FileResult, ...]:
        """The files this reset changed and that still stand changed: what Undo copies back.

        A file a failed rollback could not put back is one of them -- its text
        is the default and its backup is the only record of what it said.
        """
        if self.undo:
            return ()
        return tuple(
            r
            for r in self.results
            if r.outcome == "reset" or (r.outcome == "refused" and r.backup is not None)
        )

    @property
    def changed(self) -> tuple[FileResult, ...]:
        """Every file this press left different on disk: what the restart banner is owed for."""
        return (*self.written, *(r for r in self.results if r.outcome == "recreated"))

    @property
    def refused(self) -> bool:
        return any(r.outcome == "refused" for r in self.results)

    def lines(self) -> tuple[str, ...]:
        outcomes = {r.outcome for r in self.results}
        if self.undo:
            head = (
                "The undo could not put every file back. File by file:"
                if "refused" in outcomes
                else "Put back what the last reset replaced:"
            )
        elif self.written and "refused" in outcomes:
            head = "The reset failed part-way, and not every file could be put back. File by file:"
        elif "refused" in outcomes:
            head = "The reset was not done. File by file:"
        elif outcomes & {"reset", "recreated"}:
            head = "Put back to how Yu'lon installed this server:"
        else:
            head = "Nothing needed resetting:"
        return (head, *(r.line() for r in self.results))


ModuleKeys = Mapping[str, Sequence[str]]
"""Server-relative file -> the keys INSTALLED modules declare in it (owner decision 5)."""


def module_keys(
    rows: Iterable[tuning.TuningRow], files: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    """Which keys installed modules keep in each of `files`, from the Tuning tab's own rows.

    `tuning.rows_for()` is the one answer to "which keys does an installed
    module declare in which file" (it yields rows for installed modules only),
    so this filters its rows rather than reading manifests a second way. Only
    `conf`-backend rows: a Lua or table setting is never inside a core conf.
    Manifest order, once each.
    """
    found: dict[str, list[str]] = {}
    for row in rows:
        if not (row.installed and row.backend == "conf" and row.file in files):
            continue
        keys = found.setdefault(row.file, [])
        if row.key not in keys:
            keys.append(row.key)
    return {file: tuple(keys) for file, keys in found.items()}


def _active_key(line: str) -> str | None:
    """The key an ACTIVE `Key = value` line sets, else `None`.

    `tuning.conf_value()`'s rule (`tuning.py:171-225`), so the line carried is
    the line the Tuning tab reads the current value from: trimmed; an empty,
    `#` or `[` line says nothing; the key is everything before the first `=`.
    """
    body = line.rstrip("\r\n").strip()
    if not body or body[:1] in ("#", "["):
        return None
    head, sep, _ = body.partition("=")
    key = head.strip()
    return key if sep and key else None


def carry_module_keys(default: str, live: str, keys: Sequence[str]) -> str:
    """`default` with each module key's line carried over from `live`, byte for byte.

    Pure (owner decision 5). For each key, the FIRST active line in `live` --
    the one the server reads -- replaces every active line for that key in
    `default`, each keeping the ending of the line it replaces, so a CRLF
    default stays CRLF. A key with no active line in `live` (absent, or only
    commented) leaves `default` as it is. A key `default` has no active line
    for is appended with the file's ending, `conf.patch()`'s absent-key rule
    (`conf.py:182-185`), because a conf the emulator reads with the key missing
    silently takes the compiled default -- which is not what the module set.
    """
    lines = default.splitlines(keepends=True)
    live_lines = live.splitlines(keepends=True)
    for key in keys:
        carried = next(
            (line.rstrip("\r\n") for line in live_lines if _active_key(line) == key), None
        )
        if carried is None:
            continue
        hit = False
        for index, line in enumerate(lines):
            if _active_key(line) == key:
                lines[index] = carried + line[len(line.rstrip("\r\n")) :]
                hit = True
        if not hit:
            newline = "\r\n" if lines and lines[0].endswith("\r\n") else "\n"
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(carried + newline)
    return "".join(lines)


def apply_rule(file: str) -> tuning.ApplyRule:
    """What a reset of `file` owes before the server uses it (spec correction 21).

    The override is container ENVIRONMENT, read when a container is created, so
    it owes a recreate (owner decision 4) -- `tuning.file_rule()` would call it
    read-only and raise no banner. Every conf is priced exactly as the tab
    prices a save of it.
    """
    if file == composegen.OVERRIDE_FILE:
        return "recreate"
    return tuning.file_rule(file)


def install_keys(entry: CatalogEntry, file: str) -> frozenset[str]:
    """The keys this game's install table writes into `file`, casefolded (lead ruling).

    They WIN over decision 5's carry-over: a module that declared, say,
    `WorldServerPort` or `SOAP.Enabled` in `mangosd.conf` must not keep a live
    value the install sets, or a reset could leave the server off its own port
    or its command channel. Casefolded so a spelling that differs only in case
    is dropped too -- dropping more is the safe side. WotLK's confs have no
    table, so none.
    """
    native_block = entry.install.native
    if native_block is None or native_block.cmangos is None:
        return frozenset()
    patch = native_block.cmangos.conf.files.get(file.removeprefix(f"{ETC_DIR}/"))
    if patch is None or not file.startswith(f"{ETC_DIR}/"):
        return frozenset()
    return frozenset(key.casefold() for key in patch.keys)


def _foreign(server_dir: Path, file: str) -> bool:
    """The override of a compose stack Yu'lon did not generate (spec correction 20)."""
    if file != composegen.OVERRIDE_FILE:
        return False
    base = server_dir / composegen.BASE_FILE
    return not (base.is_file() and composegen.is_ours(base))


def install_writes(entry: CatalogEntry, file: str) -> bool:
    """Whether a fresh Yu'lon install writes `file` -- so a missing one is made again.

    A CMaNGOS game's conf table is exactly what its install writes into
    `etc/`, and WotLK's install writes the compose override. WotLK's confs are
    NOT written by the install: a normal install has `playerbots.conf.dist` and
    no `playerbots.conf`, and the worldserver does not load the `.dist`
    (measured, `dbreads.py`, `party.py`), so making one from it would change the
    server rather than put it back (plan correction 8). Such a file stays absent.
    """
    if file == composegen.OVERRIDE_FILE:
        return True
    native_block = entry.install.native
    return (
        native_block is not None and native_block.family == "cmangos" and file in core_files(entry)
    )


def _install_mode(server_dir: Path, file: str) -> int:
    """The mode the install gives `file` when it makes it (for a recreated one).

    A CMaNGOS conf: `conf.CONF_MODE`, as `conf.materialise` sets it. The
    override: the mode of the base `docker-compose.yml` the same install call
    (`composegen.write_plan`, a plain `write_text`) wrote beside it.
    """
    if file == composegen.OVERRIDE_FILE:
        try:
            return stat.S_IMODE((server_dir / composegen.BASE_FILE).stat().st_mode)
        except OSError:
            return conf.CONF_MODE
    return conf.CONF_MODE


@dataclass(frozen=True)
class PressFacts:
    """What the question must name BEFORE a press (Codex review), file by file.

    `foreign`: another tool's override, left alone. `missing`: not on disk and
    made again as the install makes it. `absent`: not on disk and never written
    by the install (a WotLK conf), so it stays as it is.
    """

    foreign: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()

    def among(self, files: Sequence[str]) -> PressFacts:
        """The same facts, for `files` only."""
        return PressFacts(
            tuple(f for f in self.foreign if f in files),
            tuple(f for f in self.missing if f in files),
            tuple(f for f in self.absent if f in files),
        )


def press_facts(entry: CatalogEntry, server_dir: Path, files: Sequence[str]) -> PressFacts:
    """Sort `files` into `PressFacts`: a stat per file and one read of the base compose file.

    Off the GUI thread and fresh: the Tuning tab reads it in a job of its own on
    every press, and asks its question from that answer (a server inside WSL
    answers over 9p). The answer goes into the reset as `confirmed`, which
    refuses if any file's case has changed since.
    """
    foreign = tuple(file for file in files if _foreign(server_dir, file))
    gone = [f for f in files if f not in foreign and not (server_dir / f).is_file()]
    return PressFacts(
        foreign,
        tuple(f for f in gone if install_writes(entry, f)),
        tuple(f for f in gone if not install_writes(entry, f)),
    )


CHANGED_SINCE = (
    "it changed since you were asked ({was}), so nothing was written. Press Reset to default "
    "again to be asked about the files as they are now"
)


def _case(facts: PressFacts, file: str) -> str:
    """How a question built from `facts` described `file`."""
    if file in facts.foreign:
        return "left alone: not made by Yu'lon"
    if file in facts.missing:
        return "not on disk, to be made again"
    if file in facts.absent:
        return "not on disk"
    return "on disk"


def _changed_since(asked: PressFacts, now: PressFacts, files: Sequence[str]) -> dict[str, str]:
    """Each file whose case differs between the question and now, and what it is now."""
    return {
        f: f"asked as {_case(asked, f)}, now {_case(now, f)}"
        for f in files
        if _case(asked, f) != _case(now, f)
    }


def reset(
    entry: CatalogEntry,
    server_dir: Path,
    files: Sequence[str],
    *,
    module_keys: ModuleKeys | None = None,
    confirmed: PressFacts | None = None,
    wsl_distro: str | None = None,
    seams: Seams | None = None,
) -> ResetReport:
    """Put `files` back to their as-installed text: every one, or none.

    Phase one builds every default and reads every current file, and writes
    nothing; one failure there refuses the whole press. Phase two backs each
    changing file up (`reset_backup`, `<file>.<stamp>.reset-<press>.bak` beside
    it, one press id for all of them) and replaces it atomically, keeping its
    mode (`conf.replace_file`); a failure there puts back, from those backups,
    every file this press had already written, and removes any it recreated. A
    file already equal to its default is not touched and gets no backup.
    A file not on disk is MADE AGAIN when the install writes it
    (`install_writes`; Codex review: a deleted core conf left a broken server)
    with the install's mode (`_install_mode`), and its default must build like
    any other or the whole press refuses; one the install does not write stays
    absent. Another tool's override is left alone.

    `module_keys` (owner decision 5): for each file, the keys installed
    modules declare in it, from `module_keys()`. Their live lines are carried
    into the default before anything is compared or written, so a module's
    setting keeps its current value while every core key goes back. A key the
    install table writes into that file is never carried (`install_keys()`). A file
    holding such keys that is not UTF-8 refuses the press: its lines cannot be
    carried without guessing at its bytes.

    Raises:
        ValueError: a file outside this game's set -- a caller bug, and the
            structural reason a module conf can never be reset from here.
    """
    seams = seams or Seams()
    stray = [file for file in files if file not in core_files(entry)]
    if stray:
        raise ValueError(f"not {entry.id}'s own settings files: {', '.join(stray)}")
    skipped: dict[str, Outcome] = {}
    missing: set[str] = set()
    for file in files:
        if _foreign(server_dir, file):
            skipped[file] = "foreign"
        elif not (server_dir / file).is_file():
            if install_writes(entry, file):
                missing.add(file)
            else:
                skipped[file] = "absent"
    if confirmed is not None:
        # Codex (final pass): the player said Yes to a question built from
        # `confirmed`. A file whose case has changed since -- deleted, back on
        # disk, its compose stack now another tool's or Yu'lon's again -- would
        # be reset differently from what they were told, so nothing is written.
        now = PressFacts(
            tuple(f for f in files if skipped.get(f) == "foreign"),
            tuple(f for f in files if f in missing),
            tuple(f for f in files if skipped.get(f) == "absent"),
        )
        moved = _changed_since(confirmed.among(files), now, files)
        if moved:
            return ResetReport(
                tuple(
                    (
                        FileResult(f, "refused", reason=CHANGED_SINCE.format(was=moved[f]))
                        if f in moved
                        else FileResult(f, "held")
                    )
                    for f in files
                )
            )
    present = [file for file in files if file not in skipped]
    texts, reasons = (
        default_texts(entry, server_dir, present, wsl_distro=wsl_distro, seams=seams)
        if present
        else ({}, {})
    )
    current: dict[str, bytes] = {}
    for file in present:
        if file in reasons or file in missing:
            continue
        try:
            current[file] = (server_dir / file).read_bytes()
        except OSError as exc:
            reasons[file] = UNREADABLE.format(what=label(file), exc=exc)
            continue
        owned = install_keys(entry, file)
        carry = [key for key in (module_keys or {}).get(file, ()) if key.casefold() not in owned]
        if carry:
            try:
                live = current[file].decode("utf-8")
            except UnicodeDecodeError as exc:
                reasons[file] = NOT_UTF8.format(what=label(file), exc=exc)
                continue
            texts[file] = carry_module_keys(texts[file], live, carry)
    if reasons:
        return ResetReport(tuple(_before_writing(file, skipped, reasons) for file in files))

    press = new_press()
    done: dict[str, FileResult] = {}
    for file in present:
        path = server_dir / file
        if file in missing:
            try:
                # `modules/tortoise_bots.conf` may have lost its folder too;
                # `materialise` makes the same one.
                path.parent.mkdir(parents=True, exist_ok=True)
                seams.write(path, texts[file], mode=_install_mode(server_dir, file))
            except (OSError, InstallerError) as exc:
                return _rolled_back(files, skipped, done, file, exc, server_dir, seams)
            logger.info(f"recreated {path} as {entry.id} installs it")
            done[file] = FileResult(file, "recreated")
            continue
        if current[file] == texts[file].encode("utf-8"):
            continue
        made: Path | None = None
        try:
            made = seams.backup(path, press)
            seams.write(path, texts[file])
        except (OSError, InstallerError, tuning.TuningError) as exc:
            # The failed file's own backup, if it was taken, goes to the
            # rollback too: it is this press's newest, and left behind it would
            # name this press as "the last reset" (re-review).
            return _rolled_back(files, skipped, done, file, exc, server_dir, seams, made)
        logger.info(f"reset {path} to how {entry.id} installed it; backup {made.name}")
        done[file] = FileResult(file, "reset", made)
    return ResetReport(
        tuple(
            (
                FileResult(file, skipped[file])
                if file in skipped
                else done.get(file) or FileResult(file, "already")
            )
            for file in files
        )
    )


def _before_writing(file: str, skipped: dict[str, Outcome], reasons: dict[str, str]) -> FileResult:
    """A file's line when phase one refused the press: skipped, the refusal, or held."""
    if file in skipped:
        return FileResult(file, skipped[file])
    if file in reasons:
        return FileResult(file, "refused", reason=reasons[file])
    return FileResult(file, "held")


def _rolled_back(
    files: Sequence[str],
    skipped: dict[str, Outcome],
    done: dict[str, FileResult],
    failed: str,
    exc: BaseException,
    server_dir: Path,
    seams: Seams,
    failed_backup: Path | None = None,
) -> ResetReport:
    """A write failed part-way: put every file this press wrote back, and remove any it made.

    When every one of them was put back, the press left nothing standing, so its
    own backups are removed too -- the failed file's included (`failed_backup`,
    taken before the write that failed; `replace_file` is atomic, so that file
    was never changed). Left behind, the newest of them would name this press as
    "the last reset": it hid the press before it from the Undo after a restart
    and, after a later edit, pointed the Undo at this press's backups (re-review).
    When ANY could not be put back, every backup of the press is kept: a backup
    whose restore failed is the only record of what its file said.
    """
    logger.warning(f"reset of {failed} failed ({exc}); putting back {len(done)} file(s)")
    results: dict[str, FileResult] = {}
    for file, item in done.items():
        if item.outcome == "recreated":
            # It was missing before this press: all or nothing means missing again.
            try:
                (server_dir / file).unlink(missing_ok=True)
            except OSError as undo_exc:
                results[file] = FileResult(
                    file, "refused", reason=UNMADE_FAILED.format(exc=undo_exc)
                )
            else:
                results[file] = FileResult(file, "held")
            continue
        assert item.backup is not None
        try:
            seams.restore(item.backup, server_dir / file)
        except OSError as undo_exc:
            results[file] = FileResult(
                file,
                "refused",
                item.backup,
                ROLLBACK_FAILED.format(exc=undo_exc, backup=item.backup.name),
            )
        else:
            results[file] = FileResult(file, "held")
    results[failed] = FileResult(failed, "refused", reason=WRITE_FAILED.format(exc=exc))
    if not any(results[file].outcome == "refused" for file in done):
        backups = [item.backup for item in done.values() if item.backup is not None]
        for backup in (*backups, *([failed_backup] if failed_backup else [])):
            try:
                backup.unlink(missing_ok=True)
            except OSError as unlink_exc:
                logger.warning(f"could not remove {backup}: {unlink_exc}")
    return ResetReport(
        tuple(results.get(file) or FileResult(file, skipped.get(file, "held")) for file in files)
    )


def undo(
    server_dir: Path,
    written: Sequence[FileResult],
    *,
    restore: Callable[[Path, Path], None] = restore,
    backup: Callable[[Path, str], Path] = undo_backup,
) -> ResetReport:
    """Copy back, from the backups a reset made, every file that reset wrote.

    Each file is backed up FIRST (`undo_backup()`, tagged with the reset's own
    press id), so whatever was tuned into it after the reset is kept beside it,
    named in the report; a file that cannot be backed up is left as it is. Then
    a copy (`restore()`, atomic), so the reset's backup survives and a copy
    that fails leaves the file whole.
    """
    results: list[FileResult] = []
    for item in written:
        if item.backup is None:
            continue
        tagged = _tag_of(item.backup, server_dir / item.file)
        try:
            before = backup(server_dir / item.file, tagged.press if tagged else new_press())
        except (OSError, tuning.TuningError) as exc:
            results.append(
                FileResult(item.file, "refused", item.backup, UNDO_UNSAFE.format(exc=exc))
            )
            continue
        try:
            restore(item.backup, server_dir / item.file)
        except OSError as exc:
            # The file was not changed, so the backup just taken equals it and
            # records nothing; left behind, it would read as "this reset was
            # undone" and hide a reset that never was (fix round 2).
            try:
                before.unlink(missing_ok=True)
            except OSError as unlink_exc:
                logger.warning(f"could not remove {before}: {unlink_exc}")
            results.append(
                FileResult(
                    item.file,
                    "refused",
                    item.backup,
                    UNDO_FAILED.format(backup=item.backup.name, exc=exc),
                )
            )
        else:
            logger.info(f"put {item.file} back from {item.backup.name}; kept {before.name}")
            results.append(FileResult(item.file, "restored", item.backup, before=before))
    return ResetReport(tuple(results), undo=True)


def _tagged(path: Path, kind: str) -> list[tuple[_Tagged, Path]]:
    """Every `kind` backup of `path`, oldest first (a name sort is a time sort)."""
    found = []
    for backup in tuning.backups_of(path):
        tag = _tag_of(backup, path)
        if tag is not None and tag.kind == kind:
            found.append((tag, backup))
    return found


def _undoable(server_dir: Path, item: FileResult) -> bool:
    """Whether an Undo of `item` would still put back the reset it records.

    Not when the file already equals the reset's backup (undone, or put back by
    hand). Not when an undo of THAT press (its id, not a clock) has a backup
    beside the file -- the reset was undone, and a later save into the file must
    not re-arm it (fix round 1: the undo would overwrite that tuning) -- UNLESS
    the file equals the newest such undo backup, which means the undo was itself
    reverted: the reset's text stands again.
    """
    if item.backup is None:
        return False
    path = server_dir / item.file
    try:
        current = path.read_bytes()
        if current == item.backup.read_bytes():
            return False
    except OSError:
        # A file gone or unreadable: a reset never deletes one, so this is
        # not a state an undo of it can put right.
        return False
    reset = _tag_of(item.backup, path)
    undone = [b for tag, b in _tagged(path, UNDO_TAG) if reset and tag.press == reset.press]
    if not undone:
        return True
    try:
        return current == undone[-1].read_bytes()
    except OSError:
        return False


def still_undoable(server_dir: Path, items: Sequence[FileResult]) -> tuple[FileResult, ...]:
    """The items an Undo would still change: `_undoable()`'s rule, for session and disk alike."""
    return tuple(item for item in items if _undoable(server_dir, item))


def undo_items(
    entry: CatalogEntry, server_dir: Path, session: Sequence[FileResult]
) -> tuple[FileResult, ...]:
    """What "Undo the last reset…" would put back, or `()` when there is nothing.

    This session's own record first (`session`, the last press's `written`):
    it is exact. Without one -- the window was closed since, or crashed
    half-way through a press -- the last press read off the backups on disk,
    because the raw editor lists the WotLK confs read-only and its Revert cannot
    reach their backups. Both go through the one `still_undoable()` rule. Reads
    files and lists folders, so the Tuning tab runs it on its job runner.
    """
    if session:
        return still_undoable(server_dir, session)
    return last_reset_on_disk(entry, server_dir)


def last_reset_on_disk(entry: CatalogEntry, server_dir: Path) -> tuple[FileResult, ...]:
    """The last press's files and backups, read off the disk, for an Undo after a restart.

    The session's own record (`ResetReport.written`) dies with the window --
    and with a crash half-way through a press, which is exactly when an undo is
    wanted -- while the Tuning tab lists the WotLK confs read-only, so its
    per-file Revert cannot reach their backups. The backups beside each file
    are the record that survives. The newest reset backup of any of this
    game's own files names the last press (its id); that press's backup of
    each file is what it wrote -- however long the press took (Codex review:
    a ten-second window split a slow press) -- and of those, each
    `still_undoable()` is what an Undo would put back, so an undone reset is
    not offered again, even after a later save changed the file.

    Reads only; `()` when there is nothing to put back.
    """
    found = {file: _tagged(server_dir / file, RESET_TAG) for file in core_files(entry)}
    stamps = [(tag.stamp, tag.press) for backups in found.values() for tag, _ in backups]
    if not stamps:
        return ()
    last = max(stamps)[1]
    press = [
        FileResult(file, "reset", [b for tag, b in backups if tag.press == last][-1])
        for file, backups in found.items()
        if any(tag.press == last for tag, _ in backups)
    ]
    return still_undoable(server_dir, press)


def question(
    files: Sequence[str],
    modules: Sequence[str],
    *,
    facts: PressFacts | None = None,
) -> str:
    """The Yes/No text: which files, what goes back, what is kept, the backup, the restart.

    `facts` is `press_facts()` for these files: named BEFORE the press, so a
    file left alone, made again or left absent is never news only in the report.
    Conditional about the server being up: the tab keeps no status to ask
    (spec correction 14).
    """
    facts = facts or PressFacts()
    them = "this file" if len(files) == 1 else "these files"
    names = "\n".join(f"    {label(file)}" for file in files)
    parts = [
        f"Put {them} back to how Yu'lon installed this server?\n\n{names}",
        f"Every value you changed in {them} goes back to the one the install wrote. "
        "Settings in module files are kept.",
    ]
    if modules:
        parts.append(
            f"Settings that installed modules keep in {them} are kept as they are now: "
            f"{', '.join(modules)}."
        )
    for file in facts.foreign:
        parts.append(f"Left alone: {label(file)} was not made by Yu'lon.")
    for file in facts.missing:
        parts.append(f"Not on disk, so it is made again as Yu'lon installs it: {label(file)}.")
    for file in facts.absent:
        parts.append(
            f"{label(file)} is not on disk and a fresh install does not write it, so it stays "
            "as it is."
        )
    if composegen.OVERRIDE_FILE in files and composegen.OVERRIDE_FILE not in facts.foreign:
        parts.append(
            f"{composegen.OVERRIDE_FILE} holds the containers' own settings (the bot population, "
            "and the command channel if it is on). Anything added to it by hand is dropped, and "
            "the containers have to be RECREATED before it counts."
        )
    parts.append(
        'A backup of each file that is on disk is made first, and "Undo the last reset" in this '
        "menu puts them back."
    )
    # The job the banner will offer (`apply_rule`), never a flat "restart":
    # the override and CMaNGOS `etc/` owe a recreate (fix round 1).
    if any(apply_rule(file) == "recreate" for file in files):
        parts.append(
            "If the server is running, it keeps its current settings until its containers are "
            "recreated; this tab offers the recreate when the reset is done."
        )
    else:
        parts.append(
            "If the server is running, it keeps its current settings until it is restarted; this "
            "tab offers the restart when the reset is done."
        )
    return "\n\n".join(parts)


ResetRoute = Callable[[Sequence[str], ModuleKeys, "PressFacts | None"], ResetReport]
"""The press: the files asked for, the keys installed modules keep in them, and the
`PressFacts` the player said Yes to (`None`: nothing to hold the files to)."""


def route_for_app(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    seams: Seams | None = None,
) -> ResetRoute:
    """The reset a Tuning tab presses, bound to its install. Lazy: builds nothing until pressed.

    `seams` exists for the view's tests, which must not ask the host about
    SELinux; the app passes none.
    """

    def run(
        files: Sequence[str], keys: ModuleKeys, confirmed: PressFacts | None = None
    ) -> ResetReport:
        return reset(
            entry,
            server_dir,
            files,
            module_keys=keys,
            confirmed=confirmed,
            wsl_distro=wsl_distro,
            seams=seams,
        )

    return run
