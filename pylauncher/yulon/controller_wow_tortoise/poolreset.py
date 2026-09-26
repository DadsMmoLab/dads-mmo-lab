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
back to `off`, backed up like the write, so no later ordinary restart rebuilds
the bots by surprise. The one exception is a reset that failed PART-WAY
(`reset failed:`, logged after deletion may have begun): the module records the
generation only after success and resumes at the next start while the token is
still new, and `off` would stop that resume (`PlanAtStartup` returns on `Off`
before anything is planned), stranding half a pool. So there the request stays.
A watch that timed out, or was stopped, leaves it too: the rebuild may still be
running.

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
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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
    logger.info(f"asked {entry.id} to rebuild its random bots ({KEY} = once:{token}) in {path}")
    return KeyWritten(path, made, token)


def take_back(entry: CatalogEntry, server_dir: Path) -> Path:
    """Write `AiPlayerbot.RandomBotPoolReset = off`, backed up like the request; the backup.

    Raises:
        PoolResetError: nothing was written.
    """
    path, made = _write_value(entry, server_dir, "off")
    logger.info(f"took {entry.id}'s random-bot rebuild request back ({KEY} = off) in {path}")
    return made


def _write_value(entry: CatalogEntry, server_dir: Path, value: str) -> tuple[Path, Path]:
    """Set the key to `value` in the install's `aiplayerbot.conf`; the file and its backup.

    The bot count's writer (`bot_population.write`) in miniature: a fresh read,
    `conf.patch` (every other byte kept; the key replaced where it stands, or
    appended), `tuning.backup` beside it so the Tuning tab's Revert on the
    Playerbots card finds it, and an atomic replace.
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
        made = tuning.backup(path)
    except (OSError, UnicodeDecodeError, InstallerError, tuning.TuningError) as exc:
        raise PoolResetError(f"could not write {path.name}: {exc}") from exc
    try:
        conf.replace_file(path, text)
    except InstallerError as exc:
        made.unlink(missing_ok=True)
        raise PoolResetError(f"could not write {path.name}: {exc}") from exc
    return path, made


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
FINISHES_AT_NEXT_START = (
    f"The request stays in aiplayerbot.conf, so the bots module finishes the rebuild at the "
    f"next start of the server. Leave it there: setting {KEY} to off would stop that and leave "
    "the bots half rebuilt."
)
STILL_RUNNING = (
    "The rebuild may still be running, so the request stays in aiplayerbot.conf until it has "
    "finished."
)


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
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    pause: Callable[[float, threading.Event | None], None] = _wait
    monotonic: Callable[[], float] = time.monotonic
    timeout_s: float = WATCH_TIMEOUT_S
    poll_s: float = POLL_S

    def take_module_moved(self) -> botpool.Move | None:
        """Did the last update press move TortoiseBots, and is its restart owed? Answered once."""
        return self.module_moved.take()

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
            written = write_key(self.entry, self.server_dir, token)
        except PoolResetError:
            if restart_owed and not _cancelled(cancel):
                yield from self.restart_owed_now(cancel)
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
            raise PoolResetError(f"The restart failed ({exc}). {self._take_back()}") from exc
        yield "Restarted. Watching the server's log for the bots module's rebuild…"
        yield from self._watch(token, cancel)

    def _take_back(self) -> str:
        """Set the key back to off; the sentence that says so, or what to do by hand."""
        try:
            take_back(self.entry, self.server_dir)
        except PoolResetError as exc:
            return (
                f"Yu'lon could not set {KEY} back to off ({exc}). Set it to off in "
                "aiplayerbot.conf yourself, or the random bots are rebuilt at the next restart."
            )
        return TAKEN_BACK

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

    def _watch(self, token: str, cancel: threading.Event | None) -> Iterator[str]:
        deadline = self.monotonic() + self.timeout_s
        said: set[str] = set()
        while True:
            log = self.world_log()
            watch = read_log(log.text, token, this_run_only=log.this_run_only)
            for line in watch.seen:
                if line not in said:
                    said.add(line)
                    yield line
            if watch.final == "applied":
                yield watch.said
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
    )
