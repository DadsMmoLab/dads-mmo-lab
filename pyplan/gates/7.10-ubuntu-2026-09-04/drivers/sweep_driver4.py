import sys
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

section("Networking: apply(lan) -- firewall helpers + realmlist updater, live")
try:
    plan = services.network_plan("lan")
    print("plan:", plan)
    print("firewall_commands:", plan.firewall_commands)
    print("realmlist_sql:", plan.realmlist_sql)
    print("manual_steps:", plan.manual_steps)
    report = services.network_apply(plan)
    print("NetworkReport.done:", report.done)
    print("NetworkReport.skipped:", report.skipped)
    print("restart_required:", report.restart_required)
    ok(f"network_apply(lan) -> done={report.done} skipped={report.skipped}")
except Exception as e:
    fail("network_apply(lan)", e)

section("Networking: write_client_realmlist() (round-trips a client file)")
try:
    from yulon.networking import write_client_realmlist

    fake_client = Path.home() / "gate0904" / "sweep_fake_client"
    fake_client.mkdir(parents=True, exist_ok=True)
    result_path = write_client_realmlist(fake_client, "192.168.1.50")
    content = result_path.read_text()
    print("wrote:", result_path, "content:", content)
    assert "192.168.1.50" in content
    ok(f"write_client_realmlist() wrote {result_path} with correct address")
except Exception as e:
    fail("write_client_realmlist()", e)

section("Interrupted-restore bookkeeping: interrupted_restore() / forget_interrupted()")
try:
    ir = services.interrupted_restore()
    print("interrupted_restore():", ir)
    ok(f"interrupted_restore() -> {ir} (None expected: our restore completed cleanly)")
except Exception as e:
    fail("interrupted_restore()", e)

print("\n\n=== SUMMARY (phase 4) ===")
for status_, msg in results:
    print(status_, msg)
