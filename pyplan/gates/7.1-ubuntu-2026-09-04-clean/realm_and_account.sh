#!/usr/bin/env bash
# 7.1 gate, second half on yulon-ubuntu, 2026-09-04:
#   TASK 1 - point the realm row at the Tailscale address, BY HAND.
#            The launcher's LAN networking step is NOT used: it is
#            bug-checklist section 39 (it enables ufw with only the game
#            ports allowed and cuts SSH to this box, which has no console).
#   TASK 2 - create a game account through ControllerServices, the seam the
#            Server tab's Accounts tile calls (clause 13).
# Every command below is echoed with its real output. Nothing is committed.

TS_ADDR=100.71.125.58
DB=ac-database
MYSQL=(docker exec "$DB" mysql -uroot -ppassword)

run() {
  echo
  echo "\$ $*"
  "$@" 2>&1
  echo "[exit $?]"
}

sql() {
  echo
  echo "\$ docker exec $DB mysql -e \"$1\""
  "${MYSQL[@]}" -t -e "$1" 2>&1
  echo "[exit $?]"
}

echo "=============================================================="
echo "host:        $(hostname)  ($(date -Is))"
echo "checkout:    /home/pk/gate71 @ $(cd /home/pk/gate71 && git rev-parse --short HEAD)"
echo "server dir:  /home/pk/wowserver"
echo "tailscale:   $TS_ADDR (node yulon-ubuntu-1)"
echo "=============================================================="

echo
echo "##############  TASK 1: realm row -> Tailscale address  ##############"
echo
echo "-- WHY BY HAND: the launcher's LAN step (svc.network_apply) would run"
echo "-- 'ufw --force enable' with only 3724/8085 allowed and cut SSH to this"
echo "-- box (bug-checklist section 39). It is not invoked anywhere below."

run tailscale ip -4
run tailscale status --self --peers=false

sql "SELECT id, name, address, localAddress, localSubnetMask, port, flag FROM acore_auth.realmlist;"

echo
echo "-- BOTH address and localAddress are set. AzerothCore's"
echo "-- Realm::GetAddressForClient hands out localAddress to a client whose IP"
echo "-- is inside localAddress/localSubnetMask (and to a loopback client), and"
echo "-- address to everyone else. With both equal, every client is told the"
echo "-- Tailscale address whichever branch it takes, so localSubnetMask cannot"
echo "-- override it for a client outside the LAN."

sql "UPDATE acore_auth.realmlist SET address='$TS_ADDR', localAddress='$TS_ADDR' WHERE id=1; SELECT ROW_COUNT() AS rows_changed;"

echo
echo "-- the row as it now reads:"
sql "SELECT id, name, address, localAddress, localSubnetMask, port, flag FROM acore_auth.realmlist;"

echo
echo "##############  LISTENERS: can a remote client reach them  ##############"
run sudo ss -lntp
echo
echo "-- just the two game ports:"
sudo ss -lntp 2>&1 | awk 'NR==1 || /:3724|:8085/'
run docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
echo
echo "-- does the authserver answer ON the Tailscale address (from the VM's own side)?"
run timeout 5 bash -c "cat < /dev/null > /dev/tcp/$TS_ADDR/3724 && echo 'TCP connect to '$TS_ADDR':3724 succeeded'"
run timeout 5 bash -c "cat < /dev/null > /dev/tcp/$TS_ADDR/8085 && echo 'TCP connect to '$TS_ADDR':8085 succeeded'"
echo
echo "-- ufw must still be inactive (that is the whole point of not using the LAN step):"
run sudo ufw status

echo
echo "##############  TASK 2: account through ControllerServices  ##############"
run /home/pk/gate71/pylauncher/.venv/bin/python /home/pk/gate71-account-driver.py

echo
echo "-- final proof, straight from the schema (clause 13's SELECT):"
sql "SELECT id, username FROM acore_auth.account;"

echo
echo "-- and the realm row once more, after the account work:"
sql "SELECT id, name, address, localAddress, localSubnetMask, port FROM acore_auth.realmlist;"
echo
echo "DONE $(date -Is)"
