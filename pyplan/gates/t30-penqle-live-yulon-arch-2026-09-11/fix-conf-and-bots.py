import sys, os, subprocess
from pathlib import Path
from datetime import datetime
sys.path.insert(0, "/home/pk/y8-t30/pylauncher")
from yulon.log import configure, use_utf8_streams
use_utf8_streams()
configure()
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_tortoise import modules as tortoise_modules
from yulon.controller_wow_tortoise import autoupdate as tortoise_autoupdate
from yulon import docker
from yulon.apply import DockerSql

SERVER = Path("/home/pk/tortoise-penqle")
CLIENT = Path("/home/pk/clients/TurtleWoW")
entry = load_catalog().get("wow-tortoise")
spec = entry.container_spec()
os.environ["MYSQL_PWD"] = (SERVER / ".db_password").read_text().strip()
sql = DockerSql(spec.db, entry.install.native.db)
now = lambda: datetime.now().astimezone().isoformat()

print(now())
print("# T30 Half 2 live -- capture 23: conf activation and Browse bots (fixes)")
print()

# --- Fix #4: Browse bots via _BotBrowser ---
print("## Browse bots through _BotBrowser.page()")
print("## _BotBrowser is the exact wrapper the Bots tab uses (controller_view.py:1856)")
# Use the same class the tab uses
from yulon.ui.controller_view import _BotBrowser
browser = _BotBrowser(entry, SERVER, sql)
pg = browser.page()
print(f"total: {pg.total}")
print(f"problem: {pg.problem!r}")
print(f"bots ({len(pg.bots)}):")
for bot in pg.bots[:10]:
    print(f"  {bot}")
if pg.total and pg.total > len(pg.bots):
    print(f"  ... and {pg.total - len(pg.bots)} more")
print()

# SQL corroboration
cnt = subprocess.run(
    ["docker", "exec", "-e", "MYSQL_PWD", spec.db, "mariadb", "-uroot", "-N", "-B", "tw_logon",
     "-e", "SELECT COUNT(*) FROM account WHERE username LIKE CONCAT(CHAR(82,78,68,66,79,84),CHAR(37))"],
    capture_output=True, text=True, env=os.environ).stdout.strip()
online = subprocess.run(
    ["docker", "exec", "-e", "MYSQL_PWD", spec.db, "mariadb", "-uroot", "-N", "-B", "tw_char",
     "-e", "SELECT COUNT(*) FROM characters WHERE online=1"],
    capture_output=True, text=True, env=os.environ).stdout.strip()
print(f"SQL corroboration: RNDBOT accounts={cnt}, bots online={online}")
print()

# --- Fix #2: conf activation ---
print("## Modules conf activation through applier.install()")
print("## stopping world (the guard requires it for restart-bearing manifests)")
subprocess.run(["docker", "stop", "tortoise-mangosd"], capture_output=True)
print(f"world stopped at {now()}")

def _cda(cd):
    return cd if cd and (cd / "Interface" / "AddOns").is_dir() else None
applier = tortoise_modules.applier(
    SERVER, sql=sql,
    arming=lambda: tortoise_autoupdate.read_arming(
        SERVER, world_container=spec.world, schemas=entry.schema_map(), sql=sql),
    world_running=lambda: docker.world_running(spec.world),
    start_database=lambda: docker.start_database(spec, SERVER, because="conf"),
    client_dir=_cda(CLIENT),
)
store = tortoise_modules.store()
perf = store.load("mod", "perf-report")
print(f"manifest: {perf.id} ({perf.name})")
report = applier.install(perf, {})
print(f"report: action={report.action} item_id={report.item_id}")
print(f"  done: {report.done}")
print(f"  skipped: {report.skipped}")
print(f"  rebuild_required: {report.rebuild_required}")
print(f"  restart_recommended: {report.restart_recommended}")
print()
out = subprocess.run(["grep", "-n", "-E", "Perf[.]Enable|Perf[.]ReportInterval",
    str(SERVER / "etc" / "mangosd.conf")], capture_output=True, text=True).stdout.strip()
print(f"mangosd.conf:\n{out}")
print()
subprocess.run(["docker", "start", "tortoise-mangosd"], capture_output=True)
print(f"world restarted at {now()}")
