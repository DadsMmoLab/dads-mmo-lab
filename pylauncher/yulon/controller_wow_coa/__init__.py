"""CoA controller — the AzerothCore package's facts for `wow-coa` (T855-B).

The one server whose Modules tab reads a BORROWED tree plus its own:
`manifests/wow-wotlk/` (`manifests_from: wow-wotlk`, as Unbound) layered with
`manifests/wow-coa/`, which holds only the ConsolePort controller add-on (a
`mod` of eight `client` steps into the player's own client). The engine that
installs it is game-agnostic (`yulon.apply` over this game's manifest store),
so this package carries the same three bindings every manifest tree carries:
the bundled root, the store over this tree, and the GitHub prefix its indexes
refresh from.
"""

from __future__ import annotations

GAME_ID = "wow-coa"
"""The catalog id every manifest in this package binds to."""
