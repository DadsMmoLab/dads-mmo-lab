#!/usr/bin/env bash
# Independent re-derivation of the 7.1 lane's compose-fixture comparison, by a
# different lane, with its own script, at the tip code. Read-only: it runs
# `docker compose config` and nothing else against the install.
set -u
OUT=/home/pk/gate710/compose-recheck.txt
CO=/home/pk/gate0904/checkout
PY=/home/pk/gate0904/venv/bin/python
SD=/home/pk/wowserver
{
  echo "=== independent compose re-check, $(date -Is), lane 7.2/7.10"
  echo "checkout : $CO at $(git -C $CO rev-parse HEAD)"
  echo "server   : $SD"
  echo "docker   : $(docker --version)"
  echo "compose  : $(docker compose version)"
  echo
  echo "--- route A: docker compose config (YAML), the route the fixture brief did NOT use"
  ( cd "$SD" && docker compose config > /tmp/rc.yml 2>/tmp/rc.yml.err ); echo "exit $?  lines $(wc -l < /tmp/rc.yml)  stderr bytes $(wc -c < /tmp/rc.yml.err)"
  echo
  echo "--- route B: docker compose config --format json, the route the brief documents"
  ( cd "$SD" && docker compose config --format json > /tmp/rc.json 2>/tmp/rc.json.err ); echo "exit $?  bytes $(wc -c < /tmp/rc.json)  stderr bytes $(wc -c < /tmp/rc.json.err)"
  echo
  echo "--- the brief's own transform, applied verbatim to route B"
  "$PY" - <<PYEOF
import json, pathlib
raw = json.load(open("/tmp/rc.json"))
raw.pop("name", None)
root = "$SD"
text = json.dumps(raw, indent=2, sort_keys=True).replace(root + "/", "./").replace(root, ".")
pathlib.Path("/tmp/rc-transformed.json").write_text(text + "\n")
print("services:", sorted(raw["services"]))
print("absolute /home/ paths remaining:", text.count("/home/"))
PYEOF
  echo
  echo "--- byte diff: brief-transform of MY capture  vs  the committed fixture"
  diff <(sed 's/\r$//' "$CO/pylauncher/tests/data/wotlk-compose-config.json") /tmp/rc-transformed.json > /tmp/rc.diff
  echo "differing lines: $(wc -l < /tmp/rc.diff)"
  sed 's/^/  /' /tmp/rc.diff
  echo
  echo "--- the project's own seam: support_compose.compare()"
  cd "$CO/pylauncher" && "$PY" - <<PYEOF
import json, sys
sys.path.insert(0, ".")
from tests import support_compose
fixture = json.load(open("tests/data/wotlk-compose-config.json"))
mine    = json.load(open("/tmp/rc-transformed.json"))
svc = support_compose.compare(fixture, mine)
print("service differences (fixture vs my capture):", len(svc))
for d in svc: print("   ", d)
stack = support_compose.compare_stack(fixture, mine)
print("stack differences (fixture vs my capture):", len(stack))
for d in stack: print("   ", d)
print("control - compare_stack(fixture, fixture):", len(support_compose.compare_stack(fixture, fixture)))
print("control - compare_stack(mine, mine):      ", len(support_compose.compare_stack(mine, mine)))
PYEOF
} > "$OUT" 2>&1
echo "wrote $OUT"
