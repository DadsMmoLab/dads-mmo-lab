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
log for the module's lines. After an update that moved the module, T123 may
already have restarted the world for an enrolment; that restart came before
the key existed, so this flow's own restart is still the one that runs the
reset, and it is the only one this flow makes.

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

from yulon import bot_population, tuning
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

Final = Literal["applied", "refused", "not-read"]


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

    The bot count's writer (`bot_population.write`) in miniature: a fresh read,
    `conf.patch` (every other byte kept; the key replaced where it stands, or
    appended), `tuning.backup` beside it so the Tuning tab's Revert on the
    Playerbots card finds it, and an atomic replace.

    Raises:
        PoolResetError: nothing was written.
    """
    if not is_valid_token(token):
        raise PoolResetError(f"{token!r} is not a token the bots module accepts")
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
                keys={KEY: f"once:{token}"},
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
    logger.info(f"asked {entry.id} to rebuild its random bots ({KEY} = once:{token}) in {path}")
    return KeyWritten(path, made, token)


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
    r"TortoiseBots: random pool generation .* needs AiPlayerbot\.RandomBotAutoCreate=1 to "
    r"refill the pool; no reset was scheduled"
)
_SERVICE_OFF = re.compile(
    r"TortoiseBots: AiPlayerbot\.RandomBotPoolReset requests a pool rebuild, but the "
    r"random-bot service is disabled"
)
_INVALID = re.compile(
    r"TortoiseBots: AiPlayerbot\.RandomBotPoolReset is invalid \((?P<reason>.*)\); "
    r"no reset was scheduled"
)
_REFUSED = re.compile(
    r"TortoiseBots: random pool (?:reset failed|reset aborted before any deletion|"
    r"reset skipped|reset disabled for this start|summary unavailable): (?P<reason>.*)"
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
STILL_ASKED = (
    f"The request stays in aiplayerbot.conf, so the server tries again at its next start; to "
    f"call it off, set {KEY} back to off (Revert on the Tuning tab's Playerbots card puts the "
    "file back as it was)."
)


@dataclass(frozen=True)
class Watch:
    """What this run's world log says so far about ONE rebuild request."""

    seen: tuple[str, ...] = ()
    """Milestones in plain words, in the order the module logged them."""
    final: Final | None = None
    """None while the reset is still going (or has not started)."""
    said: str = ""
    """The final sentence, when there is one."""


def read_log(text: str, token: str) -> Watch:
    """Read the module's reset lines out of `text` for `token`. Pure.

    The first line that ends the reset decides; the milestones before it are
    kept. A line about another token is another rebuild's and is skipped.
    """
    seen: list[str] = []
    for line in text.splitlines():
        if "TortoiseBots:" not in line:
            continue
        if (m := _SCHEDULED.search(line)) and m["token"] == token:
            seen.append(
                f"The server is rebuilding the random bots: {m['characters']} bot characters "
                f"on {m['accounts']} bot accounts are being deleted…"
            )
        elif m := _PROGRESS.search(line):
            seen.append(f"Deleted {m['done']}/{m['total']} bot characters…")
        elif m := _VERIFIED.search(line):
            seen.append(
                f"Checked: 0 left on the {m['accounts']} bot accounts. Making the bots again…"
            )
        elif (m := _APPLIED.search(line)) and m["token"] == token:
            return Watch(tuple(seen), "applied", DONE)
        elif m := _ALREADY.search(line):
            if m["token"] == token:
                return Watch(
                    tuple(seen), "applied", "This rebuild had already run; nothing more to do."
                )
            return Watch(tuple(seen), "not-read", _not_read())
        elif _OFF.search(line):
            return Watch(tuple(seen), "not-read", _not_read())
        elif _AUTOCREATE.search(line) or _SERVICE_OFF.search(line):
            return Watch(
                tuple(seen),
                "refused",
                "The server did not rebuild the random bots: it needs "
                "AiPlayerbot.RandomBotAutoCreate = 1 in aiplayerbot.conf to make them again. "
                f"{NOTHING_DELETED} Set it to 1 and restart the server. {STILL_ASKED}",
            )
        elif m := _INVALID.search(line):
            return Watch(
                tuple(seen),
                "refused",
                f"The server could not read the rebuild request ({m['reason']}). "
                f"{NOTHING_DELETED}",
            )
        elif m := _REFUSED.search(line):
            return Watch(tuple(seen), "refused", _refusal(m["reason"], line))
    return Watch(tuple(seen))


def _not_read() -> str:
    return (
        "The server started without reading this rebuild request, so nothing was rebuilt. "
        f"Check that aiplayerbot.conf in this server's etc folder says {KEY} = once:…"
    )


def _refusal(reason: str, line: str) -> str:
    """The module's reason, in words, naming the one thing to fix where it names one."""
    if m := _GUILD.search(reason):
        member = f" ({m['member']})" if m["member"] else ""
        return (
            f"The server refused to rebuild the random bots: the bot guild '{m['guild']}' has a "
            f"member who is not a bot{member}. {NOTHING_DELETED} Take that member out of the "
            f"guild, then try again. {STILL_ASKED}"
        )
    if m := _SESSION.search(reason):
        return (
            f"The server refused to rebuild the random bots: someone is playing the bot "
            f"character {m['name']}. {NOTHING_DELETED} Log that character out, then try again."
        )
    if "reset failed" in line:
        return (
            f"The rebuild stopped part of the way through: {reason}. The bots module finishes it "
            "at the next start of the server."
        )
    return f"The server did not rebuild the random bots: {reason}. {NOTHING_DELETED}"


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
    SOAP-then-console list), `restart` (stop, then start), `world_log` (this
    run's world log), `clock` (the token's stamp).
    """

    entry: CatalogEntry
    server_dir: Path
    world_running: Callable[[], bool | None]
    channels: Callable[[], Sequence[Channel]]
    restart: Callable[[], object]
    world_log: Callable[[], str]
    module_moved: botpool.ModuleMoved = field(default_factory=botpool.ModuleMoved)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    pause: Callable[[float, threading.Event | None], None] = _wait
    monotonic: Callable[[], float] = time.monotonic
    timeout_s: float = WATCH_TIMEOUT_S
    poll_s: float = POLL_S

    def take_module_moved(self) -> bool:
        """Did the last update press move TortoiseBots? Answered once (`botpool.ModuleMoved`)."""
        return self.module_moved.take()

    def rebuild(
        self,
        *,
        backup: Callable[[], object] | None = None,
        cancel: threading.Event | None = None,
    ) -> Iterator[str]:
        """Back up (if given) -> enrol -> write the key -> restart once -> watch the log.

        Raises:
            PoolResetError: a backup that failed (nothing else was done), a key
                that could not be written, or a refusal the module logged.
        """
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
            return
        token = new_token(self.clock())
        written = write_key(self.entry, self.server_dir, token)
        yield (
            f"Wrote {KEY} = once:{token} into {written.file.name}; the file as it was is beside "
            f"it at {written.backup.name}."
        )
        if _cancelled(cancel):
            yield (
                "Stopped before the restart. The random bots are rebuilt at the next start of "
                "the server."
            )
            return
        yield "Restarting the server (anyone playing is disconnected)…"
        try:
            self.restart()
        except Exception as exc:  # noqa: BLE001 - the request is written; say what is left
            raise PoolResetError(
                f"The restart failed ({exc}). The request is written, so the random bots are "
                "rebuilt the next time the server starts."
            ) from exc
        yield "Restarted. Watching the server's log for the bots module's rebuild…"
        yield from self._watch(token, cancel)

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
            watch = read_log(self.world_log(), token)
            for line in watch.seen:
                if line not in said:
                    said.add(line)
                    yield line
            if watch.final == "applied":
                yield watch.said
                return
            if watch.final is not None:
                raise PoolResetError(watch.said)
            if _cancelled(cancel):
                yield (
                    "Stopped watching. The rebuild carries on inside the server; the "
                    f"{TORTOISE_CONSOLE_TAB} shows its lines."
                )
                return
            if self.monotonic() >= deadline:
                yield (
                    "The bots module's rebuild lines were not seen yet. It may still be starting "
                    f"up: the {TORTOISE_CONSOLE_TAB} shows the server's log as it goes."
                )
                return
            self.pause(self.poll_s, cancel)


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def for_entry(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    world_running: Callable[[], bool | None],
    channels: Callable[[], Sequence[Channel]],
    restart: Callable[[], object],
    world_log: Callable[[], str],
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
