"""The sub-tab's title and its "Server version" section, spelled once, below every layer.

The labels are said by the view that draws them, by the chip on a module
row (`ui/widgets/modules_panel.py`, which imports nothing from the view), and by
the engine's own refusals and news lines (`catalog/native.py`,
`catalog/upstream.py`). Until T155 each of those typed the label itself, and the
rebuild-owed chip went on saying "Press Rebuild server… on this tab" after the
press had become "Rebuild the server…" and moved into this menu (T89). This
module imports nothing, so every one of them can read the label from here, and
`tests/test_server_build_presses.py` fails any shown string that spells one by
hand.

T692a moved the presses into the "Server version" section on the tab:
`SERVER_BUILD` is that section, and `under_server_build()` places an entry
under it there. T692b renamed the tab to "Backups & updates": `MAINTENANCE_TAB`
is the title, and every player sentence that sends someone to the tab reads it
from here, so the widget's title and the sentences cannot drift apart.
"""

from __future__ import annotations

MAINTENANCE_TAB = "Backups & updates"
"""The sub-tab's title (T692b): what the tab bar draws and every sentence says.

The code, the icon and the widget keep the internal name "maintenance"; only
the words a player reads changed.
"""

SERVER_BUILD = "Server version"
REBUILD = "Rebuild the server…"
UPDATE_TO_LATEST = "Update the server to latest…"
RETURN_TO_PIN = "Return to the tested pin…"
PUT_THE_FOLDERS_BACK = "Put the folders back…"


SERVER_BUILD_PLACE = f"“{SERVER_BUILD}” on the {MAINTENANCE_TAB} tab"
"""Where the section is, for a sentence that names it without one entry (T692a)."""


def under_server_build(entry: str) -> str:
    """Where a section entry is, for a sentence: `“<entry>” under “Server version” on the tab`.

    The tab is named because some of these sentences send the player to
    another tab first, and the section because the entry is on no toolbar.
    """
    return f"“{entry}” under {SERVER_BUILD_PLACE}"
