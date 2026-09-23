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

import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
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
    write: Callable[[Path, str], None] = conf.replace_file
    backup: Callable[[Path], Path] = tuning.backup
    restore: Callable[[Path, Path], None] = tuning.restore


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


Outcome = Literal["reset", "already", "absent", "foreign", "refused", "held", "restored"]

WRITE_FAILED = (
    "it could not be written ({exc}). Every file this press had already reset was put back, "
    "so nothing changed"
)
ROLLBACK_FAILED = (
    "it was reset, then a later file failed, and copying {backup} back over it failed too "
    '({exc}). The file as it was is {backup}, beside it, and "Undo the last reset" tries again'
)
UNDO_FAILED = "copying {backup} back over it failed ({exc})"


@dataclass(frozen=True)
class FileResult:
    """What happened to one file. Never carries the file's text -- it holds the password."""

    file: str
    outcome: Outcome
    backup: Path | None = None
    reason: str = ""

    def line(self) -> str:
        name = label(self.file)
        kept = self.backup.name if self.backup is not None else ""
        return {
            "reset": f"{name}: back to how Yu'lon installed it. The file as it was is kept "
            f"beside it as {kept}.",
            "already": f"{name}: already as Yu'lon installed it, so it was left alone "
            "(no backup made).",
            "absent": f"{name}: not on disk, so there was nothing to reset.",
            "foreign": f"{name}: this server's compose files were not made by Yu'lon, so it "
            "was left alone.",
            # A refused file WITH a backup was written and could not be put
            # back (a failed rollback or a failed undo): "NOT reset" would be false.
            "refused": f"{name}: NOT {'put back' if self.backup else 'reset'}: {self.reason}.",
            "held": f"{name}: not reset. A reset is all or nothing, and another file could not "
            "be made.",
            "restored": f"{name}: put back from {kept}.",
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
        elif "reset" in outcomes:
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


def _foreign(server_dir: Path, file: str) -> bool:
    """The override of a compose stack Yu'lon did not generate (spec correction 20)."""
    if file != composegen.OVERRIDE_FILE:
        return False
    base = server_dir / composegen.BASE_FILE
    return not (base.is_file() and composegen.is_ours(base))


def reset(
    entry: CatalogEntry,
    server_dir: Path,
    files: Sequence[str],
    *,
    module_keys: ModuleKeys | None = None,
    wsl_distro: str | None = None,
    seams: Seams | None = None,
) -> ResetReport:
    """Put `files` back to their as-installed text: every one, or none.

    Phase one builds every default and reads every current file, and writes
    nothing; one failure there refuses the whole press. Phase two backs each
    changing file up (`tuning.backup`, `<file>.<stamp>.bak` beside it) and
    replaces it atomically (`conf.replace_file`); a failure there puts back,
    from those backups, every file this press had already written. A file
    already equal to its default is not touched and gets no backup; a file not
    on disk is reported and never created; another tool's override is left.

    `module_keys` (owner decision 5): for each file, the keys installed
    modules declare in it, from `module_keys()`. Their live lines are carried
    into the default before anything is compared or written, so a module's
    setting keeps its current value while every core key goes back. A file
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
    for file in files:
        if _foreign(server_dir, file):
            skipped[file] = "foreign"
        elif not (server_dir / file).is_file():
            skipped[file] = "absent"
    present = [file for file in files if file not in skipped]
    texts, reasons = (
        default_texts(entry, server_dir, present, wsl_distro=wsl_distro, seams=seams)
        if present
        else ({}, {})
    )
    current: dict[str, bytes] = {}
    for file in present:
        if file in reasons:
            continue
        try:
            current[file] = (server_dir / file).read_bytes()
        except OSError as exc:
            reasons[file] = UNREADABLE.format(what=label(file), exc=exc)
            continue
        carry = (module_keys or {}).get(file)
        if carry:
            try:
                live = current[file].decode("utf-8")
            except UnicodeDecodeError as exc:
                reasons[file] = NOT_UTF8.format(what=label(file), exc=exc)
                continue
            texts[file] = carry_module_keys(texts[file], live, carry)
    if reasons:
        return ResetReport(tuple(_before_writing(file, skipped, reasons) for file in files))

    done: dict[str, FileResult] = {}
    for file in present:
        path = server_dir / file
        if current[file] == texts[file].encode("utf-8"):
            continue
        try:
            made = seams.backup(path)
            seams.write(path, texts[file])
        except (OSError, InstallerError, tuning.TuningError) as exc:
            return _rolled_back(files, skipped, done, file, exc, server_dir, seams)
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
) -> ResetReport:
    """A write failed part-way: put every file this press wrote back from its backup."""
    logger.warning(f"reset of {failed} failed ({exc}); putting back {len(done)} file(s)")
    results: dict[str, FileResult] = {}
    for file, item in done.items():
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
    return ResetReport(
        tuple(results.get(file) or FileResult(file, skipped.get(file, "held")) for file in files)
    )


def undo(
    server_dir: Path,
    written: Sequence[FileResult],
    *,
    restore: Callable[[Path, Path], None] = tuning.restore,
) -> ResetReport:
    """Copy back, from the backups a reset made, every file that reset wrote.

    A copy (`tuning.restore`), so the backup survives and a second undo still
    has something to restore.
    """
    results: list[FileResult] = []
    for item in written:
        if item.backup is None:
            continue
        try:
            restore(item.backup, server_dir / item.file)
        except OSError as exc:
            results.append(
                FileResult(
                    item.file,
                    "refused",
                    item.backup,
                    UNDO_FAILED.format(backup=item.backup.name, exc=exc),
                )
            )
        else:
            logger.info(f"put {item.file} back from {item.backup.name}")
            results.append(FileResult(item.file, "restored", item.backup))
    return ResetReport(tuple(results), undo=True)


def question(files: Sequence[str], modules: Sequence[str]) -> str:
    """The Yes/No text: which files, what goes back, what is kept, the backup, the restart.

    Conditional about the server being up: the tab keeps no status to ask
    (spec correction 14).
    """
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
    if composegen.OVERRIDE_FILE in files:
        parts.append(
            f"{composegen.OVERRIDE_FILE} holds the containers' own settings (the bot population, "
            "and the command channel if it is on). Anything added to it by hand is dropped, and "
            "the containers have to be RECREATED before it counts."
        )
    parts.append(
        'A backup of each file is made first, and "Undo the last reset" in this menu puts '
        "them back."
    )
    parts.append(
        "If the server is running, it keeps its current settings until it is restarted; this "
        "tab offers the restart when the reset is done."
    )
    return "\n\n".join(parts)


ResetRoute = Callable[[Sequence[str], ModuleKeys], ResetReport]
"""The press: the files asked for, and the keys installed modules keep in them."""


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

    def run(files: Sequence[str], keys: ModuleKeys) -> ResetReport:
        return reset(entry, server_dir, files, module_keys=keys, wsl_distro=wsl_distro, seams=seams)

    return run
