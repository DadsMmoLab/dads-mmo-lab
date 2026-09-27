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

Nothing here imports Qt. `PoolRebuild.rebuild()` is a line source for the Bots
tab's log panel and runs on that panel's worker thread.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

from yulon import bot_population, docker, tuning
from yulon.catalog.catalog import CatalogEntry, ConfPatch
from yulon.catalog.families import conf
from yulon.catalog.installer import InstallerError
from yulon.channel import Channel
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
    request, key off.

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
    `conf.patch` (every other byte kept; the key replaced where it stands, or
    appended), `tuning.backup` beside it so the Tuning tab's Revert on the
    Playerbots card finds it (unless `backup` is False: `take_back`), and an
    atomic replace.
    """
    path = server_dir / bot_population.CONF_FILE
    native_block = entry.install.native
    cmangos = native_block.cmangos if native_block is not None else None
    table = cmangos.conf.files.get(bot_population.CONF_NAME) if cmangos is not None else None
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            before = handle.read()
        text = conf.patch(
            before,
            ConfPatch(
                keys={KEY: value},
                match_commented=table.match_commented if table is not None else False,
            ),
            {},
        )
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


_ACTIVE = re.compile(rf"^{re.escape(KEY)}\s*=\s*(?P<value>.*?)\s*$")


def setting(server_dir: Path) -> str:
    """What the install's `aiplayerbot.conf` asks of the module, as written: `off` if unset.

    The case is kept, because a `once:` token is compared case-sensitively by
    the module and may have to be written back exactly; compare the keyword
    with `.lower()`.

    Read the way `conf.patch` writes: active lines at column 0. A `once:` on
    ANY of them counts (the patch moves every copy at once). A missing file is
    `off`: nothing is asked of a module that has no config.

    Raises:
        OSError, UnicodeDecodeError: the file is there and could not be read.
    """
    path = server_dir / bot_population.CONF_FILE
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
    except FileNotFoundError:
        return "off"
    values = [m["value"] for line in text.splitlines() if (m := _ACTIVE.match(line.rstrip()))]
    for value in values:
        if value.lower().startswith("once:"):
            return value
    return values[-1] if values else "off"


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
    if not value.lower().startswith("once:"):
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
        return ALWAYS_BEFORE_RESTORE if setting(server_dir).lower() == "always" else None
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


def _planned(text: str, token: str) -> Literal["ours", "other"] | None:
    """Has this run planned its pool reset, and was it about `token`? Pure.

    "ours": some module line names the token (it read the request). "other":
    a planning outcome that does not name it (the run planned without it and
    never reads the key again). None: no planning line yet.
    """
    other = False
    for line in text.splitlines():
        if "TortoiseBots:" not in line:
            continue
        if token in line:
            return "ours"
        if any(pattern.search(line) for pattern in _PLANNING):
            other = True
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

    def restart_owed_now(self, cancel: threading.Event | None = None) -> Iterator[str]:
        """The restart T123's enrolment is owed, on its own: the offer was declined (T144)."""
        yield "Restarting the server so the bots enrolled during the update log in…"
        try:
            self.restart()
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
            PoolResetError: a backup that failed (nothing else was done), a key
                that could not be written, a failed restart, or a refusal the
                module logged.
        """
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
