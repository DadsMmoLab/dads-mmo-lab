"""Which Characters-tab verbs a Centurion server is offered, and why the rest are not.

Every verb the tab has is a TrinityCore console command that exists in Centurion's
source at faac5fc9, each registered `Console::Yes`: `tele name`
(`cs_tele.cpp:56`), `character level` and `character rename`
(`cs_character.cpp:72-73`), `revive` (`cs_misc.cpp:122`), `send money` and
`send items` (`cs_send.cpp:45-48`). Existing in the source is not the same as
having been watched to work on this fork with its own bot module and its own
character scripts, and the rule on every other tree is that a button is drawn
only for a command somebody has run against a live server of that game (8.4a).

So the verbs are behind a flag, `CONFIRMED_LIVE`, and a verb it does not name is
not drawn; the tab says why in one line.

T208's live check, on 2026-10-04 on a Windows 11 test machine, ran a test build
with all six verbs drawn and pressed each one in Yu'lon on a throwaway character,
then read the result back from the database and the game client. Set level,
Teleport, Send gold, Send everything worn and Rename at next login each did what
they said on an offline character. Revive, pressed on an online ghost, crashed
the world server about three seconds later and did not revive it (T218). T218
then traced the crash to the fork's console `revive` itself: it dereferences a
null session at `cs_misc.cpp:795`, before its `if (target)`, so every revive
sent through the console or SOAP crashes the server, offline characters
included. Offline was not a safe way round it. So `CONFIRMED_LIVE` was set to
those five, and Revive was withheld with its own sentence, `REVIVE_CRASHED`,
rather than the one for a verb nobody had tried.

T218 was fixed in the fork (thomasjteachey/TrinityCore112 #1767, in CENTURION 4948d1a9): the
handler treats a command without a session as full permission. So Revive is offered again, but
only on a server whose build carries that fix (`revive_is_fixed()`): the catalog's pin must be
one that does (`REVIVE_FIXED_PINS`), and the build the server runs must have been compiled from
that pin, or from where Update to latest or Return to the tested pin moved it while the catalog
had it (T589: the record of the build, not the checkout, which an update moves hours before its
compile ends). A server made before the pin moved is still the crashing build, and pressing
Revive there would take its world down, so it keeps the sentence that says to update the
server first.

**Two ids, two trees (T850).** This module serves every entry whose controller is
`controller_view`'s `_for_centurion` -- Centurion and, since T850, Mists of Pandaria
(`wow-mop`, 1LineAtaTime/LoA-Pandaria-Playerbots @ e135742e). The verbs are NOT shared:
`CONFIRMED_LIVE_BY_GAME` names, per id, the ones watched on a live server of that game, and
MoP's row is empty because nothing has been tried on a live LoA Pandaria server (GAP §2,
"Characters tab"). So MoP draws no verb, each with the "checked against a live server"
sentence -- including Revive, whose crash sentences are Centurion's alone
(`REVIVE_CRASH_IS_CENTURIONS`). Fill MoP's row in after its first live check.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Final

from yulon import play, server_build_presses
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.native import built_carries_pin, read_built_from, read_state
from yulon.log import get_logger

logger = get_logger(__name__)

CONFIRMED_LIVE: Final[frozenset[str]] = frozenset(
    {"set_level", "teleport", "mail_gold", "send_gear", "rename"}
)
"""The `play.VERBS` T208's live check watched work on a Centurion server (2026-10-04)."""

NOT_CONFIRMED = "each is offered once it has been checked against a live {game} server"
"""Why a verb nobody has tried is not drawn: the view says it after the verbs it names."""

REVIVE_FIXED_PINS: Final[frozenset[str]] = frozenset(
    {
        "4948d1a9290cb1046eede1dbf289789200ea050e",
        "25d3e6efbee97c3cb38830003e2460d98e235f46",
        "351950fb9a5579edfb96a301eb846f13e65aeda6",
    }
)
"""Centurion pins whose build answers a console or SOAP `revive` (T218).

CENTURION 4948d1a9 and 25d3e6ef hold #1767 (`cs_misc.cpp`: `!session || session->HasPermission`);
T824 read the same line at 351950fb (`cs_misc.cpp:798`, `HandleReviveCommand`). A later pin is
added here by whoever moves it, after reading that the fix is still in it:
`test_the_shipped_pin_carries_the_revive_fix` fails until they do, and until then Revive is
withheld again rather than offered on a build nobody has looked at."""

REVIVE_CRASH_IS_CENTURIONS: Final[frozenset[str]] = frozenset({"wow-centurion"})
"""The ids the crash above was measured on, and the only ones owed its two sentences.

`cs_misc.cpp:795`'s null-session dereference is this fork's at CENTURION faac5fc9, and #1767
fixed it there. Another id this package serves (T850's `wow-mop`) has never been tried at all,
so its Revive gets the plain "not checked yet" sentence: claiming a crash nobody saw, on
another fork's console, is exactly the inherited fact this module exists to refuse."""

CONFIRMED_LIVE_BY_GAME: Final[Mapping[str, frozenset[str]]] = {
    # T850: no Characters verb has been run against a live LoA Pandaria server yet
    # (GAP §2, "Characters tab"), so MoP draws none of them and says why.
    "wow-mop": frozenset(),
}
"""Per id, the OVERRIDE of `CONFIRMED_LIVE` -- an id with no row uses it, Centurion's.

Per tree and never inherited: Centurion's five are T208's, on Centurion's own box and its own
bot module, and are not evidence about another core. A table of overrides rather than a table
of every id, so `CONFIRMED_LIVE` is still read at CALL time: `test_centurion_view` swaps it
out to prove the tab draws exactly the verbs it names."""

REVIVE_CRASHED = (
    "it crashed the world server when it was tried, so it stays off until that is fixed"
)
"""Why Revive is not drawn when the catalog's pin does not carry the fix (T218)."""

REVIVE_NEEDS_UPDATE = (
    "it crashed the world server on builds made before its fix, and this server has no finished "
    "build Yu'lon knows to have the fix, so it stays off until one finishes: press "
    f"{server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)} (or let "
    "the install finish), then open Yu'lon again"
)
"""Why Revive is not drawn on a server whose build may be the one that crashes (T218, T589).

Said of a server built before the pin moved, of one whose last press did not finish, and of a
fresh install whose build has not finished, so it claims nothing about when the build was made."""


def _core_source(entry: CatalogEntry) -> tuple[str, str, str] | None:
    """`(repo, dest, rev)` of Centurion's one source, the core, or None for a shape it is not."""
    sources = entry.emulator.sources
    if len(sources) != 1:
        return None
    (source,) = sources
    rev = source.rev
    return (source.repo, source.dest, rev) if rev else None


def _checkout_head(checkout: Path) -> str | None:
    """The commit the checkout is on, read from `.git` without running git; None if unreadable.

    A detached HEAD holds the sha. A branch is followed through `refs/` and then
    `packed-refs`. No container or process is started: this runs when the tab is built.
    """
    git_dir = checkout / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head or None
        ref = head[4:].strip()
        try:
            loose = (git_dir / ref).read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            loose = ""
        if loose:
            return loose
        for line in (git_dir / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, name = line.partition(" ")
            if name == ref and not line.startswith(("#", "^")):
                return sha
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(f"could not read what {checkout} is on: {exc}")
    return None


def revive_is_fixed(entry: CatalogEntry, server_dir: Path | None) -> bool:
    """Does the build this server runs answer a console `revive`? (T218, T589)

    Yes only when the catalog's pin carries the fix AND the install record says the build
    finished with no failed or stopped press since (`last_error`) AND the record of the running
    build (`native.BUILT_FROM_FILE`) names the commit the core was compiled from, the checkout is
    on that commit, and it is the pin or where an Update to latest / Return to the tested pin
    made while the catalog had this pin moved it (`native.built_carries_pin()`).

    The checkout alone is not enough (T589): an update moves it hours before the compile ends,
    and a Yu'lon killed in between leaves the old, crashing binary beside a checkout on the pin.
    The record is written only once a build is the one the tags name and forgotten when a press
    starts changing them, so that state has no record. Anything that cannot be read answers no:
    the cost is a button that appears after an update, not a world server that goes down.
    """
    core = _core_source(entry)
    if core is None or core[2] not in REVIVE_FIXED_PINS or server_dir is None:
        return False
    repo, dest, pin = core
    head = _checkout_head(server_dir / dest)
    # `valid=()`: not asking about stages, so no "stages this build does not know" warning;
    # every recorded name then reads back in `unknown`.
    state = read_state(server_dir, valid=())
    if head is None or state is None or "build" not in state.unknown or state.last_error:
        return False
    built = read_built_from(server_dir).get(repo, "")
    return head == built and built_carries_pin(built, pin, state.rev_for(repo))


def confirmed_live(entry: CatalogEntry) -> frozenset[str]:
    """The verbs watched to work on a live server of THIS entry (`CONFIRMED_LIVE_BY_GAME`)."""
    return CONFIRMED_LIVE_BY_GAME.get(entry.id, CONFIRMED_LIVE)


def withheld(entry: CatalogEntry, server_dir: Path | None = None) -> Mapping[str, str]:
    """Verb -> why it is not offered, for every verb this entry has no live check for.

    Revive is also named when this server's build (`server_dir`) may still be the one that
    crashes on it (`revive_is_fixed()`); a call without a folder withholds it. That arm is
    Centurion's fork and its own #1767 (`REVIVE_CRASH_IS_CENTURIONS`), so another id this
    package serves keeps the plain "not checked yet" sentence for Revive instead of a claim
    about a crash nobody watched there (T850, MoP).
    """
    untried = NOT_CONFIRMED.format(game=entry.name)
    live = confirmed_live(entry)
    reasons = {verb: untried for verb in play.VERBS if verb not in live}
    if "revive" in reasons and entry.id in REVIVE_CRASH_IS_CENTURIONS:
        core = _core_source(entry)
        pin_fixed = core is not None and core[2] in REVIVE_FIXED_PINS
        if pin_fixed and revive_is_fixed(entry, server_dir):
            del reasons["revive"]
        else:
            reasons["revive"] = REVIVE_NEEDS_UPDATE if pin_fixed else REVIVE_CRASHED
    return {verb: reasons[verb] for verb in play.VERBS if verb in reasons}
