"""T30 Half 2 live: forget ~/tortoise-vm and re-attach through the app's own code.

state.forget() + state.remember() are the app's own state management; the
"Use existing..." button's attach_existing() wraps these with a QFileDialog
the user clicks through. The validation (compose file present, dir_problem)
is called here the way the button does it. Named deviation: the GUI dialog
was bypassed; the validation and state write are the app's own code.
"""
from __future__ import annotations
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, "/home/pk/y8-t30/pylauncher")

from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import compose_file
from yulon.log import configure, use_utf8_streams
from yulon.state import KnownInstall, load_state, save_state

use_utf8_streams()
configure()

SERVER = Path("/home/pk/tortoise-vm")
CLIENT = Path("/home/pk/clients/TurtleWoW")
GAME = "wow-tortoise"

now = lambda: datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

print(f"=== Use existing press ===  {now()}")

# Load state
state = load_state()
print(f"state before: {len(state.installs)} install(s)")
for inst in state.installs:
    print(f"  {inst.game} -> {inst.server_dir} client={inst.client_dir}")

# Step 1: forget
found = state.find(GAME, SERVER)
if found:
    print(f"\nforgetting {GAME} at {SERVER} (the app's own state.forget)")
    state.forget(GAME, SERVER)
    path = save_state(state)
    print(f"  saved to {path}, now {len(state.installs)} install(s)")
else:
    print(f"\n{GAME} at {SERVER} not in state (nothing to forget)")

# Step 2: validate (the way attach_existing does it)
cf = compose_file(SERVER)
print(f"\ncompose file check: {cf}")
if cf is None:
    print("ERROR: no compose file found")
    sys.exit(1)

from yulon import platform
problem = platform.server_dir_problem(SERVER)
print(f"server_dir_problem: {problem}")
if problem is not None:
    print(f"ERROR: {problem}")
    sys.exit(1)

# Step 3: re-attach (the way on_installed does it)
print(f"\nremembering {GAME} at {SERVER} client={CLIENT}")
state.remember(KnownInstall(game=GAME, server_dir=SERVER, client_dir=CLIENT))
path = save_state(state)
print(f"  saved to {path}, now {len(state.installs)} install(s)")

# Read back the entry to prove it
entry = load_catalog().get(GAME)
print(f"\ncatalog entry: {entry.name}")
print(f"  channel: {entry.operations.channel}")
for s in entry.emulator.sources:
    print(f"  source: {s.repo} branch={s.branch} rev={s.rev}")

# Final state
state2 = load_state()
print(f"\nstate after re-read: {len(state2.installs)} install(s)")
for inst in state2.installs:
    print(f"  {inst.game} -> {inst.server_dir} client={inst.client_dir}")

print(f"\n=== done ===  {now()}")
