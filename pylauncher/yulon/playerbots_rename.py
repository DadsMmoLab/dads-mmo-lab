"""A server's bot settings renamed to the prefix its mod-playerbots reads, before a start (T657).

`playerbots_keys` says which prefix a server's checkout reads. This renames what Yu'lon
and the player wrote to match, in the three files that hold such names:

* `env/dist/etc/modules/playerbots.conf`: every `AiPlayerbot.<Name> = …` line (or
  `Playerbots.<Name>`, going back), commented ones too so an uncommented line still
  counts; every other byte kept -- values, comments, a player's own keys (which get
  the same prefix change), the order, CRLF;
* the compose override's world `environment:` names (`AC_AI_PLAYERBOT_*` <->
  `AC_PLAYERBOTS_*`), values kept, nothing else in the file touched -- only when the
  compose files are Yu'lon's;
* the command channel's copy of the override from before its press
  (`<override>.before-channel`), which its rollback puts back.

**When.** Right before every start of the world: the Server tab's Start, Restart and
recreate (`Controller.start()`, also the Bots tab's Apply… recreate), the install's
`up` and a rebuild's recreate (Rebuild, "Update the server to latest…", "Return to the
tested pin…", the Modules tab's mod-playerbots update, a rollback starting the build
from before). So a press that moves mod-playerbots across the rename renames on its
way to the start, a rollback renames back, and a server already moved by an older Yu'lon
(v0.9.15's "Update to latest") is renamed at its next start. Nothing to rename says
nothing; the first rename says one line.

**Safe to run again.** Renamed lines are not renamed twice. A key set under BOTH names
keeps the line already under the module's name (the one the module reads, first copy
winning) and leaves the other as it was, which the module ignores. The conf and the
override are backed up beside themselves first (`tuning.backup()`, which the Tuning
tab's Revert lists); the channel's copy is a backup itself and gets none, since a
`.bak` of it would be listed as one of the override's.

**A rename that cannot be written stops the start** (`RenameRefused`): the world would
otherwise run on the module's defaults -- 500 bots and its command server on 8888 --
while Yu'lon's settings said otherwise.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from yulon import playerbots_keys, server_build_presses, tuning
from yulon.catalog import compose_env, composegen
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families import conf
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

_CONF_LINE = re.compile(
    r"^(?P<head>[ \t]*(?P<hash>#?)[ \t]*)(?P<key>(?:AiPlayerbot|Playerbots)\.[A-Za-z0-9_.]+)"
    r"(?P<tail>[ \t]*=.*)$",
    re.DOTALL,
)

SAID = (
    "mod-playerbots on this server reads its settings as {new}* now, so Yu'lon renamed "
    "{count} of this server's {old}* setting{s} to {new}*, every value kept ({files}); the "
    "files as they were are backed up beside them."
)
REFUSED = (
    "mod-playerbots on this server reads its settings as {new}*, and Yu'lon could not rename "
    "this server's {old}* settings to match ({why}), so the server was not started: it would "
    "run on the module's defaults (500 bots, its command port open) instead of your settings."
)
BUILT_PREFIX_FILE = ".yulon-built-prefix.json"
"""The prefix the server's BUILT image reads, as the checkout said when it was compiled (T660).

`{"version": 1, "prefix": "AiPlayerbot."}`. The checkout cannot say it: Update to latest
moves the sources before an hours-long compile, and a Yu'lon that dies in between leaves
the old image beside the new sources. Written after the compile returned 0 (the install's
`stage_build()`, a rebuild's build stage), and put back by a rollback; never forgotten
at a rebuild's start, so a crash leaves the OLD image's prefix. Absent for a folder built
before this existed, which is judged by its checkout alone, as T657 did.
"""

OLDER_BUILD = (
    "This server's build is older than its sources: it was built from a mod-playerbots that "
    "reads its settings as {built}*, but the folder now holds one that reads {now}* (an update "
    "that stopped before its compile finished leaves this). Yu'lon did not rename your settings "
    "and did not start the server, because the build that would run ignores {now}* settings. "
    "Press {rebuild} to build what the folder holds."
)


class RenameRefused(InstallerError):
    """The settings could not be renamed; nothing was started (the sentence says why)."""


def read_built(server_dir: Path) -> str | None:
    """The prefix the built image reads (`BUILT_PREFIX_FILE`), or None: absent or not one."""
    try:
        raw = json.loads((server_dir / BUILT_PREFIX_FILE).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning(f"{server_dir / BUILT_PREFIX_FILE} could not be read ({exc})")
        return None
    prefix = raw.get("prefix") if isinstance(raw, dict) and raw.get("version") == 1 else None
    if prefix in (playerbots_keys.OLD, playerbots_keys.NEW):
        return str(prefix)
    return None


def write_built(server_dir: Path, prefix: str | None) -> None:
    """Record `prefix` as the built image's, or forget the record when None. Never raises.

    A record that cannot be written is logged and left absent: the server is then judged by
    its checkout, as before this record existed.
    """
    path = server_dir / BUILT_PREFIX_FILE
    staged = path.with_name(path.name + ".yulon-new")
    try:
        if prefix is None:
            path.unlink(missing_ok=True)
            return
        staged.write_text(json.dumps({"version": 1, "prefix": prefix}) + "\n", encoding="utf-8")
        os.replace(staged, path)
    except OSError as exc:
        logger.warning(f"{path} could not be updated ({exc}); the build is taken as not known")
        try:
            staged.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
        except OSError as also:
            logger.warning(f"could not clean up {path}: {also}")


def remember_built(server_dir: Path) -> None:
    """Record the prefix the checkout reads now as the one the image just compiled reads."""
    write_built(server_dir, playerbots_keys.module_prefix(server_dir))


def _active_keys(lines: list[str]) -> set[str]:
    found: set[str] = set()
    for line in lines:
        body = line.strip()
        if body and not body.startswith("#") and "=" in body:
            found.add(body.partition("=")[0].strip())
    return found


BOM = "\ufeff"


def rename_conf_text(text: str, prefix: str) -> tuple[str, int]:
    """`text` with every bot key line under `prefix`, and how many lines changed.

    A UTF-8 byte-order mark (a Windows editor's save) is kept and does not hide the first line.
    """
    if text.startswith(BOM):
        renamed, count = rename_conf_text(text[len(BOM) :], prefix)
        return BOM + renamed, count
    lines = text.split("\n")
    active = _active_keys(lines)
    count = 0
    for index, line in enumerate(lines):
        match = _CONF_LINE.match(line)
        if match is None:
            continue
        name = match.group("key")
        target = playerbots_keys.key(name, prefix)
        if target == name:
            continue
        live = not match.group("hash")
        if live and target in active:
            continue
        lines[index] = f"{match.group('head')}{target}{match.group('tail')}"
        if live:
            active.add(target)
        count += 1
    return "\n".join(lines), count


def rename_env_text(text: str, service: str, prefix: str) -> tuple[str, int]:
    """`text` with `service`'s environment names under `prefix`, and how many lines changed."""
    lines = text.split("\n")
    at = compose_env.env_lines(lines, service)
    count = 0
    for name, spots in at.items():
        target = playerbots_keys.env(name, prefix)
        if target == name or target in at:
            continue
        for index in spots:
            line = lines[index]
            ending = "\r" if line.endswith("\r") else ""
            match = compose_env.ENV_LINE.match(line[: len(line) - len(ending)])
            if match is None:  # `env_lines` hands over matching lines only
                continue
            head = match.group("head").replace(name, target, 1)
            lines[index] = f"{head}{match.group('value')}{match.group('tail')}{ending}"
            count += 1
    return "\n".join(lines), count


@dataclass(frozen=True)
class _Change:
    path: Path
    text: str
    count: int
    backed_up: bool


def _read(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _changes(entry: CatalogEntry, server_dir: Path, prefix: str) -> list[_Change]:
    found: list[_Change] = []
    conf_path = server_dir / playerbots_keys.CONF
    if conf_path.is_file() and not conf_path.is_symlink():
        text, count = rename_conf_text(_read(conf_path), prefix)
        if count:
            found.append(_Change(conf_path, text, count, backed_up=True))
    base = server_dir / composegen.BASE_FILE
    if not (base.is_file() and composegen.is_ours(base)):
        return found
    service = entry.container_spec().world
    override = server_dir / composegen.OVERRIDE_FILE
    channel = server_dir / f"{composegen.OVERRIDE_FILE}{composegen.CHANNEL_BACKUP_SUFFIX}"
    for path, backed_up in ((override, True), (channel, False)):
        if path.is_file() and not path.is_symlink():
            text, count = rename_env_text(_read(path), service, prefix)
            if count:
                found.append(_Change(path, text, count, backed_up))
    return found


def settle(entry: CatalogEntry, server_dir: Path) -> str | None:
    """Rename this server's bot settings to its module's prefix; the line to say, or None.

    Raises:
        RenameRefused: a file could not be read or written; the ones written before it
            stay renamed (each is whole, and backed up), and the next start finishes.
    """
    native = entry.install.native
    if native is None or native.family != "azerothcore":
        return None
    prefix = playerbots_keys.module_prefix(server_dir)
    if prefix is None:
        return None
    old = playerbots_keys.other(prefix)
    built = read_built(server_dir)
    if built is not None and built != prefix:
        raise RenameRefused(
            OLDER_BUILD.format(
                built=built,
                now=prefix,
                rebuild=server_build_presses.under_server_build(server_build_presses.REBUILD),
            )
        )
    try:
        changes = _changes(entry, server_dir, prefix)
        for change in changes:
            if change.backed_up:
                tuning.backup(change.path, root=server_dir)
            conf.replace_file(change.path, change.text)
    except (OSError, UnicodeDecodeError, InstallerError, tuning.TuningError) as exc:
        raise RenameRefused(REFUSED.format(new=prefix, old=old, why=exc)) from exc
    for change in changes:
        logger.info(f"renamed {change.count} bot setting(s) {old}* -> {prefix}* in {change.path}")
    said = [change for change in changes if change.backed_up]
    if not said:  # nothing a player set, only the channel's copy (logged above)
        return None
    count = sum(change.count for change in said)
    files = ", ".join(change.path.name for change in said)
    return SAID.format(new=prefix, old=old, count=count, s="" if count == 1 else "s", files=files)
