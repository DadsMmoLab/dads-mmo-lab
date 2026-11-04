"""CoA controller — the AzerothCore package's facts for `wow-coa` (T855-B).

The one server whose Modules tab reads a BORROWED tree plus its own:
`manifests/wow-wotlk/` (`manifests_from: wow-wotlk`, as Unbound) layered with
`manifests/wow-coa/`, which holds its two own `mod`s: the ConsolePort controller
add-on (opt-in, eight `client` steps) and SquidBots Lite, the CoA Bots add-on
(one `client` step, one of this game's default add-ons, T870). Both write into
the player's own client and nothing into the server. The engine that
installs them is game-agnostic (`yulon.apply` over this game's manifest store),
so this package carries the same three bindings every manifest tree carries:
the bundled root, the store over this tree, and the GitHub prefix its indexes
refresh from.
"""

from __future__ import annotations

GAME_ID = "wow-coa"
"""The catalog id every manifest in this package binds to."""
