#!/usr/bin/env bash
# T30 Half 1 — database bring-up for the one world run.
# The DB password is generated into ~/t30/.db_password (mode 600), passed to
# every consumer through the environment, and never printed into a capture.
set -euo pipefail
cd "$HOME/t30"

if [ ! -f .db_password ]; then
  umask 077; openssl rand -hex 16 > .db_password
fi
DB_PASS="$(cat .db_password)"

docker network inspect t30-net >/dev/null 2>&1 || docker network create t30-net >/dev/null

echo "--- starting t30-db (mariadb:10.6)"
docker rm -f t30-db >/dev/null 2>&1 || true
docker run -d --name t30-db --network t30-net \
  -e MARIADB_ROOT_PASSWORD="$DB_PASS" \
  -e MARIADB_ROOT_HOST=% \
  mariadb:10.6 --max_allowed_packet=128M >/dev/null

for i in $(seq 1 90); do
  if docker exec -e MYSQL_PWD="$DB_PASS" t30-db mariadb -uroot -e "SELECT 1" >/dev/null 2>&1; then break; fi
  sleep 2
done
docker exec -e MYSQL_PWD="$DB_PASS" t30-db mariadb -uroot -e "SELECT VERSION()"

run_sql() { docker exec -i -e MYSQL_PWD="$DB_PASS" t30-db mariadb -uroot "$@"; }

echo "--- importing sql/create_databases.sql"
run_sql < src/tortoise-wow/sql/create_databases.sql

N=$(ls src/tortoise-wow/sql/base/*.sql | wc -l)
echo "--- importing sql/base/*.sql into tw_world ($N files)"
for f in src/tortoise-wow/sql/base/*.sql; do
  run_sql tw_world < "$f" || { echo "FAILED on $f"; exit 1; }
done

echo "--- app user and grants (password read from the environment inside the container)"
docker exec -e MYSQL_PWD="$DB_PASS" -e P="$DB_PASS" t30-db bash -c 'mariadb -uroot -e "
CREATE USER IF NOT EXISTS '\''mangos'\''@'\''%'\'' IDENTIFIED BY '\''$P'\'';
GRANT ALL ON \`tw_world\`.* TO '\''mangos'\''@'\''%'\'';
GRANT ALL ON \`tw_char\`.* TO '\''mangos'\''@'\''%'\'';
GRANT ALL ON \`tw_logon\`.* TO '\''mangos'\''@'\''%'\'';
GRANT ALL ON \`tw_logs\`.* TO '\''mangos'\''@'\''%'\'';
FLUSH PRIVILEGES;"'

echo "--- realm row"
run_sql tw_logon -e "REPLACE INTO realmlist (id,name,address,port,icon,realmflags,timezone,allowedSecurityLevel,population,realmbuilds) VALUES (1,'Tortoise WoW','127.0.0.1',8090,0,0,0,0,0,'7272')"

echo "--- table counts after base import"
run_sql -e "SELECT table_schema, COUNT(*) AS tables FROM information_schema.tables WHERE table_schema LIKE 'tw\\_%' GROUP BY table_schema"
