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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from yulon import play
from yulon.catalog.catalog import CatalogEntry

CONFIRMED_LIVE: Final[frozenset[str]] = frozenset(
    {"set_level", "teleport", "mail_gold", "send_gear", "rename"}
)
"""The `play.VERBS` T208's live check watched work on a Centurion server (2026-10-04)."""

NOT_CONFIRMED = "each is offered once it has been checked against a live {game} server"
"""Why a verb nobody has tried is not drawn: the view says it after the verbs it names."""

REVIVE_CRASHED = (
    "it crashed the world server when it was tried, so it stays off until that is fixed"
)
"""Why Revive is not drawn: T208 pressed it and the world server crashed (T218)."""


def withheld(entry: CatalogEntry) -> Mapping[str, str]:
    """Verb -> why it is not offered, for every verb `CONFIRMED_LIVE` does not name."""
    untried = NOT_CONFIRMED.format(game=entry.name)
    return {
        verb: REVIVE_CRASHED if verb == "revive" else untried
        for verb in play.VERBS
        if verb not in CONFIRMED_LIVE
    }
