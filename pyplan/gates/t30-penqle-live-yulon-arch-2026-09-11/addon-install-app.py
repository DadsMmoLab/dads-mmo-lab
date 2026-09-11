"""T30 Half 2 live: Addon install through the app's own applier.

This is the exact path _module_action("install") takes:
applier.install(manifest, values) -> ApplyReport.
"""
import sys, os
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

def _client_dir_for_addons(cd):
    if cd is None:
        return None
    return cd if (cd / "Interface" / "AddOns").is_dir() else None

applier = tortoise_modules.applier(
    SERVER, sql=sql,
    arming=lambda: tortoise_autoupdate.read_arming(
        SERVER, world_container=spec.world, schemas=entry.schema_map(), sql=sql),
    world_running=lambda: docker.world_running(spec.world),
    start_database=lambda: docker.start_database(spec, SERVER, because="addon install"),
    client_dir=_client_dir_for_addons(CLIENT),
)

store = tortoise_modules.store()
now = lambda: datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

for addon_id in ["tortoise-bots-manager", "tortoise-gm-manager"]:
    m = store.load("mod", addon_id)
    print(f"[{now()}] installing {m.id}: {m.name}")
    report = applier.install(m, {})
    print(f"  steps: {report.steps}, skipped: {report.skipped}")
    print(f"  rebuild: {report.rebuild}, restart_recommended: {report.restart_recommended}")
    for line in getattr(report, "lines", []):
        print(f"  > {line}")
    print()

addons = CLIENT / "Interface" / "AddOns"
print(f"[{now()}] AddOns folder:")
for d in sorted(addons.iterdir()):
    if d.is_dir() and "Tortoise" in d.name:
        tocs = list(d.glob("*.toc"))
        print(f"  {d.name}/  .toc={[t.name for t in tocs]}")
