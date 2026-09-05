"""Read-only probe of the half-written TBC databases left by the 7.4c interruption.

Builds the SAME `sqlplan.MarkerGate` the installer's `stage_import()` will build
on the re-press -- `controller_wow_tbc.repair.import_gate()` quotes the same plan,
the same container, and reads this install's own generated password -- and asks it
what state the databases are in. Nothing here writes or drops.

`repair.import_state()` is deliberately NOT used: it translates `partial` to
`unreadable` for the controller's own reasons, and `partial` is the answer this
gate line is about.
"""

import sys
from pathlib import Path

sys.path.insert(0, "/home/pk/yulon-74c/pylauncher")

from yulon.controller_wow_tbc import repair
from yulon.log import use_utf8_streams

use_utf8_streams()

server_dir = Path("/home/pk/tbc-7.4c")
gate = repair.import_gate(server_dir)
state = gate.probe()
print(f"probe state : {state.state}")
print(f"complete    : {state.complete}")
print(f"detail      : {state.detail}")

# What the controller would say about the same databases, for the record.
controller = repair.import_state(server_dir)
print(f"controller  : {controller.state} -- {controller.detail}")
