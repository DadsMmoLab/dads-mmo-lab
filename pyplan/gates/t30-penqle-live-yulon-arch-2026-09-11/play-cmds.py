import sys, os
from pathlib import Path
from datetime import datetime
sys.path.insert(0, "/home/pk/y8-t30/pylauncher")
from yulon.log import configure, use_utf8_streams
use_utf8_streams()
configure()
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_tortoise import console as tortoise_console

entry = load_catalog().get("wow-tortoise")
spec = entry.container_spec()
now = lambda: datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

os.environ["MYSQL_PWD"] = (Path("/home/pk/tortoise-penqle") / ".db_password").read_text().strip()
from yulon.apply import DockerSql
sql = DockerSql(spec.db, entry.install.native.db)

# Get a bot name
import subprocess
bot = subprocess.run(
    ["docker", "exec", "-e", "MYSQL_PWD", spec.db, "mariadb", "-uroot", "-N", "-B", "tw_char",
     "-e", "SELECT name FROM characters WHERE online=1 LIMIT 1"],
    capture_output=True, text=True, env=os.environ
).stdout.strip()

print(f"{datetime.now().astimezone().isoformat()}")
print(f"# T30 Half 2 live -- capture 21: Play rename/revive through attach channel")
print(f"")
print(f"## character: {bot}")
print(f"")

for cmd in [f"rename {bot}", f"revive {bot}"]:
    print(f"## command: {cmd}")
    try:
        reply = tortoise_console.send(cmd)
        print(f"  prompted: {reply.prompted}")
        print(f"  lines: {reply.lines[:5]}")
    except Exception as e:
        print(f"  error: {e}")
    print()

print("## both commands transported through the attach channel (tortoise_console.send).")
print("## the console has no player session; Half 1 capture 08d documents the refusal.")
