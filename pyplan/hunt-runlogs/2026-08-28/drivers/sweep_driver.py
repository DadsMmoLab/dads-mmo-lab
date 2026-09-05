"""Direct-driving feature sweep for the wow-server-playerbots install.

Reuses the exact production wiring (ControllerServices.for_wotlk) that the
GUI's controller_view.py buttons call, so this is not a reimplementation --
it is the same seam, called non-interactively because the native
xdg-desktop-portal folder picker has no AT-SPI content over this SSH-driven
session (documented separately).
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path.home() / "yulon-run" / "pylauncher"))

SERVER_DIR = Path.home() / "wow-server-playerbots"

results = []


def section(name):
    print(f"\n{'=' * 20} {name} {'=' * 20}")


def ok(msg):
    print(f"[OK] {msg}")
    results.append(("OK", msg))


def fail(msg, exc=None):
    print(f"[FAIL] {msg}")
    if exc:
        traceback.print_exc()
    results.append(("FAIL", msg))


section("Loading catalog entry + building ControllerServices.for_wotlk (real GUI wiring)")
from yulon.catalog.catalog import load_catalog
from yulon.ui.controller_view import ControllerServices

catalog = load_catalog()
entry = catalog.get("wow-wotlk")
services = ControllerServices.for_wotlk(entry, SERVER_DIR)
print("entry:", entry.id, entry.name if hasattr(entry, "name") else "")
print("services built OK:", services)

section("Controller status()")
try:
    status = services.controller.status()
    print("status:", status)
    ok(f"status() -> db={status.db} auth={status.auth} world={status.world}")
except Exception as e:
    fail("status()", e)

section("Controller import_state()")
try:
    istate = services.controller.import_state()
    print("import_state:", istate)
    ok(f"import_state() -> {istate}")
except Exception as e:
    fail("import_state()", e)

section("Controller port_conflicts()")
try:
    conflicts = services.controller.port_conflicts()
    print("port_conflicts:", conflicts)
    ok(f"port_conflicts() -> {conflicts}")
except Exception as e:
    fail("port_conflicts()", e)

print("\n\n=== SUMMARY SO FAR ===")
for status_, msg in results:
    print(status_, msg)
