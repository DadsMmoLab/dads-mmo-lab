"""The random-bot count a rewrite carries over, read off the file it replaces (T117).

T99 let a player set the count (Min = Max = N) where the install wrote it: the
WotLK override's world `environment:` lines, or a CMaNGOS tree's
`etc/aiplayerbot.conf`. Every other writer of those files built the number from
the catalog again, so a Repair, Update to latest's put-back, the channel's
Enable press and the CMaNGOS `conf` stage each put 500 back (measured,
`tests/test_keep_bot_count.py`). Those writers ask this module instead. Reset to
default does not: putting the installed number back is its job.

**The file on disk is the only source.** The same rule T102 follows for the
SELinux label: nothing is probed and nothing is remembered elsewhere, so what a
rewrite keeps is what the server was going to read. A pair is carried only when
both are whole numbers and 0 <= Min <= Max (and, in the override, each line is
there exactly once, since otherwise which one the server reads is unknown).
Anything else -- no file, an unreadable one, a pair the server would not honour
-- carries nothing, and the catalog's value stands.

One reader for both formats where they meet: the pair rule (`pair`). The two
files differ only in how a value is found -- a `KEY: "value"` line in one
compose service, a `Key = value` line read by `tuning.conf_value` -- and that
line scanner is here too, shared with the Bots tab's own reader
(`bot_population`). Nothing here imports Qt, and every public function but
`pair` reads the disk.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from yulon import tuning
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, ConfPatchTable

MIN_KEY = "AiPlayerbot.MinRandomBots"
MAX_KEY = "AiPlayerbot.MaxRandomBots"
MIN_ENV = composegen.env_name_for(MIN_KEY)
MAX_ENV = composegen.env_name_for(MAX_KEY)
CONF_NAME = "aiplayerbot.conf"

LARGEST = 2**31 - 1
"""The largest whole number the cores read these keys as (`GetIntDefault`, `GetOption<int32>`)."""

_WHOLE = re.compile(r"[0-9]+")

ENV_LINE = re.compile(
    r"^(?P<head>[ \t]*(?P<key>[A-Za-z_][A-Za-z0-9_]*)[ \t]*:[ \t]*)"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s#]*)(?P<tail>.*)$"
)


def pair(low: str | None, high: str | None) -> tuple[str, str] | None:
    """`(Min, Max)` as whole-number text when a server would honour them, else `None`.

    Digits only, so `+5` and `1_000`, which Python's `int()` takes, are refused;
    then 0 <= Min <= Max <= `LARGEST`.
    """
    if low is None or high is None:
        return None
    low, high = low.strip(), high.strip()
    if not (_WHOLE.fullmatch(low) and _WHOLE.fullmatch(high)):
        return None
    lo, hi = int(low), int(high)
    if lo > hi or hi > LARGEST:
        return None
    return str(lo), str(hi)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _says_nothing(line: str) -> bool:
    bare = line.strip()
    return bare == "" or bare.startswith("#")


def env_lines(lines: list[str], service: str) -> dict[str, list[int]]:
    """Where each key of `service`'s `environment:` mapping is, by line index. Comments skipped.

    A line scanner and not a YAML round trip, on purpose: a YAML writer drops
    comments and restyles what it emits (the override template's own header
    says so), and the Bots box must change two values and nothing else. The
    shape it reads is the one the install writes -- `  <service>:` then
    `    environment:` then `      KEY: "value"` lines; a block it cannot find
    answers `{}`.
    """
    found: dict[str, list[int]] = {}
    service_at: int | None = None
    env_at: int | None = None
    for index, raw in enumerate(lines):
        line = raw.rstrip("\r")
        if _says_nothing(line):
            continue
        depth = _indent(line)
        if env_at is not None and depth <= env_at:
            env_at = None
        if service_at is not None and depth <= service_at:
            service_at = None
        if service_at is None:
            if line.strip() == f"{service}:":
                service_at = depth
            continue
        if env_at is None:
            if line.strip() == "environment:":
                env_at = depth
            continue
        match = ENV_LINE.match(line)
        if match is not None:
            found.setdefault(match.group("key"), []).append(index)
    return found


def env_value(line: str) -> str:
    """The value of one `KEY: value` line, quotes stripped."""
    match = ENV_LINE.match(line.rstrip("\r"))
    return "" if match is None else match.group("value").strip("\"'")


def _text(path: Path) -> str | None:
    """The file's exact text, or `None` when there is none a rewrite could trust."""
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def in_override_text(text: str, entry: CatalogEntry) -> dict[str, str]:
    """`{MIN_ENV: …, MAX_ENV: …}` from an override's world environment, or `{}`."""
    native = entry.install.native
    if native is None or native.azerothcore is None:
        return {}
    lines = text.split("\n")
    at = env_lines(lines, entry.container_spec().world)
    spots = [at.get(name, []) for name in (MIN_ENV, MAX_ENV)]
    if any(len(spot) != 1 for spot in spots):
        return {}
    kept = pair(*(env_value(lines[spot[0]]) for spot in spots))
    return {} if kept is None else {MIN_ENV: kept[0], MAX_ENV: kept[1]}


def in_override(entry: CatalogEntry, server_dir: Path) -> dict[str, str]:
    """The player's pair in this install's override on disk, or `{}`."""
    text = _text(server_dir / composegen.OVERRIDE_FILE)
    return {} if text is None else in_override_text(text, entry)


def in_conf(path: Path) -> dict[str, str]:
    """`{MIN_KEY: …, MAX_KEY: …}` from an `aiplayerbot.conf`, or `{}`."""
    text = _text(path)
    if text is None:
        return {}
    kept = pair(tuning.conf_value(text, MIN_KEY), tuning.conf_value(text, MAX_KEY))
    return {} if kept is None else {MIN_KEY: kept[0], MAX_KEY: kept[1]}


def world_env(
    entry: CatalogEntry, server_dir: Path, env: Mapping[str, str] | None
) -> dict[str, str] | None:
    """`env` -- `None` meaning the install's own -- with the override's pair laid over it.

    What a writer that REGENERATES the override hands `composegen.render()`.
    `None` back when there is nothing to carry and nothing was asked for, which
    is `render()`'s own default.
    """
    kept = in_override(entry, server_dir)
    if not kept:
        return None if env is None else dict(env)
    return {**(composegen.world_env(entry) if env is None else env), **kept}


def conf_table(table: ConfPatchTable, etc_dir: Path) -> ConfPatchTable:
    """The install table with its two bot keys set to the pair `aiplayerbot.conf` holds now.

    The table unchanged when it does not write both keys, or the file has no
    pair to carry.
    """
    patch = table.files.get(CONF_NAME)
    if patch is None or MIN_KEY not in patch.keys or MAX_KEY not in patch.keys:
        return table
    kept = in_conf(etc_dir / CONF_NAME)
    if not kept:
        return table
    files = {**table.files, CONF_NAME: patch.model_copy(update={"keys": {**patch.keys, **kept}})}
    return table.model_copy(update={"files": files})
