"""Phase 3: live backup -> verify -> restore cycle, using the exact GUI wiring.

Backs up the CURRENT live databases (safe: 'safe to run while people are
playing'), verifies the dump, stops only auth+world (leaves the db container
up, which plan_restore requires), restores the same data back into itself
(idempotent -- no data loss since it's the current state round-tripped), then
restarts auth+world and lets bots relog.
"""
import subprocess
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

# ----------------------------------------------------------------- backup
section("Backup: services.backup() live, while server is running")
report = None
try:
    report = services.backup()
    print("BackupReport:", report)
    print("dumps:", [(d.database, d.path, d.size_bytes) for d in report.dumps])
    ok(f"backup() -> {len(report.dumps)} dump(s) in {report.directory}, server_was_running={report.server_was_running}")
except Exception as e:
    fail("backup()", e)

# ------------------------------------------------------------ verify_dump
section("Verify: maintenance.verify_dump() on each dump")
try:
    from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance

    if report is None:
        raise RuntimeError("no report from backup() step")
    for d in report.dumps:
        size = wotlk_maintenance.verify_dump(d.path, d.database)
        print(f"  verify_dump({d.path.name}) -> {size} bytes")
    ok(f"verify_dump() passed for all {len(report.dumps)} dump(s)")
except Exception as e:
    fail("verify_dump()", e)

# ------------------------------------------------------- restore refusal
section("Restore refusal: plan_restore() while world/auth are running")
try:
    pick = report.dumps[0].path
    plan = services.plan_restore(pick)
    print("plan.refusals (should be non-empty, server running):", plan.refusals)
    if plan.refusals:
        ok(f"plan_restore() correctly refused while running: {plan.refusals}")
    else:
        fail("plan_restore() should have refused while world/auth are running but did not")
except Exception as e:
    fail("plan_restore() refusal check", e)

# ------------------------------------------------ stop auth+world only
section("Stopping ac-authserver + ac-worldserver only (db stays up, as restore requires)")
try:
    subprocess.run(["sudo", "docker", "stop", "ac-authserver", "ac-worldserver"], check=True)
    ok("docker stop ac-authserver ac-worldserver succeeded")
except Exception as e:
    fail("docker stop ac-authserver ac-worldserver", e)

time.sleep(3)

# ------------------------------------------------------------ real restore
section("Restore: plan_restore() + restore() with db up, world/auth down")
try:
    pick = report.dumps[0].path
    plan2 = services.plan_restore(pick)
    print("plan2.refusals (should now be empty):", plan2.refusals)
    print("plan2.databases:", plan2.databases, "token:", plan2.token)
    if plan2.refusals:
        fail(f"plan_restore() still refused with world/auth stopped: {plan2.refusals}")
    else:
        ok("plan_restore() allowed the restore with world/auth stopped")
        restore_report = services.restore(plan2)
        print("RestoreReport:", restore_report)
        ok(f"restore() succeeded: restored {restore_report.databases} from {restore_report.backup}")
except Exception as e:
    fail("restore()", e)

# -------------------------------------------------------- bring server back
section("Restarting the install (controller.start())")
try:
    services.controller.start()
    ok("controller.start() issued")
except Exception as e:
    fail("controller.start()", e)

print("\n\n=== SUMMARY (phase 3) ===")
for status_, msg in results:
    print(status_, msg)
