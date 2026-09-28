"""The Modules tab's "Server build ▾" menu, spelled once, below every layer that names it (T155).

The four labels are said by the view that draws them, by the chip on a module
row (`ui/widgets/modules_panel.py`, which imports nothing from the view), and by
the engine's own refusals and news lines (`catalog/native.py`,
`catalog/upstream.py`). Until T155 each of those typed the label itself, and the
rebuild-owed chip went on saying "Press Rebuild server… on this tab" after the
press had become "Rebuild the server…" and moved into this menu (T89). This
module imports nothing, so every one of them can read the label from here, and
`tests/test_server_build_presses.py` fails any shown string that spells one by
hand.
"""

from __future__ import annotations

SERVER_BUILD = "Server build ▾"
REBUILD = "Rebuild the server…"
UPDATE_TO_LATEST = "Update the server to latest…"
RETURN_TO_PIN = "Return to the tested pin…"


def under_server_build(entry: str) -> str:
    """Where a menu entry is, for a sentence: `“<entry>” under “Server build ▾” on the Modules tab`.

    The tab is named because some of these sentences send the player to
    another tab first, and the menu because the entry is on no toolbar.
    """
    return f"“{entry}” under “{SERVER_BUILD}” on the Modules tab"
