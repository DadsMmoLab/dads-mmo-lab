"""Which Characters-tab verbs a Centurion server is offered, and why the rest are not.

Every verb the tab has is a TrinityCore console command that exists in Centurion's
source at faac5fc9, each registered `Console::Yes`: `tele name`
(`cs_tele.cpp:56`), `character level` and `character rename`
(`cs_character.cpp:72-73`), `revive` (`cs_misc.cpp:122`), `send money` and
`send items` (`cs_send.cpp:45-48`). Existing in the source is not the same as
having been watched to work on this fork with its own bot module and its own
character scripts, and the rule on every other tree is that a button is drawn
only for a command somebody has run against a live server of that game (8.4a).

So the verbs are behind a flag, `CONFIRMED_LIVE`, which T179 Task 9's live proof
fills with the verbs it watched work. Until then none is drawn and each says so;
any the proof finds broken stays out, with the same sentence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from yulon import play
from yulon.catalog.catalog import CatalogEntry

CONFIRMED_LIVE: Final[frozenset[str]] = frozenset()
"""The `play.VERBS` T179 Task 9 watched work on a live Centurion server. Empty until then."""

NOT_CONFIRMED = "each is offered once it has been checked against a live {game} server"
"""Why a verb is not drawn: the view says it after the verbs it names."""


def withheld(entry: CatalogEntry) -> Mapping[str, str]:
    """Verb -> why it is not offered, for every verb `CONFIRMED_LIVE` does not name."""
    reason = NOT_CONFIRMED.format(game=entry.name)
    return {verb: reason for verb in play.VERBS if verb not in CONFIRMED_LIVE}
