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

import re
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from yulon import git
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.native import LatestRoute
from yulon.channel import Answer, Channel
from yulon.controller import Controller
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
        f"back, type `{PREVIEW}` at the server console (the Console tab), then type the "
        "`bot pool adopt confirm …` line it prints within five minutes, then restart the server."
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


def _cancelled(cancel: threading.Event | None) -> bool:
    return cancel is not None and cancel.is_set()


def _first_line(answer: Answer) -> str:
    return next((line.strip() for line in answer.text.splitlines() if line.strip()), "")


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


def restart_world(controller: Controller) -> None:
    """Stop, then start: the Server tab's own restart (`_do_restart`), for the module to reload.

    The controller already knows which daemon it means, so nothing here names one.
    """
    controller.stop()
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
) -> Iterator[str]:
    """Run the update, then enrol the older bots if the module moved, then restart.

    A failed update raises out of here before anything is asked: its own
    handler has put the sources back, so the module did not move.

    A head nobody could read counts as moved. The first thing sent is the
    preview, which is a read, and it answers "nothing to adopt" on a server
    that has nothing -- so the cost of not knowing is one question.
    """
    before = head(module_dir)
    yield from update(cancel)
    after = head(module_dir)
    if before is not None and before == after:
        return
    yield (
        "The bots module changed, and it now only runs bot accounts it has enrolled. "
        "Enrolling the ones this server already had…"
    )
    reasons: list[str] = []
    outcome: Outcome = Unreached("no command channel is set up for this server")
    for channel in channels():
        outcome = adopt(channel, pause=pause, cancel=cancel)
        if not isinstance(outcome, Unreached):
            # Adopted, or a confirm that went out unanswered: either way no
            # other channel is asked anything, and never a second confirm.
            break
        reasons.append(outcome.why)
        if outcome.why == CANCELLED:
            break
    if isinstance(outcome, Unreached):
        yield console_steps("; ".join(reasons) or outcome.why)
        return
    if isinstance(outcome, Adopted) and outcome.count == 0:
        yield "Every bot account was already enrolled; nothing to do."
        return
    if isinstance(outcome, Unconfirmed):
        yield (
            f"The enrol command was sent but its answer never came ({outcome.why}), so it may "
            "well have run. Restarting the server so any bots it enrolled log in again. If the "
            f"older bots are still missing afterwards, type `{PREVIEW}` at the server console "
            "(the Console tab), then the `bot pool adopt confirm …` line it prints, then restart."
        )
    else:
        yield (
            f"Enrolled {outcome.count} bot account(s). Restarting the server so their "
            "characters log in again…"
        )
    if _cancelled(cancel):
        yield (
            "The restart was not started because the job was cancelled. Restart the server "
            f"from the Server tab to bring the older bots online. {AFTER_A_STOP}"
        )
        return
    try:
        restart()
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
) -> LatestRoute | None:
    """The update route with `after_update()` around both presses; unchanged when it cannot be.

    `replace()`, so every other field -- `upstream_news` (T124) among them -- is
    the route's own. `wsl_distro` reaches the module's head read (T125).

    Both directions: "Return to the tested pin" moves the module as well.
    `head_sha` is looked up per press, so a test can replace it on this module.
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
            )

        return run

    return replace(route, press=wrapped(route.press), to_pin=wrapped(route.to_pin))
