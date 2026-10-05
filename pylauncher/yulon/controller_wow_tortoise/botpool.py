"""After the bots module moves, enrol the bot accounts an older module made (T123).

**What changed upstream.** TortoiseBots (2026-09-24, issue #265) runs only the
bot accounts listed in its own registry table, `tortoise_bots_pool_account`,
where it used to take every account whose name starts `RNDBOT`. The migration
that adds the table creates it empty and nothing back-fills it. So an install
made on the old pin and moved onto the new module by "Update the server to
latest…" keeps its bots on accounts the module now ignores.

**What that looks like, measured** on yulon-ubuntu on 2026-09-25 with the
registry emptied under a finished install (`.notes/gates/t123-tortoisebots-adopt/`):
the world log says "50 account(s) match the random-bot prefix but are NOT
managed pool accounts", loads 0 candidates, and auto-create starts building a
second pool of 500 beside the old one. None of the old bots came online.

**The module's own remedy** is two commands, `bot pool adopt preview` and then
`bot pool adopt confirm <challenge>` with the code the preview printed. Both
need a session-less handler at console level. This core queues SOAP commands at
console level (`src/mangosd/MaNGOSsoap.cpp:196`), so the install's SOAP channel
reaches them; the attach console reaches them by definition and is the second
choice. Two facts about them decide the shape of `adopt()`:

* the confirm is refused if the set of `RNDBOT` accounts changed since the
  preview, and auto-create adds one every ~15 s while the pool is short. The two
  are sent back to back (1.06 s apart worked on the box) and the pair is asked
  again on that refusal, which wrote nothing;
* adopting registers the accounts but brings nothing online. The module loads
  its candidates at world start only, so an adoption that enrolled anything is
  followed by a restart. Measured after one: 700 candidates, 500 online.

Only the update route moves this checkout on an existing install -- Rebuild
and Repair compile the `src/` that is there -- so `after_update()` wraps that
route's two presses and runs only when the module's commit actually changed.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from yulon import docker, git
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.native import LatestRoute
from yulon.channel import Answer, Channel
from yulon.controller import Controller, StartRefused
from yulon.log import get_logger

logger = get_logger(__name__)

PREVIEW = "bot pool adopt preview"
"""Read-only: lists the prefix accounts and prints a five-minute challenge."""

CONFIRM = "bot pool adopt confirm {challenge}"
"""Registers every account the preview listed, or refuses if the set moved."""

TRIES = 3
"""How many preview-and-confirm rounds before giving up. Bounded, never a loop."""

PAUSE_S = 30.0
"""Seconds between previews that got no answer.

The world thread is busy for 20-40 s after "World server is up" when it logs
500 bots in (`tortoise-soap-first-run-facts`), and a SOAP request in that window
times out. A preview is a read, so asking it again cannot do anything twice.
"""

_PENDING = re.compile(r"(\d+) account\(s\) still need adoption")
_CHALLENGE = re.compile(r"bot pool adopt confirm ([0-9A-Za-z]+)")
_NOTHING = ("Nothing to adopt.", "Every matching account is already managed.")
_ADOPTED = re.compile(r"Adoption complete: (\d+) account\(s\) registered")
_SET_CHANGED = "the matching account set changed since the preview"

Press = Callable[[threading.Event | None], Iterator[str]]
"""`LatestRoute.press`'s shape: cancel in, lines out."""


AFTER_A_STOP = (
    "The enrolment is saved in the database as soon as it is made, so the next start of "
    "the server loads those bots, even if this job stops before its restart."
)


def console_steps(why: str) -> str:
    """The one message a player reads when this app could not run the adoption itself."""
    return (
        "Your older bots stay offline for now: the bots module only runs bot accounts it has "
        f"enrolled, and Yu'lon could not enrol this server's older ones ({why}). To bring them "
        "back, type this at the server console (the Console tab):\n"
        f"{PREVIEW}\n"
        "Then type the bot pool adopt confirm line it prints within five minutes, and restart "
        "the server."
    )


@dataclass(frozen=True)
class Preview:
    """What a preview said: how many accounts still need adopting, and the code to confirm."""

    pending: int
    challenge: str


@dataclass(frozen=True)
class Adopted:
    """The module registered this many accounts. Zero means there was nothing to adopt."""

    count: int


@dataclass(frozen=True)
class Unreached:
    """No adoption happened through this channel, and why, in words a player can read."""

    why: str


@dataclass(frozen=True)
class Unconfirmed:
    """A confirm went out and no answer came back, so the accounts MAY be enrolled.

    Its own outcome and not an `Unreached`, because what follows differs
    (cold review, round 1). SOAP queues the command on the world thread, so a
    confirm whose answer timed out has usually still run -- and a second
    channel's preview then says "already managed", which read as "nothing to
    do" and skipped the restart. After one of these no channel is asked
    anything again and the world is restarted anyway.
    """

    why: str


Outcome = Adopted | Unreached | Unconfirmed


def read_preview(text: str) -> Preview | None:
    """The preview's two facts, or None when `text` is not a preview answer at all.

    None is not zero: "This command is only available at the server console" is
    an answer, and reading it as "nothing to adopt" would skip the one channel
    that can run it.
    """
    nothing = any(line in text for line in _NOTHING)
    pending = _PENDING.search(text)
    challenge = _CHALLENGE.search(text)
    wants = challenge is not None or (pending is not None and int(pending.group(1)) > 0)
    if nothing and wants:
        # Both kinds of line in one capture: the attach console hands back
        # everything printed between two prompts, and this is not one answer.
        # Zero would skip the adoption, so it is unreadable (Codex, round 1).
        return None
    if nothing:
        return Preview(pending=0, challenge="")
    if pending is None or challenge is None:
        return None
    return Preview(pending=int(pending.group(1)), challenge=challenge.group(1))


def adopt(
    channel: Channel,
    *,
    pause: Callable[[float], None] = time.sleep,
    cancel: threading.Event | None = None,
) -> Outcome:
    """Preview, then confirm at once; ask again only where asking again cannot write twice.

    `cancel` is read before each command, so a Cancel pressed while this runs
    stops it before the next one goes out.
    """
    previews_unanswered = 0
    rounds = 0
    while rounds < TRIES:
        if _cancelled(cancel):
            return Unreached(CANCELLED)
        answer = channel.send(PREVIEW)
        if not answer.known:
            previews_unanswered += 1
            if answer.denied or previews_unanswered >= TRIES:
                return Unreached(answer.reason or "the server did not answer")
            pause(PAUSE_S)
            continue
        preview = read_preview(answer.text)
        if preview is None:
            return Unreached(_first_line(answer) or "the server answered something else")
        if preview.pending == 0:
            return Adopted(0)
        if _cancelled(cancel):
            return Unreached(CANCELLED)
        rounds += 1
        confirm = channel.send(CONFIRM.format(challenge=preview.challenge))
        if not confirm.known:
            # Never sent again, on this channel or any other: it may have run.
            return Unconfirmed(confirm.reason or "the server did not answer the confirm")
        adopted = _ADOPTED.search(confirm.text)
        if adopted is not None:
            return Adopted(int(adopted.group(1)))
        if _SET_CHANGED not in confirm.text:
            return Unreached(_first_line(confirm) or "the server refused the confirm")
        logger.info("adoption refused: the bot account set moved since the preview; asking again")
    return Unreached("new bot accounts kept appearing between the preview and the confirm")


CANCELLED = "the job was cancelled"

RESTART_OWED = (
    "The server restarts next, once you have answered whether to rebuild the random bots, so "
    "the enrolled bots log in with one restart. If this window closes before that, restart "
    "the server from the Server tab: the enrolment is saved, and any later start loads them."
)
"""What the update's log says when the enrolment's restart is owed to the view (T144)."""


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _first_line(answer: Answer) -> str:
    return next((line.strip() for line in answer.text.splitlines() if line.strip()), "")


def adopt_over(
    channels: Sequence[Channel],
    *,
    pause: Callable[[float], None] = time.sleep,
    cancel: threading.Event | None = None,
) -> Outcome:
    """`adopt()` over each channel in turn until one reaches the command; the reasons joined.

    Adopted, or a confirm that went out unanswered: either way no other channel
    is asked anything, and never a second confirm. Shared by `after_update()`
    and T144's rebuild, so the two cannot enrol by different rules.
    """
    reasons: list[str] = []
    outcome: Outcome = Unreached("no command channel is set up for this server")
    for channel in channels:
        outcome = adopt(channel, pause=pause, cancel=cancel)
        if not isinstance(outcome, Unreached):
            return outcome
        reasons.append(outcome.why)
        if outcome.why == CANCELLED:
            break
    return Unreached("; ".join(reasons)) if reasons else outcome


@dataclass(frozen=True)
class Move:
    """An update press moved the bots module (T144). `restart_owed`: see `ModuleMoved`."""

    restart_owed: bool


class ModuleMoved:
    """Carries "the last update press moved the bots module" from the job to the view (T144).

    Set by `after_update()` on the worker thread, taken by the view once the
    job has finished. `take()` answers and clears in one call, so one move is
    offered once, and a later press that fails or does not move the module
    cannot find a stale one (`after_update()` also clears it as it starts).

    **And the restart the enrolment needs, owed rather than made.** With this
    flag wired, `after_update()` enrols but does not restart: the view asks
    whether to rebuild the random bots first, and a Yes writes the rebuild
    request so that ONE restart does both (approved design point 3). A No, or a
    rebuild that stops before its own restart, runs the owed restart instead.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._move: Move | None = None

    def set(self) -> None:
        with self._lock:
            self._move = Move(restart_owed=False)

    def owe_restart(self) -> None:
        with self._lock:
            self._move = Move(restart_owed=True)

    def take(self) -> Move | None:
        with self._lock:
            move, self._move = self._move, None
        return move


def module_dir(entry: CatalogEntry, server_dir: Path) -> Path | None:
    """Where the bots module's checkout lives in this install, read off the catalog.

    The source compiled INTO the core: the one whose folder sits inside the
    first source's. None when the entry has none, and then nothing is wrapped.
    """
    sources = entry.emulator.sources
    if not sources:
        return None
    core = Path(sources[0].dest)
    inside = [s for s in sources[1:] if Path(s.dest).is_relative_to(core)]
    return server_dir / inside[-1].dest if inside else None


RESTART_REFUSED = "The restart was refused, so the server was not stopped:"
"""How a bots press says its restart was refused (T197): the refusal follows, naming its repair."""


class StopFailed(Exception):
    """`restart_world()`'s STOP raised: the world that may still be up is the OLD run (T144).

    Its own type, so a caller can tell it from a start that raised -- after
    which the world may be the NEW run, already reading its config. The
    message is the stop's own.
    """


def restart_world(controller: Controller) -> None:
    """Stop, then start: the Server tab's own restart (`_do_restart`), for the module to reload.

    The controller already knows which daemon it means, so nothing here names one.
    Asked first whether a start may run at all (`Controller.refuse_start()`, T197), as
    `_do_restart` does, so a refused start leaves the running world running.

    Raises:
        StartRefused: no start may run here; nothing was stopped.
        StopFailed: the stop raised; nothing was started.
    """
    controller.refuse_start()
    # One lifecycle command from the stop to the start (T216 review round 3), so a
    # restore cannot take its hold in between and leave the world stopped. A
    # server already held refuses before the stop: nothing was stopped or
    # started, which is what `StopFailed` tells the caller.
    with contextlib.ExitStack() as composite:
        try:
            composite.enter_context(docker.lifecycle(controller.server_dir))
            controller.stop()
        except Exception as exc:  # noqa: BLE001 - re-raised, typed: see `StopFailed`
            raise StopFailed(str(exc)) from exc
        controller.start()


def head_sha(dest: Path, *, wsl_distro: str | None = None) -> str | None:
    """The commit the checkout is on, through the transport the update route itself uses.

    Inside a WSL distro that is the distro's Docker (T125), as it is for the route.
    """
    return git.ContainerGit(wsl_distro=wsl_distro).head_sha(dest)


def after_update(
    update: Press,
    cancel: threading.Event | None,
    *,
    module_dir: Path,
    head: Callable[[Path], str | None],
    channels: Callable[[], Sequence[Channel]],
    restart: Callable[[], object],
    pause: Callable[[float], None] = time.sleep,
    moved: ModuleMoved | None = None,
) -> Iterator[str]:
    """Run the update, then enrol the older bots if the module moved, then restart.

    `moved` is set once the update has succeeded AND moved the module, before
    anything is asked of the server (T144): the view reads it after the job has
    finished, to offer rebuilding the random bots. A modal cannot be put up from
    inside this generator, which runs on the log panel's worker thread. With it
    wired, an enrolment's restart is OWED to the view (`ModuleMoved`) and not
    made here, so the update path restarts the world once.

    A failed update raises out of here before anything is asked: its own
    handler has put the sources back, so the module did not move.

    A head nobody could read counts as moved. The first thing sent is the
    preview, which is a read, and it answers "nothing to adopt" on a server
    that has nothing -- so the cost of not knowing is one question.
    """
    if moved is not None:
        moved.take()
    before = head(module_dir)
    yield from update(cancel)
    after = head(module_dir)
    if before is not None and before == after:
        return
    if moved is not None:
        moved.set()
    yield (
        "The bots module changed, and it now only runs bot accounts it has enrolled. "
        "Enrolling the ones this server already had…"
    )
    outcome = adopt_over(channels(), pause=pause, cancel=cancel)
    if isinstance(outcome, Unreached):
        yield console_steps(outcome.why)
        return
    if isinstance(outcome, Adopted) and outcome.count == 0:
        yield "Every bot account was already enrolled; nothing to do."
        return
    restarting = "Restarting the server" if moved is None else "The server will restart"
    if isinstance(outcome, Unconfirmed):
        yield (
            f"The enrol command was sent but its answer never came ({outcome.why}), so it may "
            f"well have run. {restarting} so any bots it enrolled log in again. If the "
            "older bots are still missing afterwards, type this at the server console (the "
            f"Console tab):\n{PREVIEW}\n"
            "then the bot pool adopt confirm line it prints, then restart."
        )
    else:
        yield (
            f"Enrolled {outcome.count} bot account(s). {restarting} so their "
            "characters log in again…"
        )
    if _cancelled(cancel):
        yield (
            "The restart was not started because the job was cancelled. Restart the server "
            f"from the Server tab to bring the older bots online. {AFTER_A_STOP}"
        )
        return
    if moved is not None:
        moved.owe_restart()
        yield RESTART_OWED
        return
    try:
        restart()
    except StartRefused as exc:
        # T197: Restart is refused too; the refusal names its own repair.
        yield (
            f"{RESTART_REFUSED} {exc} The older bots log in again at the next start. "
            f"{AFTER_A_STOP}"
        )
        return
    except Exception as exc:  # noqa: BLE001 - the update succeeded; this is one press left
        logger.warning(f"the restart after the adoption failed: {exc}")
        yield (
            f"The restart failed ({exc}). Restart the server from the Server tab and the older "
            f"bots log in again. {AFTER_A_STOP}"
        )
        return
    yield "Restarted. The bots come online over the next few minutes."


def wrap_route(
    route: LatestRoute | None,
    entry: CatalogEntry,
    server_dir: Path,
    *,
    channels: Callable[[], Sequence[Channel]],
    restart: Callable[[], object],
    wsl_distro: str | None = None,
    moved: ModuleMoved | None = None,
) -> LatestRoute | None:
    """The update route with `after_update()` around both presses; unchanged when it cannot be.

    `replace()`, so every other field -- `upstream_news` (T124) among them -- is
    the route's own. `wsl_distro` reaches the module's head read (T125).

    Both directions: "Return to the tested pin" moves the module as well.
    `head_sha` is looked up per press, so a test can replace it on this module.
    `moved` is handed to both presses (T144).
    """
    dest = module_dir(entry, server_dir)
    if route is None or dest is None:
        return route

    def wrapped(press: Press) -> Press:
        def run(cancel: threading.Event | None) -> Iterator[str]:
            return after_update(
                press,
                cancel,
                module_dir=dest,
                head=lambda d: head_sha(d, wsl_distro=wsl_distro),
                channels=channels,
                restart=restart,
                moved=moved,
            )

        return run

    return replace(route, press=wrapped(route.press), to_pin=wrapped(route.to_pin))
