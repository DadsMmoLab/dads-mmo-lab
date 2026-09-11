#!/usr/bin/env bash
# T30 Half 1 — write the confs by hand the way catalog.json's conf block would,
# then bring the world up once. The DB password reaches the conf from
# ~/t30/.db_password through the environment; it is never echoed.
set -euo pipefail
cd "$HOME/t30"
DB_PASS="$(cat .db_password)"

echo "--- the conf files the build installed"
docker rm -f t30-conf >/dev/null 2>&1 || true
docker create --name t30-conf t30/tortoise-core >/dev/null
rm -rf etc && mkdir -p etc
docker cp t30-conf:/opt/tortoise/etc/. etc/ >/dev/null
docker rm t30-conf >/dev/null
find etc -type f | sort

echo "--- copying every *.dist to its live name"
find etc -name "*.conf.dist" | while read -r f; do cp "$f" "${f%.dist}"; done
[ -f etc/mangosd.conf ] || cp etc/mangosd.conf.dist etc/mangosd.conf
ls etc etc/modules 2>/dev/null

echo "--- mangosd.conf: the keys catalog.json:1114-1128 writes, plus what this tree needs"
python3 setconf.py etc/mangosd.conf \
  "LoginDatabase.Info=\"t30-db;3306;mangos;$DB_PASS;tw_logon\"" \
  "WorldDatabase.Info=\"t30-db;3306;mangos;$DB_PASS;tw_world\"" \
  "CharacterDatabase.Info=\"t30-db;3306;mangos;$DB_PASS;tw_char\"" \
  "LogsDatabase.Info=\"t30-db;3306;mangos;$DB_PASS;tw_logs\"" \
  'Database.AutoUpdate.Path="/opt/tortoise/sql/database_updates/"' \
  'DataDir="/opt/tortoise/data"' \
  'WorldServerPort=8090' \
  'GameType=0' \
  'GM.LoginState=0' \
  'GM.StartLevel=1' \
  'AutoHonorRestart=0' \
  'AutoRestart.MaxServerUptime=0' \
  'Console.Enable=1'

echo "--- mangosd.conf, with the password masked:"
grep -nE "^(LoginDatabase\.Info|WorldDatabase\.Info|CharacterDatabase\.Info|LogsDatabase\.Info|Database\.AutoUpdate|DataDir|WorldServerPort|GameType|GM\.|AutoHonorRestart|AutoRestart\.MaxServerUptime|Console\.Enable)" etc/mangosd.conf \
  | sed "s/$DB_PASS/<GENERATED-PASSWORD>/g"

echo "--- aiplayerbot.conf: the three keys catalog.json:1107-1110 writes, as far as this tree still has them"
python3 setconf.py etc/aiplayerbot.conf \
  'AiPlayerbot.Enabled=1' \
  'AiPlayerbot.MinRandomBots=5' \
  'AiPlayerbot.MaxRandomBots=5' \
  'AiPlayerbot.RandomBotAutoCreate=1' \
  'AiPlayerbot.RandomBotAutologin=1' \
  'AiPlayerbot.RandomBotLoginAtStartup=1' \
  'AiPlayerbot.RandomBotAccountPrefix=RNDBOT'
grep -nE "^AiPlayerbot\.(Enabled|MinRandomBots|MaxRandomBots|RandomBot)" etc/aiplayerbot.conf

echo "--- module conf"
ls -l etc/modules 2>&1 || echo "(no etc/modules directory)"

echo "--- client data offered read-only from the old fork's extraction"
du -sh "$HOME/tortoise-vm/data"
ls "$HOME/tortoise-vm/data"

echo "--- starting the world (one run)"
rm -f console.in; : > console.in
docker rm -f t30-mangosd >/dev/null 2>&1 || true
nohup bash -c "tail -f $HOME/t30/console.in | docker run -i --name t30-mangosd --network t30-net \
  -v $HOME/t30/etc:/opt/tortoise/etc \
  -v $HOME/tortoise-vm/data:/opt/tortoise/data:ro \
  t30/tortoise-core ./mangosd >> $HOME/t30/evidence/08-world.log 2>&1" >/dev/null 2>&1 &
echo "world started at $(date -Is)"
