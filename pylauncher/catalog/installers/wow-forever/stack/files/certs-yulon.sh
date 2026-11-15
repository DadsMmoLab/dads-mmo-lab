#!/bin/bash
# Yu'lon's own certificate script for WoW Forever's stack (T888).
#
# The image's /opt/forever/yulon/certs.sh does the same job with one difference that
# matters: its CA is name-constrained to `actual.battle.net`, which also covers
# Blizzard's real us./eu. login hosts, and it leaves `ca.key` in the certs folder this
# player's account can write to (mounted into the Proton sandbox beside it). A CA key
# anybody can read is a CA anybody can sign Blizzard's names with.
#
# So this one constrains the CA to exactly the name this stack's login server answers
# for, and the CA key never leaves this container's temporary folder: `compose run --rm`
# deletes that folder with the container. What lands in /srv/certs is only what the
# servers read: ca.pem, server.pem, chain.pem, and server.key (0600).
#
# It runs once, before the first `up` of a fresh install (the `certificates` stage). With
# `--force` it makes the set again for an install whose CA must be replaced (Play's
# remake): the new set is built and verified in /srv/certs/.staging, INSIDE this
# container and as root, and only then swapped into /srv/certs. Yu'lon never deletes a
# file in certs/ from the host, where rootful Docker owns the folder; and a run that
# fails at openssl leaves the old set exactly where it was.
set -euo pipefail

C=/srv/certs
NAME=trinity.actual.battle.net
FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1
mkdir -p "$C"

if [ "$FORCE" = 0 ] \
    && [ -f "$C/chain.pem" ] && [ -f "$C/server.key" ] && [ -f "$C/server.pem" ] \
    && [ -f "$C/ca.pem" ]; then
    # Even when the certificates are already there: a key from the image's script is a
    # key that must not stay in the player's folder.
    rm -f "$C/ca.key"
    echo "certificates ready"
    exit 0
fi

umask 077
T=$(mktemp -d)
S="$C/.staging"
rm -rf "$S"
mkdir -p "$S"
trap 'rm -rf "$T" "$S"' EXIT

cat > "$T/ca.cnf" <<'EOF'
[req]
distinguished_name = dn
x509_extensions = ca_ext
prompt = no
[dn]
CN = Yulon WoW Forever Local CA
[ca_ext]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
nameConstraints = critical,permitted;DNS:trinity.actual.battle.net
EOF

cat > "$T/server.cnf" <<'EOF'
[req]
distinguished_name = dn
prompt = no
[dn]
CN = trinity.actual.battle.net
EOF

cat > "$T/leaf.ext" <<'EOF'
subjectAltName = DNS:trinity.actual.battle.net
extendedKeyUsage = serverAuth
authorityKeyIdentifier = keyid:always
EOF

openssl req -x509 -newkey rsa:3072 -nodes -keyout "$T/ca.key" -out "$T/ca.pem" \
    -days 3650 -config "$T/ca.cnf"
openssl req -new -newkey rsa:2048 -nodes -keyout "$T/server.key" -out "$T/server.csr" \
    -config "$T/server.cnf"
openssl x509 -req -in "$T/server.csr" -CA "$T/ca.pem" -CAkey "$T/ca.key" \
    -CAcreateserial -out "$T/server.pem" -days 825 -extfile "$T/leaf.ext"
cat "$T/server.pem" "$T/ca.pem" > "$T/chain.pem"
openssl verify -CAfile "$T/ca.pem" "$T/server.pem"

# The whole set, verified, staged in the folder it will live in (same filesystem, so
# each move is a rename). Only now does anything in /srv/certs change.
cp "$T/ca.pem" "$T/server.pem" "$T/chain.pem" "$T/server.key" "$S/"
chmod 644 "$S/ca.pem" "$S/server.pem" "$S/chain.pem"
chmod 600 "$S/server.key"
mv -f "$S/ca.pem" "$S/server.pem" "$S/chain.pem" "$S/server.key" "$C/"
# What no set writes any more: the image's CA key, and any file the old script left that
# this one does not (the CSR, the serial, the staging folder itself).
rm -f "$C/ca.key" "$C/server.csr" "$C/ca.srl" "$C/.staging/ca.key"
rm -rf "$S"
echo "certificates ready for $NAME"
