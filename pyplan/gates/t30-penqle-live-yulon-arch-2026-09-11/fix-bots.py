import sys, os, subprocess
from pathlib import Path
from datetime import datetime
sys.path.insert(0, "/home/pk/y8-t30/pylauncher")
from yulon.log import configure, use_utf8_streams
use_utf8_streams()
configure()
from yulon.catalog.catalog import load_catalog
from yulon.apply import DockerSql

SERVER = Path("/home/pk/tortoise-penqle")
entry = load_catalog().get("wow-tortoise")
spec = entry.container_spec()
password = (SERVER / ".db_password").read_text().strip()
os.environ["MYSQL_PWD"] = password

# Construct DockerSql the way _sql_for does (controller_view.py:770)
sql = DockerSql(
    spec.db,
    password,
    schemas=entry.schema_map(),
)

now = lambda: datetime.now().astimezone().isoformat()
print()
print(f"{now()}")
print("## Browse bots FIX: _BotBrowser with correct DockerSql construction")
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
