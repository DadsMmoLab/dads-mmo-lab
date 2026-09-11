#!/usr/bin/env bash
# T30 Half 1 — the `.bot` surface as the console sees it, then shut down.
# Console commands are typed WITHOUT the leading dot (CliRunnable.cpp:563-588
# feeds the line straight to ChatHandler::ExecuteCommand at SEC_CONSOLE).
set -euo pipefail
cd "$HOME/t30"
DB_PASS="$(cat .db_password)"
MARK="=== T30 CONSOLE PROBE ==="

send() { printf '%s\n' "$1" >> console.in; sleep "${2:-3}"; }

echo "$MARK" >> evidence/08-world.log
send 'server info' 3
for c in 'bot' 'bot help' 'bot list' 'bot roster' 'bot stats' 'bot status' \
         'bot add Nobody' 'bot summon Nobody' 'bot ah help' 'bot nosuchthing'; do
  printf '\n--- console: %s\n' "$c" >> evidence/08-world.log
  send "$c" 3
done
sleep 5
