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
outside the set as a caller bug.

Nothing here imports Qt. The Tuning tab (`ui/controller_view.py`) draws the
button and runs `reset()` on its job runner.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

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
    """The as-installed text of each file, in memory. Writes nothing to the server folder.

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
    """The install's own password, or `None`. NEVER a new one (spec correction 3)."""
    known = entry.install.db_password(server_dir)
    if known is not None:
        return known
    kept = dbsecret.recall(
        entry.id, composegen.install_id(server_dir, platform_id=seams.platform_id)
    )
    return None if kept is None else kept.password


def _from_image(
    entry: CatalogEntry,
    server_dir: Path,
    files: Sequence[str],
    *,
    wsl_distro: str | None,
    seams: Seams,
) -> Built:
    """CMaNGOS/Tortoise: the image's template, patched by the install's table and tokens.

    One `docker cp` of the whole source dir into a temporary folder, as
    `conf.materialise()` does into `etc/` -- but never into `etc/`, because
    that folder is the thing being reset. Local checks first (distro,
    password), then one question to the daemon (is the image here?), then the
    copy: the cheapest refusal is the one that runs.
    """

    def every(reason: str) -> Built:
        return {}, dict.fromkeys(files, reason)

    if wsl_distro is not None:
        return every(IN_WSL.format(distro=wsl_distro))
    engine = installer_for(entry, platform_id=seams.platform_id)
    if not isinstance(engine, CmangosInstaller):  # the catalog says cmangos; keeps mypy honest
        return every(NO_DEFAULT.format(game=entry.name))
    password = _password(entry, server_dir, seams)
    if password is None:
        return every(NO_PASSWORD.format(file=entry.install.password.file or "its password file"))
    table = engine.conf_table()
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
    texts: dict[str, str] = {}
    reasons: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="yulon-reset-") as scratch:
        # `dest` must NOT exist: `docker cp <c>:/opt/mangos/etc <dest>` then
        # makes `dest` the folder (conf.py:236-241 has the trailing-slash half).
        staged = Path(scratch) / "etc"
        try:
            seams.copy_from_image(image, source, staged)
        except docker.DockerCliMissingError as exc:
            # Ahead of its base class: it carries the one instruction a user can act on.
            return every(str(exc))
        except docker.DockerCommandError as exc:
            return every(COPY_FAILED.format(exc=exc))
        for file in files:
            name = file[len(ETC_DIR) + 1 :]
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
    return texts, reasons
