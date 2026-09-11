#!/usr/bin/env bash
# T30 Half 1 — bot/character counts straight from the database, then down.
set -euo pipefail
cd "$HOME/t30"
DB_PASS="$(cat .db_password)"
q() { docker exec -e MYSQL_PWD="$DB_PASS" t30-db mariadb -uroot -e "$1"; }

{
  date -Is
  echo "# T30 Half 1 capture 11 — the RNDBOT population the run produced, read from the database"
  echo "## accounts whose username starts RNDBOT (dbreads.py:226-227 builds exactly this clause)"
  q "SELECT COUNT(*) AS rndbot_accounts FROM tw_logon.account WHERE UPPER(username) LIKE 'RNDBOT%'"
  q "SELECT id, username FROM tw_logon.account ORDER BY id LIMIT 20"
  echo "## characters, and how many the world had online at shutdown"
  q "SELECT COUNT(*) AS characters FROM tw_char.characters"
  q "SELECT COUNT(*) AS online FROM tw_char.characters WHERE online = 1"
  q "SELECT guid, name, level, online, account FROM tw_char.characters ORDER BY guid LIMIT 20"
  echo "## the module's own tables, world side (catalog.json:1240-1244 wants >= 10 ai_playerbot% in tw_world)"
  q "SELECT COUNT(*) AS ai_playerbot_tables_world FROM information_schema.tables WHERE table_schema='tw_world' AND table_name LIKE 'ai_playerbot%'"
  q "SELECT COUNT(*) AS ai_playerbot_tables_char FROM information_schema.tables WHERE table_schema='tw_char' AND table_name LIKE 'ai_playerbot%'"
  echo "## the auto-updater's own record of what it applied"
  q "SELECT COUNT(*) AS world_migrations FROM tw_world.migrations" 2>&1 || true
  q "SHOW TABLES FROM tw_world LIKE '%migration%'"
  q "SHOW TABLES FROM tw_char LIKE '%migration%'"
} > evidence/11-run-database.txt 2>&1

echo "--- stopping the world (EOF on stdin is a clean shutdown, CliRunnable.cpp:587-590)"
pkill -f "tail -f $HOME/t30/console.in" || true
for i in $(seq 1 60); do
  docker ps --format '{{.Names}}' | grep -q '^t30-mangosd$' || break
  sleep 2
done
docker ps -a --format '{{.Names}} {{.Status}}' | grep '^t30-' || true
