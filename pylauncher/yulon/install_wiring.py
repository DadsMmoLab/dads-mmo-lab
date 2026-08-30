"""App-side install wiring (phase7-decisions, "Wiring" row of the layout table).

D.3 fills this module in: `fixed_db_password()`, `import_gate_for()`,
`installer_for_app()` and the CLI harness `main()`. It exists this early
because `DEFAULT_DB_ROOT_PASSWORD` moves here the moment the catalog's
`db_root_password` field is deleted (the two were read together in `main.py`
and `controller_view.py`), and `controller_wow_wotlk.modules` re-exports it
from here so no importer has to move twice.
"""

from __future__ import annotations

DEFAULT_DB_ROOT_PASSWORD = "password"
"""acore-docker's default root password: what every fixed-password WotLK install shares with
backup, the console and every archived guide, and the fallback when an entry's plan has no
fixed value (moved from `controller_wow_wotlk.modules`, which re-exports it)."""
