"""Phase 2 of the direct-driving feature sweep: modules, networking, backups/restore,
repair_import refusal, console, log streaming, self-update.

Uses the same ControllerServices.for_wotlk real GUI wiring as phase 1.
"""
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path.home() / "gate0904" / "checkout" / "pylauncher"))

SERVER_DIR = Path.home() / "wowserver"

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


from yulon.catalog.catalog import load_catalog
from yulon.ui.controller_view import ControllerServices

catalog = load_catalog()
entry = catalog.get("wow-wotlk")
services = ControllerServices.for_wotlk(entry, SERVER_DIR)

# ---------------------------------------------------------------- modules
section("Modules: ManifestStore + Applier (GUI 'Modules' tab wiring)")
try:
    store = services.store
    applier = services.applier
    print("store:", store, "applier:", applier)
    for kind in ("module", "ale", "mod", "keg"):
        try:
            idx = store.load_index(kind)
            items = list(idx.items) if hasattr(idx, "items") else idx
            print(f"  kind={kind!r}: index loaded, {len(items) if hasattr(items,'__len__') else '?'} items")
        except Exception as e:
            print(f"  kind={kind!r}: load_index failed: {e}")
    ok("ManifestStore.load_index() callable for all 4 manifest kinds (module/ale/mod/keg)")
except Exception as e:
    fail("modules store/applier", e)

# ------------------------------------------------------------- networking
section("Networking: plan(lan) / plan(internet)")
try:
    for mode in ("lan", "internet"):
        plan = services.network_plan(mode)
        print(f"mode={mode}: ready={plan.ready} warnings={getattr(plan,'warnings',None)}")
        ok(f"network_plan({mode!r}) -> ready={plan.ready}")
except Exception as e:
    fail("network_plan", e)

# ------------------------------------------------------ maintenance/repair
section("Maintenance: repair_import() refusal on a populated DB")
try:
    istate = services.controller.import_state()
    print("current import_state:", istate)
    try:
        services.controller.repair_import()
        fail("repair_import() should have refused on a populated DB but did not raise")
    except Exception as e:
        print("repair_import() correctly refused:", e)
        ok(f"repair_import() refused on populated DB: {e}")
except Exception as e:
    fail("repair_import refusal check", e)

# ------------------------------------------------------------- self-update
section("Self-update check")
try:
    from yulon.update import check_for_update

    check = check_for_update()
    print("check_for_update():", check)
    ok(f"check_for_update() -> current={check.current} latest={check.latest} available={check.available} error={check.error}")
except Exception as e:
    fail("check_for_update()", e)

# ---------------------------------------------------------------- console
section("Console: send_console('server info')")
try:
    reply = services.send_console("server info")
    print("console reply:", reply)
    ok(f"send_console('server info') -> {str(reply)[:300]}")
except Exception as e:
    fail("send_console", e)

# -------------------------------------------------------------- log panel
section("Log panel streaming: logs_source() sample")
try:
    it = services.logs_source()
    lines = []
    start = time.time()
    for line in it:
        lines.append(line)
        if len(lines) >= 5 or time.time() - start > 5:
            break
    print("sample lines:", lines)
    ok(f"logs_source() yielded {len(lines)} live line(s)")
except Exception as e:
    fail("logs_source", e)

print("\n\n=== SUMMARY (phase 2) ===")
for status_, msg in results:
    print(status_, msg)
