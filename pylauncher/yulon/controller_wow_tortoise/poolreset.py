"""Rebuild the random bots from scratch, the bots module's own way (T144).

**Why.** TortoiseBots' developer (Discord, 2026-09-26): most module changes to
random bots -- seeding, gear, professions, skills -- only reach bots created
afterwards. The module's remedy is a pool reset, documented in its
`docs/guides/living-world.md` §8 and read here at the pin Yu'lon builds
(f858f9c9; `runtime/RandomBotPoolReset.cpp` is byte-identical at 632e1b63):

* the key is `AiPlayerbot.RandomBotPoolReset` in `aiplayerbot.conf`, the bot
  config the module opens next to `mangosd.conf`
  (`ai/playerbot/PlayerbotAIConfig.cpp:125-135` and `:619`), not
  `modules/tortoise_bots.conf`;
* `once:<token>` resets at the NEXT world start only, and only while the token
  differs from the last generation the module completed. A token is 1-128
  printable, non-space ASCII characters (`runtime/PoolResetPolicy.h`,
  `IsValidPoolResetToken`), so a used token is a no-op forever and every press
  here writes a fresh one;
* the reset deletes the characters on the module's registered pool accounts
  (the accounts stay), verifies, records the generation, and auto-create makes
  the pool again (it needs `AiPlayerbot.RandomBotAutoCreate = 1`, which the
  install writes). It refuses, deleting nothing, when a pool character is being
  played or a guild led by a pool bot holds anyone outside the pool.

**Adopt first.** Accounts an older module made are not in the registry until
`bot pool adopt` enrols them (T123, `botpool.py`), and a reset only touches
registered accounts. So a running server is asked to enrol them before the key
is written. Enrolment only registers; it never deletes. Without it the reset
touches FEWER characters, never more, so an enrolment no channel could run is
said out loud (with the console steps) and the rebuild goes on over the bots
that are enrolled -- a question in the middle of a job would need a modal on the
worker thread, which this app never does.

**One restart.** The reset runs only at world start, so the flow is: back up
(if asked) -> enrol -> write the key -> restart ONCE -> read this run's world
log for the module's lines. After an update that moved the module, T123
enrols but leaves its restart OWED (`botpool.ModuleMoved`): a Yes to the
rebuild makes this flow's one restart do both, and anything else runs the owed
restart on its own, so the update path restarts the world once either way.

**A refusal takes the request back** (owner, 2026-09-26): every outcome that
did not complete the reset and deleted nothing -- a refusal, a skip, an unread
or invalid setting, a failed restart, a Stop after the write -- sets the key
back to `off` (with no backup of its own: see below), so no later ordinary restart rebuilds
the bots by surprise. The one exception is a reset that failed PART-WAY
(`reset failed:`, logged after deletion may have begun): the module records the
generation only after success and resumes at the next start while the token is
still new, and `off` would stop that resume (`PlanAtStartup` returns on `Off`
before anything is planned), stranding half a pool. So there the request stays.
A watch that timed out, or was stopped, leaves it too: the rebuild may still be
running; so does a restart that raised while the world may be up (see
`_after_a_failed_restart`). A take-back makes NO backup of its own, so the
Tuning tab's Revert lands on the file as it was before the request.

**An applied rebuild takes the request back too.** The module records the
applied generation in the characters database, which a Yu'lon backup dumps: a
restore of the backup taken first would roll it back, and a `once:` left in
the conf would then delete the restored bots at the next start.

**Nothing else writes the key.** It is not in the catalog's conf table, so
Repair and Update leave it where it is (a used token is a no-op), and Reset to
default drops it, which is harmless for the same reason.

**A Tuning backup never brings a request back** (T145). A backup taken while a
request was pending holds it, and the Playerbots card's Revert, the raw
editor's Revert and Undo the last reset put a backup back whole -- after a
restore, that re-armed a request the restored databases had no record of. They
all go through `put_back_file`: a backup that asks for a rebuild anywhere goes
back with the file's own key lines in place of its own. The key is read the way the
module reads it (`_assigned`: indented and quoted lines too, the last one in a
section wins; across sections any request counts, `value_in`) and written into
every section so the module reads what was written (`_with_value`).

Nothing here imports Qt. `PoolRebuild.rebuild()` is a line source for the Bots
tab's log panel and runs on that panel's worker thread.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import threading
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

from yulon import bot_population, docker, tuning
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, ConfPatch
from yulon.catalog.families import conf
from yulon.catalog.installer import InstallerError
from yulon.channel import Channel
from yulon.controller import StartRefused
from yulon.controller_wow_tortoise import botpool
from yulon.log import get_logger

logger = get_logger(__name__)

KEY = "AiPlayerbot.RandomBotPoolReset"
"""The module's one reset setting: `off | always | once:<token>` (`PoolResetPolicy.h`)."""

TOKEN_MAX = 128
"""`kPoolResetTokenMaxLength` in `runtime/PoolResetPolicy.h`."""

WATCH_TIMEOUT_S = 20 * 60.0
"""How long the world log is watched after the restart before the job stops watching.

The reset is planned at world start, deletes one character per world tick
(500 bots is under a minute), waits up to 15 s to verify, and has two 120 s
stall limits (`RandomBotPoolReset.cpp:37-43`). A Tortoise world takes a few
minutes to come up with 500 bots. Twenty minutes covers all of that with room;
past it the job says where to look rather than holding the tab.
"""

_T = TypeVar("_T")

POLL_S = 10.0
"""Seconds between reads of the world log while watching: the module's own progress interval."""

TORTOISE_CONSOLE_TAB = "Console tab"

Final = Literal["applied", "refused", "part-way", "not-read"]
"""`part-way` is the one final that keeps the request; the others that are not
`applied` take it back."""


class PoolResetError(Exception):
    """A refusal a player reads. Raised out of the job, so the log panel says FAILED."""


def new_token(now: datetime) -> str:
    """`yulon-<UTC YYYYmmddTHHMMSSZ>`: new every press, and readable in `bot pool status`."""
    return "yulon-" + now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def is_valid_token(token: str) -> bool:
    """The module's own rule (`IsValidPoolResetToken`): 1-128 characters, each 0x21-0x7e."""
    return 0 < len(token) <= TOKEN_MAX and all(0x20 < ord(c) < 0x7F for c in token)


@dataclass(frozen=True)
class KeyWritten:
    """The file the request went into, the copy of it as it was, and the token."""

    file: Path
    backup: Path
    token: str


def write_key(entry: CatalogEntry, server_dir: Path, token: str) -> KeyWritten:
    """Write `AiPlayerbot.RandomBotPoolReset = once:<token>`, backing the file up first.

    Raises:
        PoolResetError: nothing was written.
    """
    if not is_valid_token(token):
        raise PoolResetError(f"{token!r} is not a token the bots module accepts")
    path, made = _write_value(entry, server_dir, f"once:{token}")
    if made is None:  # never: `backup=True`; a plain check, because `-O` drops asserts
        raise PoolResetError(f"no backup was made of {path.name}")
    logger.info(f"asked {entry.id} to rebuild its random bots ({KEY} = once:{token}) in {path}")
    return KeyWritten(path, made, token)


def take_back(entry: CatalogEntry, server_dir: Path) -> Path:
    """Write `AiPlayerbot.RandomBotPoolReset = off`, WITHOUT a backup of its own; the file.

    No backup, on purpose: a backup taken now would be of the file saying
    `once:<token>`, and the Tuning tab's Revert restores the NEWEST backup --
    so Revert after a take-back would arm the request again and the next
    restart would rebuild the bots by surprise. Without one, the newest is the
    backup `write_key` made a moment earlier: the file as it was before the
    request, key off. (An OLDER save's backup can still hold a request;
    `put_back_file` keeps it out, T145.)

    Raises:
        PoolResetError: nothing was written.
    """
    path, _ = _write_value(entry, server_dir, "off", backup=False)
    logger.info(f"took {entry.id}'s random-bot rebuild request back ({KEY} = off) in {path}")
    return path


def _write_value(
    entry: CatalogEntry, server_dir: Path, value: str, *, backup: bool = True
) -> tuple[Path, Path | None]:
    """Set the key to `value` in the install's `aiplayerbot.conf`; the file and its backup.

    The bot count's writer (`bot_population.write`) in miniature: a fresh read,
    `_with_value` (every other byte kept; the key replaced where it stands, or
    appended), `tuning.backup` beside it so the Tuning tab's Revert on the
    Playerbots card finds it (unless `backup` is False: `take_back`), and an
    atomic replace.
    """
    path = server_dir / bot_population.CONF_FILE
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            before = handle.read()
        text = _with_value(entry, before, value)
        made = tuning.backup(path) if backup else None
    except (OSError, UnicodeDecodeError, InstallerError, tuning.TuningError) as exc:
        raise PoolResetError(f"could not write {path.name}: {exc}") from exc
    try:
        conf.replace_file(path, text)
    except InstallerError as exc:
        if made is not None:
            made.unlink(missing_ok=True)
        raise PoolResetError(f"could not write {path.name}: {exc}") from exc
    return path, made


_SPACE = " \t\n\v\f\r"
"""`ace_isspace` in the C locale: what ACE's `squish` trims off a line, a name and a value."""


def _assigned(line: str) -> str | None:
    """The value the module reads from `line` when it assigns the key; None when it does not.

    The module reads `aiplayerbot.conf` through the core's `Config::SetSource`
    (tortoise-wow `src/shared/Config/Config.cpp`, `Reload`), which is ACE's ini
    importer, `ACE_Ini_ImpExp::import_config` (ACE 7.1.2, the `libace-dev` of
    the Ubuntu 24.04 image; T145): the line is trimmed, so an INDENTED line is
    live; one that then starts with `;` or `#` is a comment, `[` a section;
    the name is everything before the first `=`, trimmed; the value is the
    rest, trimmed, with ONE pair of surrounding double quotes taken off when
    both ends are `"`. Nothing wider: a trailing `# ...` is part of the value.

    The comment and section rules need no code of their own here: such a
    line's trimmed name starts with `;`, `#` or `[`, so it never equals the
    key, and the exact name comparison carries them (a mutation that skipped
    them survived for that reason).
    """
    name, equals, value = line.partition("=")
    if not equals or name.strip(_SPACE) != KEY:
        return None
    value = value.strip(_SPACE)
    if value[:1] == '"' and value[-1:] == '"':
        value = value[1:-1]
    return value


Mode = Literal["off", "always", "once", "invalid"]


@dataclass(frozen=True)
class Request:
    """What one value of the key means to the module: its mode and, for `once`, the token."""

    mode: Mode
    token: str = ""


_MODULE_TRIM = " \t\r\n"
"""What `ParsePoolResetSetting` trims off the whole value (`PoolResetPolicy.h:63-65`)."""
_TOKEN_TRIM = " \t"
"""What it trims off the token after `once:` (`PoolResetPolicy.h:83-85`)."""


def parse(value: str) -> Request:
    """`value` classified exactly as TortoiseBots' `ParsePoolResetSetting` does. Pure.

    TortoiseBots 632e1b63, `runtime/PoolResetPolicy.h:58-108`, called on the
    raw config string by `RandomBotPoolReset::PlanAtStartup`
    (`runtime/RandomBotPoolReset.cpp:311`) and `RandomBotService.cpp:250` (T145
    round 3). `value` is what ACE hands the module (`_assigned`: squished,
    one pair of quotes off), so padding INSIDE quotes is still there:

    1. trim `" \t\r\n"` off both ends (:63-65) -- not `\v` or `\f`;
    2. lower-case a copy (:67-69); empty or `off` is Off (:71), `always` is
       Always (:74);
    3. a lower-cased copy starting `once:` (:80): the token is the ORIGINAL
       text after those five characters, case kept (:82), trimmed of `" \t"`
       (:83-85); empty, or failing `IsValidPoolResetToken` (1-128 characters,
       each 0x21-0x7e, :44-56), is Invalid (:87-99); else Once with it;
    4. anything else is Invalid (:105-107).

    Invalid schedules nothing (`ShouldResetForGeneration`, :113-124), and the
    token is compared with the applied generation case-sensitively.
    """
    trimmed = value.strip(_MODULE_TRIM)
    # `std::tolower` in the C locale folds A-Z only, `str.lower()` far more; the
    # two agree on these three words, because the only characters `str.lower()`
    # turns into ASCII are U+0130 (to "i" + a combining dot) and U+212A (to "k").
    lowered = trimmed.lower()
    if lowered in ("", "off"):
        return Request("off")
    if lowered == "always":
        return Request("always")
    if lowered.startswith("once:"):
        token = trimmed[5:].strip(_TOKEN_TRIM)
        return Request("once", token) if is_valid_token(token) else Request("invalid")
    return Request("invalid")


def _lines_by_section(text: str) -> list[tuple[str, str, bool]]:
    """`text` split at `\n` (`fgets`), each line with its section and whether it is a header.

    A header's own section is the one it opens. A section header met again, in
    any case, reopens the same section (`ACE_Configuration_ExtId::operator==`
    is `strcasecmp`), and lines before any header are the section "". Every
    line keeps its bytes, the `\r` of a CRLF file included.
    """
    found: list[tuple[str, str, bool]] = []
    section = ""
    for line in text.split("\n"):
        body = line.strip(_SPACE)
        header = body[:1] == "["
        if header:
            # `import_config`: the name runs to the LAST `]`. A header without
            # one fails the whole import, so no value would be read at all;
            # read on as if it were one, the safe direction for a gate.
            end = body.rfind("]")
            section = (body[1:end] if end > 0 else body[1:]).casefold()
        found.append((section, line, header))
    return found


def _by_section(text: str) -> dict[str, str]:
    """Each section that sets the key, and the value the module reads there (last wins)."""
    found: dict[str, str] = {}
    for section, line, header in _lines_by_section(text):
        value = None if header else _assigned(line)
        if value is not None:
            found[section] = value
    return found


def values_in(text: str) -> tuple[str, ...]:
    """What each `[section]` of `text` sets the key to, as the module reads it, in file order.

    Inside one section the LAST assignment wins: ACE's `set_string_value`
    replaces a name the section already holds. Sections are
    `_lines_by_section`'s; the `\r` of a CRLF file is trimmed like any other
    space. A section that never sets the key is not listed.
    """
    return tuple(_by_section(text).values())


def value_in(text: str) -> str:
    """What `text` asks of the module: `off` when it never says. Fails safe across sections.

    One section (the shipped file's `[AiPlayerbotConf]`): its last assignment.
    More than one that set the key: the module's `GetValueHelper` returns the
    FIRST section that has it in the order `enumerate_sections` walks ACE's
    hash map -- not a file order anyone can predict (T145 round 2, Codex). So
    the answer is the value that asks the most of the module:

    * the first `once:<token>` in file order, when any section has one. It is
      what the restore gate clears (`before_restore`), and the take-back then
      writes `off` into EVERY section, an `always` beside it included, since
      the module may have read either; and it is the value a failed restore
      puts back (`put_back`), the direction that lets a part-way rebuild
      finish rather than strand half a pool;
    * else `always`, when any section has it: `restore_warning` names it;
    * else the first value that is not off: sections that disagree never read
      as off;
    * else the first (every section is off in some spelling).

    Each value is classified by `parse`, the module's own rule, and returned
    as written (`_assigned`'s answer), so a token goes back exactly.
    """
    values = values_in(text)
    if not values:
        return "off"
    modes = [parse(v).mode for v in values]
    once = [v for v, mode in zip(values, modes, strict=True) if mode == "once"]
    always = [v for v, mode in zip(values, modes, strict=True) if mode == "always"]
    not_off = [v for v, mode in zip(values, modes, strict=True) if mode != "off"]
    return (once or always or not_off or list(values))[0]


def setting(server_dir: Path) -> str:
    """What the install's `aiplayerbot.conf` asks of the module (`value_in`): `off` if unset.

    As written, case and padding kept, because a `once:` token is compared
    case-sensitively by the module and may have to be written back exactly;
    classify it with `parse`, never by comparing strings.

    Read the way the module reads it (T145), not the way `conf.patch` writes:
    an indented or quoted `once:` a person typed is a request the module acts
    on, and the restore gate must see it. A missing file is `off`: nothing is
    asked of a module that has no config.

    Raises:
        OSError, UnicodeDecodeError: the file is there and could not be read.
    """
    path = server_dir / bot_population.CONF_FILE
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        return "off"
    return value_in(text)


def _with_value(entry: CatalogEntry, text: str, value: str) -> str:
    """`text` with the key set to `value` in EVERY section that sets it. Pure.

    What is kept: every line that does not assign the key, byte for byte, and
    each key line's own line ending. What is not: a line that assigns the key
    is rewritten whole, as `conf.patch` writes it -- `KEY = value` at column 0,
    so its indentation, its spacing around `=` and any quotes go. A file that
    never sets the key gets it appended at the end.

    `conf.patch` is the writer, and it matches column-0 lines only; the module
    also obeys an indented one (`_assigned`), in whichever section it sits.
    So every line the module reads as the key is first moved to column 0,
    where `conf.patch` rewrites it with the rest -- otherwise an indented copy,
    or a copy in another section (`value_in`), would stay in force (T145).
    Then every section is read back the module's way, and a text the module
    would read otherwise (`conf.patch` also splits at a form feed, which
    `fgets` does not) is refused rather than written.

    Raises:
        InstallerError: `conf.patch`'s own refusals.
        PoolResetError: the module would not read `value` from the result.
    """
    native_block = entry.install.native
    cmangos = native_block.cmangos if native_block is not None else None
    table = cmangos.conf.files.get(bot_population.CONF_NAME) if cmangos is not None else None
    lines = text.split("\n")
    for index, line in enumerate(lines):
        if _assigned(line) is not None:
            lines[index] = line.lstrip(_SPACE)
    patched = conf.patch(
        "\n".join(lines),
        ConfPatch(
            keys={KEY: value},
            match_commented=table.match_commented if table is not None else False,
        ),
        {},
    )
    # By meaning (`parse`), not by spelling: a value written back as it was
    # read -- `" once:T "` from inside quotes -- is written unquoted, and ACE
    # then trims the padding the module would have trimmed anyway.
    if {parse(v) for v in values_in(patched)} != {parse(value)}:
        raise PoolResetError(
            f"{bot_population.CONF_NAME} has a line Yu'lon cannot rewrite so that the bots "
            f"module reads {KEY} = {value}; set it by hand"
        )
    return patched


def asks_for_a_rebuild(value: str) -> bool:
    """Is `value` a rebuild request to the module (`parse`): a valid `once:<token>`, or `always`?"""
    return parse(value).mode in ("once", "always")


def put_back_file(
    entry: CatalogEntry,
    server_dir: Path,
    backup: Path,
    target: Path,
    put: Callable[[Path, Path], None],
) -> str | None:
    """Put a Tuning backup back over `target` with `put`, never arming a rebuild (T145).

    Every route that writes a backup's content back comes through here on
    Tortoise: the Playerbots card's Revert, the raw editor's Revert
    (`tuning.restore`) and Undo the last reset (`reset_defaults.restore`). A
    backup is a copy of the file as it was when it was taken, `once:<token>`
    included when a request was pending then; the Maintenance restore since
    may have set the key to off (`before_restore`) and loaded databases with no
    record of that rebuild. Putting the backup back whole re-armed it, and the
    next start deleted the restored bots.

    So a backup that asks for a rebuild in ANY section (`parse`: a valid
    `once:<token>`, or `always`) never supplies the key (round 4). It goes back
    with every one of its key lines replaced by the CURRENT file's, section by
    section, byte for byte (`_with_key_lines_of`): a section the file does not
    set the key in gets no key line, and a file that sets it nowhere leaves
    none -- off. No value stands for the file as a whole: its sections may
    disagree, and the module may be reading any of them (`value_in`), so any
    choice of one could swap the request it acts on for another (Codex, round
    3). A backup with no request anywhere is `put` whole, as before, and so is
    one whose key lines are the file's own. Only `aiplayerbot.conf` is the
    module's bot config; any other file is `put` untouched. A current file
    that cannot be read has no key lines to keep.

    Returns the report's sentence when the key was kept, else None.

    `put` is used for any other file only. `aiplayerbot.conf` is written from
    the bytes read here, by `_replace_if_unchanged`, whichever the route: an
    atomic replace, where the card's and the raw editor's `tuning.restore` was
    a `copy2` straight onto the file; the backup's mode and times on a whole
    copy, as `copy2` and `reset_defaults.restore` gave; the file's own mode
    and a fresh time on a rewritten one, as `conf.replace_file` gave.

    Raises:
        OSError: nothing was written -- a backup or file that could not be
            read, one that asks for a rebuild and cannot be rewritten (not
            UTF-8 text), a file that changed while this ran
            (`CHANGED_WHILE_PUTTING_BACK`), or the write's own failure.
    """
    if target != server_dir / bot_population.CONF_FILE:
        put(backup, target)
        return None
    # One read of each, and those bytes decide AND are written (round 5): a
    # backup opened twice could say one thing to the question and another to
    # the copy, and a file whose key lines were read must still be that file
    # when the result replaces it (`_replace_if_unchanged`).
    raw = backup.read_bytes()
    before = _read_if_there(target)
    # Replacement characters only for the question; the answer that writes decodes strictly.
    requests = [
        v for v in values_in(raw.decode("utf-8", errors="replace")) if asks_for_a_rebuild(v)
    ]
    if not requests:
        _replace_if_unchanged(target, raw, before, like=backup)
        return None
    restored = requests[0]
    try:
        backup_text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise OSError(
            f"{backup.name} is not UTF-8 text, and it asks the bots module to rebuild the "
            f"random bots ({KEY} = {restored}), so Yu'lon will not put it back: {exc}"
        ) from exc
    try:
        current = (before or b"").decode("utf-8")
    except UnicodeDecodeError as exc:
        logger.warning(f"could not read {target} before putting a backup back: {exc}")
        current = ""
    text = _with_key_lines_of(current, backup_text)
    if text == backup_text:
        _replace_if_unchanged(target, raw, before, like=backup)
        return None
    _replace_if_unchanged(target, text.encode("utf-8"), before, like=None)
    logger.info(f"put {backup.name} back over {target} with its {KEY} lines kept as they were")
    return REQUEST_NOT_PUT_BACK.format(file=target.name, restored=restored)


def _read_if_there(path: Path) -> bytes | None:
    """The file's bytes, or None when there is no file. Raises any other `OSError`."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _replace_if_unchanged(
    target: Path, data: bytes, before: bytes | None, *, like: Path | None
) -> None:
    """Put `data` in place of `target` atomically, if `target` still holds `before`. Round 5.

    `data` goes to an owner-only sibling first (`tuning.private_copy`'s rule:
    the conf holds the database password), one made for THIS call by
    `tempfile.mkstemp` (round 6, Codex): a fixed name that every call unlinked
    and re-created let a second put-back remove the first one's temp and stage
    its own there, and the first then renamed the second's bytes into place.
    Only this call's own temp is ever removed. Its name starts with a dot and
    ends `.yulon-tmp`, so `tuning.backups_of()` (`<name>.<stamp>....bak`) never
    lists it. `like` given: that file's mode and
    times (`shutil.copystat`, only its metadata is read again), which is what
    the routes' copies of a whole backup gave. None: the file's own mode, as
    `conf.replace_file` keeps it, owner-writable.

    Then `target` is read again and compared with `before`, the bytes its key
    lines were taken from; changed -- another press, a restore clearing the
    request, a hand edit -- the temp is removed and nothing is written. That
    shrinks the window to one read and one rename; without a lock every writer
    honours it cannot be closed, and no such lock exists across the app, the
    server and an editor. Refusing rather than retrying: what to put back over
    a file that moved is the person's call.

    Raises:
        OSError: nothing was written.
    """
    try:
        mode: int | None = stat.S_IMODE(target.stat().st_mode)
    except OSError:
        mode = None
    # 0600 and O_EXCL: `mkstemp`'s own guarantees, the ones `private_copy` asks for.
    fd, name = tempfile.mkstemp(
        dir=target.parent, prefix=f".{target.name}.", suffix=PUT_BACK_TEMP_SUFFIX
    )
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        if like is not None:
            shutil.copystat(like, tmp)
        elif mode is not None:
            os.chmod(tmp, mode | stat.S_IWUSR)
        if _read_if_there(target) != before:
            raise OSError(CHANGED_WHILE_PUTTING_BACK.format(file=target.name))
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


PUT_BACK_TEMP_SUFFIX = ".yulon-tmp"
"""How a Tuning backup being put back ends its (per-call) name until the rename lands."""

CHANGED_WHILE_PUTTING_BACK = (
    "{file} changed on disk while the copy was being put back, so nothing was written; press "
    "the button again to put it back over the file as it is now"
)
"""`_replace_if_unchanged`'s refusal: the routes put it in their own report line."""


def _with_key_lines_of(current: str, backup: str) -> str:
    """`backup` with its key lines swapped for `current`'s, section by section. Pure.

    Every other line of `backup` is kept byte for byte. In each section, the
    current file's key lines take the place of the backup's first one; a
    section the backup has without the key gets them under its first header;
    the current file's lines before any header go first; a section only the
    current file has is appended, its header with it.
    """
    mine: dict[str, list[str]] = {}
    headers: dict[str, str] = {}
    for section, line, header in _lines_by_section(current):
        if header:
            headers.setdefault(section, line)
        elif _assigned(line) is not None:
            mine.setdefault(section, []).append(line)
    lines = _lines_by_section(backup)
    trailing = bool(lines) and lines[-1][1] == ""
    if trailing:
        lines.pop()
    keyed = {s for s, line, header in lines if not header and _assigned(line) is not None}
    out: list[str] = []
    placed: set[str] = set()
    for section, line, header in lines:
        if not header and _assigned(line) is not None:
            if section not in placed:
                out += mine.get(section, [])
                placed.add(section)
            continue
        out.append(line)
        if header and section not in keyed and section not in placed:
            out += mine.get(section, [])
            placed.add(section)
    if "" not in placed:
        out = mine.get("", []) + out
        placed.add("")
    for section, kept in mine.items():
        if section not in placed:
            out += [headers[section], *kept]
    if trailing:
        out.append("")
    return "\n".join(out)


@dataclass(frozen=True)
class TakenBack:
    """A pending request `before_restore()` set to off: what it said, and the report's line."""

    previous: str

    @property
    def note(self) -> str:
        return RESTORE_CLEARED


def put_back(entry: CatalogEntry, server_dir: Path, previous: str) -> None:
    """Write `previous` back after a restore that loaded nothing; no backup (`take_back`).

    Raises:
        PoolResetError: nothing was written.
    """
    _write_value(entry, server_dir, previous, backup=False)
    logger.info(f"put {entry.id}'s random-bot rebuild request back ({KEY} = {previous})")


def before_restore(entry: CatalogEntry, server_dir: Path) -> TakenBack | None:
    """Take a pending `once:` request back before a restore; what it was, or None.

    A restore replaces the characters database -- the bots, AND the module's
    record of the last applied generation. So a request left in the conf (a
    part-way failure, a Stop while watching, a watch that ran out, a restart
    that could not be confirmed, or anybody's own `once:`) would be new again
    after the restore, and the next start would delete the restored bots. Any
    token, not just Yu'lon's. `always` is the user's own and is left alone:
    `restore_warning()` says so in the plan. No backup (`take_back`).

    Raises:
        PoolResetError: the file could not be read or written; the restore
            must not run with the request armed.
    """
    try:
        value = setting(server_dir)
    except (OSError, UnicodeDecodeError) as exc:
        raise PoolResetError(_restore_refused(exc)) from exc
    if parse(value).mode != "once":
        return None
    try:
        take_back(entry, server_dir)
    except PoolResetError as exc:
        raise PoolResetError(_restore_refused(exc)) from exc
    return TakenBack(value)


def after_a_failed_restore(
    entry: CatalogEntry, server_dir: Path, taken: TakenBack, *, loaded: bool | None
) -> str:
    """A restore raised after `before_restore()`: put the request back if nothing loaded.

    `loaded` is whether the restore's marker appeared (None: could not tell).
    Only a restore that loaded nothing puts the request back: once loading has
    begun, the characters database may already be the backup's, which carries
    no record of that rebuild, and re-arming it would delete the restored bots.
    Returns the sentence the failure is reported with.
    """
    if loaded is False:
        try:
            put_back(entry, server_dir, taken.previous)
        except PoolResetError as exc:
            return (
                f"Nothing was restored, but the random-bot rebuild request could not be put back "
                f"({exc}): set {KEY} = {taken.previous} in aiplayerbot.conf by hand, or a rebuild "
                "that stopped part of the way through is not finished at the next start."
            )
        return (
            f"The random-bot rebuild request was put back as it was ({KEY} = {taken.previous}), "
            "since nothing was restored."
        )
    how = (
        "the restore had started loading"
        if loaded
        else "Yu'lon could not tell whether the restore had started loading"
    )
    return (
        f"The random-bot rebuild request stays off (it was {KEY} = {taken.previous}): {how}, "
        "and the restored databases carry no record of that rebuild, so re-arming it would "
        "delete the restored bots."
    )


def restore_warning(server_dir: Path) -> str | None:
    """The plan's warning when the conf says `always`, which a restore does not change."""
    try:
        return ALWAYS_BEFORE_RESTORE if parse(setting(server_dir)).mode == "always" else None
    except (OSError, UnicodeDecodeError):
        return None


def _restore_refused(exc: Exception) -> str:
    return (
        "The restore was not started: the random-bot rebuild request in aiplayerbot.conf "
        f"could not be set back to off ({exc}), and restoring with it armed would delete the "
        f"restored bots at the next start. Set {KEY} to off yourself, then restore again."
    )


# -- reading the world log ---------------------------------------------------------------------
#
# The module's format strings, verbatim from f858f9c9 (`runtime/RandomBotPoolReset.cpp`
# and `runtime/RandomBotService.cpp`). Searched, never anchored: `docker logs` puts
# the core's time stamp in front of each line.

_SCHEDULED = re.compile(
    r"TortoiseBots: random pool generation '(?P<token>[^']*)'; reset scheduled for "
    r"(?P<characters>\d+) characters on (?P<accounts>\d+) managed accounts"
)
_PROGRESS = re.compile(
    r"TortoiseBots: random pool reset progress: (?P<done>\d+)/(?P<total>\d+) characters deleted"
)
_VERIFIED = re.compile(
    r"TortoiseBots: random pool reset verified: 0 characters remain on (?P<accounts>\d+) "
    r"managed accounts"
)
_APPLIED = re.compile(
    r"TortoiseBots: random pool generation '(?P<token>[^']*)' applied; pool rebuild starts now"
)
_ALREADY = re.compile(
    r"TortoiseBots: random pool generation '(?P<token>[^']*)' already applied; reset skipped"
)
_OFF = re.compile(r"TortoiseBots: random pool reset off; ")
_AUTOCREATE = re.compile(
    r"TortoiseBots: random pool generation (?:'(?P<token>[^']*)'|\S+) needs "
    r"AiPlayerbot\.RandomBotAutoCreate=1 to refill the pool; no reset was scheduled"
)
_SERVICE_OFF = re.compile(
    r"TortoiseBots: AiPlayerbot\.RandomBotPoolReset requests a pool rebuild, but the "
    r"random-bot service is disabled"
)
_INVALID = re.compile(
    r"TortoiseBots: AiPlayerbot\.RandomBotPoolReset is invalid \((?P<reason>.*)\); "
    r"no reset was scheduled"
)
_PART_WAY = re.compile(r"TortoiseBots: random pool reset (?:failed|stopped): (?P<reason>.*)")
"""`Fail()` and `DrivePoolReset()`'s echo of it: the reset had STARTED (it was scheduled)."""
_BEFORE_ANY = re.compile(
    r"TortoiseBots: random pool reset aborted before any deletion: (?P<reason>.*)"
)
_NOT_STARTED = re.compile(
    r"TortoiseBots: random pool (?:reset skipped|reset disabled for this start|"
    r"summary unavailable): (?P<reason>.*)"
)
_GUILD = re.compile(
    r"guild '(?P<guild>.*)' is led by pool character \d+ but holds a member outside the managed "
    r"pool \(character \d+(?: '(?P<member>[^']*)')?\)"
)
_SESSION = re.compile(r"pool character (?P<name>\S+) \(\d+\) (?:is being played|gained a network)")

DONE = (
    "Done: every old random bot is gone and the pool is being made again. The new bots log "
    "in over the next few minutes."
)
NOTHING_DELETED = "Nothing was deleted."
TAKEN_BACK = (
    f"Yu'lon set {KEY} back to off, so nothing will happen at the next restart; press "
    "Rebuild random bots… again when ready."
)
APPLIED_TAKEN_BACK = (
    f"{KEY} is back to off, so neither a later restart nor restoring a backup taken before "
    "this rebuild makes it happen again."
)
WHO_CLEARS_IT = (
    f"Yu'lon sets it back to off before any restore from the Maintenance tab, and once the "
    f"Bots tab shows the new bots you can set {KEY} to off yourself."
)
"""What every message that LEAVES the request says (T144 round 4): nothing else clears it."""
FINISHES_AT_NEXT_START = (
    "The request stays in aiplayerbot.conf until the rebuild has finished, so the bots module "
    f"finishes it at the next start of the server; do not set {KEY} to off before then, or the "
    f"bots stay half rebuilt. {WHO_CLEARS_IT}"
)
STILL_RUNNING = (
    "The rebuild may still be running, so the request stays in aiplayerbot.conf until the "
    f"rebuild has finished. {WHO_CLEARS_IT}"
)
RESTORE_CLEARED = (
    "The random-bot rebuild request in aiplayerbot.conf was set back to off: restoring this "
    "backup replaces the bot characters, so a pending rebuild would only delete the restored "
    "ones."
)
"""The restore report's line when `before_restore()` took a `once:` back (round 4)."""
REQUEST_NOT_PUT_BACK = (
    "{file}: the copy put back asked the bots module to rebuild the random bots "
    f"({KEY} = {{restored}}), so that one setting was not put back and stays as the file had "
    "it: a rebuild request brought back from an older copy would delete bots a restore brought "
    "back, at the next start. Every other setting is the copy's. To rebuild them, press "
    "Rebuild random bots… on the Bots tab."
)
"""The report's line when `put_back_file` kept the key (T145)."""
ALWAYS_BEFORE_RESTORE = (
    f"aiplayerbot.conf says {KEY} = always, so the next start after this restore deletes and "
    "rebuilds every random bot, the restored ones included. Yu'lon leaves that setting alone; "
    "set it to off first if you want the restored bots kept."
)
"""The restore plan's warning for the one value Yu'lon never writes and never clears."""


@dataclass(frozen=True)
class Watch:
    """What the world log says so far about ONE rebuild request."""

    seen: tuple[str, ...] = ()
    """Milestones in plain words, in the order the module logged them."""
    final: Final | None = None
    """None while the reset is still going (or has not started)."""
    said: str = ""
    """The final sentence, when there is one. A refusal's lacks the take-back: the job adds it."""


def read_log(text: str, token: str, *, this_run_only: bool = True) -> Watch:
    """Read the module's reset lines out of `text` for `token`. Pure.

    The first line that ends the reset decides; the milestones before it are
    kept. A line about another token is another rebuild's and is skipped.

    **Fail closed on a log that is not this run's alone** (`docker.RunLog`):
    then only a line that names THIS token can say anything -- scheduled,
    applied, already applied, or the AutoCreate refusal. A token-less line
    (progress, a refusal, "reset off") may be an older run's and is not read.
    """
    seen: list[str] = []
    last_progress: tuple[str, str] | None = None
    for line in text.splitlines():
        if "TortoiseBots:" not in line:
            continue
        if (m := _SCHEDULED.search(line)) and m["token"] == token:
            seen.append(
                f"The server is rebuilding the random bots: {m['characters']} bot characters "
                f"on {m['accounts']} bot accounts are being deleted…"
            )
        elif (m := _APPLIED.search(line)) and m["token"] == token:
            return Watch(tuple(seen), "applied", DONE)
        elif (m := _ALREADY.search(line)) and m["token"] == token:
            return Watch(
                tuple(seen), "applied", "This rebuild had already run; nothing more to do."
            )
        elif (m := _AUTOCREATE.search(line)) and (this_run_only or m["token"] == token):
            return Watch(
                tuple(seen),
                "refused",
                "The server did not rebuild the random bots: it needs "
                "AiPlayerbot.RandomBotAutoCreate = 1 in aiplayerbot.conf to make them again. "
                f"{NOTHING_DELETED} Set it to 1 before trying again.",
            )
        elif not this_run_only:
            continue
        elif m := _PROGRESS.search(line):
            last_progress = (m["done"], m["total"])
            seen.append(f"Deleted {m['done']}/{m['total']} bot characters…")
        elif m := _VERIFIED.search(line):
            seen.append(
                f"Checked: 0 left on the {m['accounts']} bot accounts. Making the bots again…"
            )
        elif _ALREADY.search(line) or _OFF.search(line):
            return Watch(tuple(seen), "not-read", _not_read())
        elif _SERVICE_OFF.search(line):
            return Watch(
                tuple(seen),
                "refused",
                "The server did not rebuild the random bots: its random-bot service is switched "
                "off (AiPlayerbot.RandomBotAutologin and AiPlayerbot.RandomBotAutoCreate are "
                f"both 0 in aiplayerbot.conf). {NOTHING_DELETED}",
            )
        elif m := _INVALID.search(line):
            return Watch(
                tuple(seen),
                "refused",
                f"The server could not read the rebuild request ({m['reason']}). "
                f"{NOTHING_DELETED}",
            )
        elif m := _PART_WAY.search(line):
            return Watch(tuple(seen), "part-way", _part_way(m["reason"], last_progress))
        elif m := _BEFORE_ANY.search(line):
            return Watch(tuple(seen), "refused", _before_any(m["reason"]))
        elif m := _NOT_STARTED.search(line):
            return Watch(
                tuple(seen),
                "refused",
                f"The server did not rebuild the random bots: {m['reason']}. {NOTHING_DELETED}",
            )
    return Watch(tuple(seen))


_PLANNING = (
    _SCHEDULED,
    _OFF,
    _ALREADY,
    _AUTOCREATE,
    _INVALID,
    _NOT_STARTED,
    _BEFORE_ANY,
    _SERVICE_OFF,
)
"""Every line `PlanAtStartup` (and the service's start right after it) ends its plan with."""


def _planned(text: str, token: str) -> Literal["ours", "earlier", "other"] | None:
    """Has this run planned its pool reset, and was it about `token`? Pure.

    "ours": some module line names the token (it read the request). "earlier":
    it scheduled a reset for a DIFFERENT token -- an earlier request left armed
    (part-way, timeout, Stop) that this run is carrying out right now, so ours
    must stay armed: `off` would also stop that reset's resume if it stops
    part-way (T144 review round 9). "other": any other planning outcome without
    it (the run planned without the request and never reads the key again).
    None: no planning line yet.
    """
    earlier = other = False
    for line in text.splitlines():
        if "TortoiseBots:" not in line:
            continue
        if token in line:
            return "ours"
        if _SCHEDULED.search(line):
            earlier = True
        elif any(pattern.search(line) for pattern in _PLANNING):
            other = True
    if earlier:
        return "earlier"
    return "other" if other else None


def _not_read() -> str:
    return (
        "The server started without reading this rebuild request, so nothing was rebuilt. "
        "Check that aiplayerbot.conf in this server's etc folder is the one it reads."
    )


def _before_any(reason: str) -> str:
    """`aborted before any deletion`: the module's reason, naming the one thing to fix."""
    if m := _GUILD.search(reason):
        member = f" ({m['member']})" if m["member"] else ""
        return (
            f"The server refused to rebuild the random bots: the bot guild '{m['guild']}' has a "
            f"member who is not a bot{member}. {NOTHING_DELETED} Take that member out of the "
            "guild before trying again."
        )
    if m := _SESSION.search(reason):
        return (
            f"The server refused to rebuild the random bots: someone is playing the bot "
            f"character {m['name']}. {NOTHING_DELETED} Log that character out before trying "
            "again."
        )
    return f"The server refused to rebuild the random bots: {reason}. {NOTHING_DELETED}"


def _part_way(reason: str, last_progress: tuple[str, str] | None) -> str:
    """`reset failed:` -- after the reset began, so characters may already be gone."""
    how_far = (
        f"after {last_progress[0]} of {last_progress[1]} bot characters were deleted"
        if last_progress is not None
        else "after it had started, before the log showed any bot character deleted"
    )
    if m := _SESSION.search(reason):
        why = f"someone logged in on the bot character {m['name']} during the rebuild"
    else:
        why = reason
    return (
        f"The rebuild stopped part of the way through, {how_far}: {why}. {FINISHES_AT_NEXT_START}"
    )


# -- the job -----------------------------------------------------------------------------------


def _wait(seconds: float, cancel: threading.Event | None) -> None:
    """Sleep, waking at once on a cancel."""
    if cancel is not None:
        cancel.wait(seconds)
    else:
        time.sleep(seconds)


@dataclass
class PoolRebuild:
    """The Bots tab's "Rebuild random bots…", bound to one Tortoise install.

    Every collaborator is a callable, so the flow is tested with the server
    replaced: `world_running` (`docker.world_running`), `channels` (T123's
    SOAP-then-console list), `restart` (stop, then start), `world_log`
    (`docker.current_run_log`), `clock` (the token's stamp).
    """

    entry: CatalogEntry
    server_dir: Path
    world_running: Callable[[], bool | None]
    channels: Callable[[], Sequence[Channel]]
    restart: Callable[[], object]
    world_log: Callable[[], docker.RunLog]
    module_moved: botpool.ModuleMoved = field(default_factory=botpool.ModuleMoved)
    world_started: Callable[[], str] = lambda: ""
    """The world container's `StartedAt`, as Docker prints it (`docker.started_at`); "" when
    unreadable. Compared for equality only: never against this app's clock."""
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    pause: Callable[[float, threading.Event | None], None] = _wait
    monotonic: Callable[[], float] = time.monotonic
    timeout_s: float = WATCH_TIMEOUT_S
    poll_s: float = POLL_S

    def take_module_moved(self) -> botpool.Move | None:
        """Did the last update press move TortoiseBots, and is its restart owed? Answered once."""
        return self.module_moved.take()

    def before_restore(self) -> TakenBack | None:
        """`before_restore()` for this install; runs on the restore's worker."""
        return before_restore(self.entry, self.server_dir)

    def after_a_failed_restore(self, taken: TakenBack, *, loaded: bool | None) -> str:
        """`after_a_failed_restore()` for this install; runs on the restore's worker."""
        return after_a_failed_restore(self.entry, self.server_dir, taken, loaded=loaded)

    def restore_warning(self) -> str | None:
        """`restore_warning()` for this install; runs on the plan's worker."""
        return restore_warning(self.server_dir)

    def put_back_file(
        self, backup: Path, target: Path, put: Callable[[Path, Path], None]
    ) -> str | None:
        """`put_back_file()` for this install: a Tuning backup back, never arming a rebuild."""
        return put_back_file(self.entry, self.server_dir, backup, target, put)

    def restart_owed_now(self, cancel: threading.Event | None = None) -> Iterator[str]:
        """The restart T123's enrolment is owed, on its own: the offer was declined (T144)."""
        yield "Restarting the server so the bots enrolled during the update log in…"
        try:
            self.restart()
        except StartRefused as exc:
            # T197: Restart is refused too; the refusal names its own repair.
            raise PoolResetError(
                f"{botpool.RESTART_REFUSED} {exc} The enrolled bots log in at any later start."
            ) from exc
        except Exception as exc:  # noqa: BLE001 - one press left, said in one sentence
            raise PoolResetError(
                f"The restart failed ({exc}). Restart the server from the Server tab; the "
                "enrolled bots log in at any later start."
            ) from exc
        yield "Restarted. The bots come online over the next few minutes."

    def rebuild(
        self,
        *,
        backup: Callable[[], object] | None = None,
        cancel: threading.Event | None = None,
        restart_owed: bool = False,
    ) -> Iterator[str]:
        """Back up (if given) -> enrol -> write the key -> restart once -> watch the log.

        `restart_owed`: an update's enrolment is waiting on a restart. This
        flow's own restart covers it; a flow that fails before that restart
        runs it anyway (a Stop does not, like T123's own cancel).

        Raises:
            PoolResetError: a server no start may run on (T197: nothing was done), a
                backup that failed (nothing else was done), a key that could not be
                written, a failed restart, or a refusal the module logged.
        """
        # T197 fix round 4: the restart this ends in would be refused, and by then the
        # key is written and armed for the first start after the repair. Asked first,
        # of the folder (`native.owed_start_refusal`, the one start guard a Tortoise
        # server has), so nothing is backed up, enrolled, written or stopped.
        refused = native.owed_start_refusal(self.server_dir)
        if refused is not None:
            raise PoolResetError(
                f"{refused} The random bots were not rebuilt: nothing was written and the "
                "server was not stopped."
            )
        try:
            if backup is not None:
                yield "Backing up the databases first… this can take minutes on a full world."
                try:
                    report = backup()
                except Exception as exc:  # noqa: BLE001 - any failure stops, in one sentence
                    raise PoolResetError(
                        f"Backup failed — the random bots were not rebuilt: {exc}"
                    ) from exc
                yield f"Backup saved to {getattr(report, 'directory', 'the backups folder')}."
            yield from self._enrol(cancel)
            if _cancelled(cancel):
                yield "Stopped before anything was written. The random bots were not rebuilt."
                if restart_owed:
                    yield OWED_AFTER_A_STOP
                return
            token = new_token(self.clock())
            # Which run is up (or was last up) BEFORE the write: after a failed
            # stop, "the same run" is decided by this stamp, never by a clock.
            started_before = self._ask(self.world_started) or ""
            written = write_key(self.entry, self.server_dir, token)
        except PoolResetError as first:
            if restart_owed and not _cancelled(cancel):
                try:
                    yield from self.restart_owed_now(cancel)
                except PoolResetError as second:
                    # Both, first failure first: the owed restart's error must not
                    # replace the one that stopped the rebuild.
                    raise PoolResetError(
                        f"{first} The restart the enrolled bots were waiting for failed too: "
                        f"{second}"
                    ) from first
            raise
        yield (
            f"Wrote {KEY} = once:{token} into {written.file.name}; the file as it was is beside "
            f"it at {written.backup.name}."
        )
        if _cancelled(cancel):
            yield f"Stopped before the restart. {self._take_back()}"
            if restart_owed:
                yield OWED_AFTER_A_STOP
            return
        yield "Restarting the server (anyone playing is disconnected)…"
        try:
            self.restart()
        except Exception as exc:  # noqa: BLE001 - the request is written; say what is left
            booting = yield from self._after_a_failed_restart(
                exc, token=token, started_before=started_before, restart_owed=restart_owed
            )
        else:
            booting = False
            yield "Restarted. Watching the server's log for the bots module's rebuild…"
        yield from self._watch(token, cancel, booting=booting, restart_owed=restart_owed)

    def _after_a_failed_restart(
        self,
        exc: Exception,
        *,
        token: str,
        started_before: str,
        restart_owed: bool = False,
    ) -> Generator[str, None, bool]:
        """A restart that raised may still have brought the world up, and the world reads the
        request as it starts -- so ask before taking it back.

        `restart_world` is stop + `start_staged`, which raises when `compose up`
        exits non-zero or db/auth are missing afterwards: the worldserver can be
        up by then, have read the key, and be deleting bots. Taking the request
        back would then be a lie ("nothing will happen at the next restart"),
        and on a part-way failure it would stop the module's resume too.

        Up: say so and return, and the caller watches the log as usual. Down:
        take it back and raise. Unknown (None, or the check raised): keep it,
        and raise saying it may run at the next start.
        """
        if isinstance(exc, botpool.StopFailed):
            return (yield from self._after_a_failed_stop(exc, token, started_before, restart_owed))
        if isinstance(exc, StartRefused):
            # T197: refused before the stop, so the running world never reads the key.
            raise PoolResetError(f"{botpool.RESTART_REFUSED} {exc} {self._take_back()}") from exc
        try:
            up = self.world_running()
        except Exception as check:  # noqa: BLE001 - an unknown answer is its own branch
            logger.warning(f"could not tell whether the world came up after a restart: {check}")
            up = None
        if up is True:
            yield (
                f"The restart reported an error ({exc}), but the world came up. Watching the "
                "server's log for the bots module's rebuild…"
            )
            return False
        if up is False:
            raise PoolResetError(
                f"The restart failed ({exc}) and the world is down. {self._take_back()}"
            ) from exc
        raise PoolResetError(
            f"The restart failed ({exc}), and Yu'lon could not tell whether the world came up. "
            "The request stays in aiplayerbot.conf until the rebuild has finished, so it may run "
            f"at the next start of the server. {WHO_CLEARS_IT} To call the rebuild off before "
            f"then, set {KEY} to off."
        ) from exc

    def _after_a_failed_stop(
        self, exc: botpool.StopFailed, token: str, started_before: str, restart_owed: bool
    ) -> Generator[str, None, bool]:
        """The STOP raised, so nothing was started: decide by what the run itself logged.

        The module reads the request once per run, late in world start-up:
        `BotHostAdapter::OnStartup` calls `sPlayerbotAIConfig.Initialize()` (which
        reads the key) and then `RandomBotService::Initialize()` one after the
        other (BotHostAdapter.cpp:85-86), and the service's `PlanAtStartup`
        (RandomBotService.cpp:237) acts on it -- ~80-90 s after the container
        starts on the live gate. So a container start time proves nothing on
        its own -- a run still booting reads a key written after its start --
        and this app's clock is never compared with the daemon's. Instead:

        * a DIFFERENT `StartedAt` than before the write, world up: a new run,
          which read the request -- watched as for a started world;
        * the same run (same `StartedAt`, up): its log decides. A line naming
          our token -> it read it, watched. A planning outcome without our
          token (`_planned`) -> it planned before the write and never reads
          the key again: called off. No planning line yet in a log scoped to
          this run -> it is still starting and may read the request as it
          finishes: WATCHED (`_watch(booting=True)`), which ends as called off
          if the run then plans without our token. No readable log -> KEEP;
        * down, or `StartedAt` unreadable: the last run's log, the same way --
          our token -> kept, and said; planning without it AND `StartedAt`
          unchanged -> called off; anything else -> kept.

        At `TortoiseBots.LogLevel` 0 the module prints no "reset off" line
        (`TB_LOG_BASIC`), so a run with nothing to plan logs no planning
        outcome and this keeps the request: the safe direction. The catalog
        writes LogLevel 1.

        An owed enrolment restart is said separately and never keeps the
        request armed; when the take-back itself failed, the order is spelled
        out, because that restart would fire the rebuild.
        """
        up = self._ask(self.world_running)
        started_now = self._ask(self.world_started) or ""
        same_run = bool(started_now) and started_now == started_before
        if up is True and started_now and not same_run:
            yield (
                f"The server could not be stopped ({exc}), but it has started again since the "
                "request was written, so that run read it. Watching the server's log for the "
                "bots module's rebuild…"
            )
            return False
        log = self._ask(self.world_log)
        seen = _planned(log.text, token) if log is not None else None
        if seen == "ours":
            if up is True and same_run:
                yield (
                    f"The server could not be stopped ({exc}), but the run that is up read the "
                    "request as it started. Watching the server's log for the bots module's "
                    "rebuild…"
                )
                return False
            raise PoolResetError(
                f"The server could not be stopped ({exc}), and its last run read the request, "
                "so the rebuild runs or resumes at its next start. The request stays in "
                f"aiplayerbot.conf until the rebuild has finished. {WHO_CLEARS_IT} To call the "
                f"rebuild off before then, set {KEY} to off.{self._owed(restart_owed)}"
            ) from exc
        scoped = log is not None and log.this_run_only
        if seen == "earlier" and same_run and scoped:
            raise PoolResetError(
                f"The server could not be stopped ({exc}). {EARLIER_RUNNING}"
                f"{self._owed(restart_owed, taken=False)}"
            ) from exc
        if seen == "other" and same_run and scoped:
            said, taken = self._called_off(f"The server could not be stopped ({exc})")
            raise PoolResetError(f"{said}{self._owed(restart_owed, taken=taken)}") from exc
        if seen is None and up is True and same_run and scoped:
            yield (
                f"The server could not be stopped ({exc}), and it is still starting up, so it may "
                "read the request as it finishes. Watching the server's log for the bots "
                "module's rebuild…"
            )
            return True
        when = (
            "so the rebuild runs at its next start"
            if up is False
            else "so the rebuild may start as the running server finishes starting, or at its "
            "next start"
        )
        raise PoolResetError(
            f"The server could not be stopped ({exc}), {when}. The request stays in "
            f"aiplayerbot.conf until the rebuild has finished. {WHO_CLEARS_IT} To call the "
            f"rebuild off before then, set {KEY} to off.{self._owed(restart_owed)}"
        ) from exc

    @staticmethod
    def _owed(restart_owed: bool, *, taken: bool = True) -> str:
        """The owed enrolment restart, said on its own; with the order when the key is armed."""
        if not restart_owed:
            return ""
        if taken:
            return f" {OWED_AFTER_A_STOP}"
        return f" {OWED_KEY_OFF_FIRST}"

    def _called_off(self, why: str) -> tuple[str, bool]:
        """Take the request back; `why` first. The sentence, and whether it was taken."""
        said = self._take_back()
        if said != TAKEN_BACK:
            return f"{why}. {said}", False
        return (
            f"{why}, so the rebuild was called off: {KEY} is back to off and nothing will happen "
            "at a later restart; press Rebuild random bots… again when ready.",
            True,
        )

    @staticmethod
    def _ask(question: Callable[[], _T]) -> _T | None:
        """One docker reading, with a failure read as "unknown"."""
        try:
            return question()
        except Exception as exc:  # noqa: BLE001 - an unknown answer is its own branch
            logger.warning(f"a reading after a failed stop could not be taken: {exc}")
            return None

    def _take_back(self, *, applied: bool = False) -> str:
        """Set the key back to off; the sentence that says so, or what to do by hand.

        `applied`: the module has recorded this generation, so the request is
        spent -- taken back anyway, because the record lives in the characters
        database and restoring a backup taken before the rebuild rolls it back.
        """
        try:
            take_back(self.entry, self.server_dir)
        except PoolResetError as exc:
            if applied:
                return (
                    f"Yu'lon could not set {KEY} back to off ({exc}). Set it to off in "
                    "aiplayerbot.conf yourself before restoring a backup taken before this "
                    "rebuild, or that restore's next start rebuilds the bots again."
                )
            return (
                f"Yu'lon could not set {KEY} back to off ({exc}). Set it to off in "
                "aiplayerbot.conf yourself, or the random bots are rebuilt at the next restart."
            )
        return APPLIED_TAKEN_BACK if applied else TAKEN_BACK

    def _enrol(self, cancel: threading.Event | None) -> Iterator[str]:
        """T123's enrolment over the live channel, when the world is up to answer it."""
        if self.world_running() is not True:
            yield (
                "The server is not running, so older bot accounts cannot be enrolled first; the "
                "rebuild covers every bot account the bots module already has."
            )
            return
        yield "Making sure the bots module has enrolled every older bot account…"
        outcome = botpool.adopt_over(
            self.channels(), pause=lambda s: self.pause(s, cancel), cancel=cancel
        )
        if isinstance(outcome, botpool.Adopted):
            yield (
                f"Enrolled {outcome.count} older bot account(s)."
                if outcome.count
                else "Every bot account was already enrolled."
            )
        elif isinstance(outcome, botpool.Unconfirmed):
            yield (
                f"The enrol command was sent but its answer never came ({outcome.why}); the "
                "restart below loads whatever it enrolled."
            )
        elif outcome.why != botpool.CANCELLED:
            yield (
                botpool.console_steps(outcome.why)
                + " The rebuild goes on over the bots that are enrolled."
            )

    def _watch(
        self,
        token: str,
        cancel: threading.Event | None,
        *,
        booting: bool = False,
        restart_owed: bool = False,
    ) -> Iterator[str]:
        """Read the run's log until the module says how the rebuild ended, or time runs out.

        `booting`: a run that was up BEFORE the request was written and had not
        planned yet (`_after_a_failed_stop`). Its first planning outcome decides
        whether it read the request: one naming our token -> an ordinary
        watch from there; one without it (e.g. "reset off") -> it read the file
        before the write and never reads it again, so the request is called
        off. Read before `read_log`, which would call "reset off" a server that
        "started without reading this rebuild request" -- not what happened.
        """
        deadline = self.monotonic() + self.timeout_s
        said: set[str] = set()
        while True:
            log = self.world_log()
            if booting and log.this_run_only:
                seen = _planned(log.text, token)
                if seen == "earlier":
                    raise PoolResetError(
                        f"{EARLIER_RUNNING}{self._owed(restart_owed, taken=False)}"
                    )
                if seen == "other":
                    text, taken = self._called_off(BOOTED_WITHOUT_IT)
                    raise PoolResetError(f"{text}{self._owed(restart_owed, taken=taken)}")
                booting = seen is None
            watch = read_log(log.text, token, this_run_only=log.this_run_only)
            for line in watch.seen:
                if line not in said:
                    said.add(line)
                    yield line
            if watch.final == "applied":
                yield f"{watch.said} {self._take_back(applied=True)}"
                return
            if watch.final == "part-way":
                raise PoolResetError(watch.said)
            if watch.final is not None:
                raise PoolResetError(f"{watch.said} {self._take_back()}")
            if _cancelled(cancel):
                if booting:
                    yield (
                        "Stopped watching. The server has not read its rebuild setting yet: it "
                        "may read the request as it finishes starting, or at its next start; "
                        f"the {TORTOISE_CONSOLE_TAB} shows its lines. {STILL_RUNNING}"
                    )
                    return
                yield (
                    "Stopped watching. The rebuild carries on inside the server; the "
                    f"{TORTOISE_CONSOLE_TAB} shows its lines. {STILL_RUNNING}"
                )
                return
            if self.monotonic() >= deadline:
                yield (
                    "The bots module's rebuild lines were not seen yet. It may still be starting "
                    f"up: the {TORTOISE_CONSOLE_TAB} shows the server's log as it goes. "
                    f"{STILL_RUNNING}"
                )
                return
            self.pause(self.poll_s, cancel)


EARLIER_RUNNING = (
    "The running server is rebuilding the random bots for an earlier request right now, so this "
    "request stays in aiplayerbot.conf: once that rebuild has finished, the next start rebuilds "
    f"them once more for this one. Do not set {KEY} to off while the earlier rebuild is running, "
    "or it cannot finish if it stops part of the way through; after it has finished you can set "
    "it to off to skip the second rebuild."
)
"""A run that scheduled a different token: ours stays armed (T144 review round 9)."""

BOOTED_WITHOUT_IT = (
    "The running server finished starting without the request: it read aiplayerbot.conf before "
    "the request was written"
)
"""A booting run that planned without our token (round 9); `_called_off` finishes it."""

OWED_KEY_OFF_FIRST = (
    "The server was not restarted, so the bots enrolled during the update are not online yet: "
    f"set {KEY} to off FIRST, then restart the server from the Server tab. The enrolment is "
    "saved, and any later start loads them."
)
"""The owed restart when the rebuild request is still armed: restarting first would fire it."""

OWED_AFTER_A_STOP = (
    "The server was not restarted, so the bots enrolled during the update are not online yet: "
    "restart the server from the Server tab. The enrolment is saved, and any later start loads "
    "them."
)


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def for_entry(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    world_running: Callable[[], bool | None],
    channels: Callable[[], Sequence[Channel]],
    restart: Callable[[], object],
    world_log: Callable[[], docker.RunLog],
    module_moved: botpool.ModuleMoved,
    world_started: Callable[[], str] = lambda: "",
) -> PoolRebuild | None:
    """The press for an install whose bots module is compiled in and whose bot conf is known.

    None where either is missing: the request goes into the bot count's file,
    and the enrolment needs the module's checkout. Only the Tortoise factory
    calls this -- the key is TortoiseBots', and the other three games' bot
    modules have never heard of it.
    """
    if bot_population.where(entry) != (bot_population.CONF_FILE, "conf"):
        return None
    if botpool.module_dir(entry, server_dir) is None:
        return None
    return PoolRebuild(
        entry=entry,
        server_dir=server_dir,
        world_running=world_running,
        channels=channels,
        restart=restart,
        world_log=world_log,
        module_moved=module_moved,
        world_started=world_started,
    )
