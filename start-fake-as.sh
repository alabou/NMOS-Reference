#!/usr/bin/env bash
# Test OAuth 2.0 Authorization Server — the Keycloak-free path.
#
# Usage:
#   start-fake-as.sh [--port=P] [--tct=T] [--serial=S] [--control-port=P]
#                    [--client-id=ID] [--client-secret=S]
#                    [--operator=NAME] [--password=PW]
#
#   --port=P          Listen port (default: 9443, same as start-keycloak.sh)
#   --tct=T           TLS Certificate Type: 0=RSA (default), 1=ECDSA
#   --serial=S        Node serial the issued tokens are scoped to
#                     (default: SNX00001). Sets the token 'aud' entry and
#                     the registered redirect URIs.
#   --control-port=P  Controller UI port to register redirect URIs for
#                     (default: 5050, matching start-node1.sh)
#   --client-id=ID    OAuth 2.0 client_id (default: the value baked into
#                     start-node1.sh's --oauth2ClientId)
#   --client-secret=S Client secret (default: secret, as start-node1.sh)
#   --operator=NAME   Pre-canned sign-in account (default: ipmx-operator)
#   --password=PW     Its password (default: admin, the same password
#                     start-node1.sh gives --controllerAdminPassword)
#
# This is a drop-in replacement for keycloak/start-keycloak.sh: same host,
# same port, same certificate, same realm path. It needs no Docker, so the
# TLS + OAuth 2.0 tutorial runs from a plain checkout.
#
# It works because the Controller locates endpoints through the RFC 8414
# metadata document (IS-10 "Behaviour - Clients.md": a client "MUST NOT
# assume that every Authorization Server instance on a network uses the
# same endpoint locations"). Nothing here imitates Keycloak's URL layout --
# this server publishes /authorize, /token and /jwks under the realm path,
# and the Controller follows what it publishes. Pointing the Controller at
# two servers with different layouts and having both work is precisely the
# property that discovery buys.
#
# Pair with the Config C rig:
#
#     ./start-fake-as.sh &
#     ./start-registry.sh 2 &
#     ./start-node1.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2
#
# The implementation ships in fake-as/, a byte-identical copy of the one the
# TR-10-SEC validator uses in the separately-released security/ project — so
# the tutorial and the certification suite exercise one Authorization Server
# rather than two lookalikes. See ./sync-fake-as.sh.
#
# Requires hosts-file entries. This script addresses its peers by DNS name
# because the certificates carry DNS SANs (XYZ-SNX000nn) and an IP literal
# matches none of them. Map to 127.0.0.1 in /etc/hosts before running:
#
#     127.0.0.1   XYZ-SNX00000    # registry + Authorization Server
#     127.0.0.1   XYZ-SNX00001    # node 1 + Controller UI
#     127.0.0.1   XYZ-SNX00002    # node 2
#
# Passing 127.0.0.1 as the registry-host argument fails TLS verification for
# the same reason -- pass XYZ-SNX00000.

set -e

AS_PORT=9443
TCT=0
NODE_SERIAL="SNX00001"
CONTROL_PORT=5050
CLIENT_ID="Example.Company.Device.Client.ABC.SNX00001.example.com"
CLIENT_SECRET="secret"
OPERATOR="ipmx-operator"
PASSWORD="admin"

for arg in "$@"; do
  case "$arg" in
    --port=*)          AS_PORT="${arg#*=}" ;;
    --tct=*)           TCT="${arg#*=}" ;;
    --serial=*)        NODE_SERIAL="${arg#*=}" ;;
    --control-port=*)  CONTROL_PORT="${arg#*=}" ;;
    --client-id=*)     CLIENT_ID="${arg#*=}" ;;
    --client-secret=*) CLIENT_SECRET="${arg#*=}" ;;
    --operator=*)      OPERATOR="${arg#*=}" ;;
    --password=*)      PASSWORD="${arg#*=}" ;;
    *) echo "start-fake-as.sh: unknown arg $arg" >&2; exit 64 ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Prefer the certificate subset bundled inside this repository, so a
# standalone clone of nmos-reference runs without the wider workspace PKI.
# SNX00000 is the reserved infrastructure serial: the registry and this
# Authorization Server both present it. An explicit IPMX_CERT_ROOT wins.
CERT_PROBE="pem/ExampleDeviceServer.ABC.SNX00000.chain.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
else
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
fi
CERTS="$CERT_ROOT/build.0"

case "$TCT" in
  0|2) AS_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00000.chain.pem"
       AS_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00000.key" ;;
  1)   AS_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00000.chain.ec.pem"
       AS_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00000.ec.key" ;;
  *) echo "start-fake-as.sh: unsupported --tct=$TCT" >&2; exit 64 ;;
esac

for f in "$AS_CERT" "$AS_KEY"; do
  if [ ! -f "$f" ]; then
    echo "start-fake-as.sh: missing $f" >&2
    echo "  Set IPMX_CERT_ROOT to a Certificates/ tree carrying SNX00000." >&2
    exit 66
  fi
done

# The Authorization Server ships vendored in this repository, so a checkout
# of nmos-reference alone can run the tutorial. When the security/ project is
# alongside it is preferred, because that copy is the source of truth and a
# developer editing it should see the effect without syncing first.
# ./sync-fake-as.sh keeps the two byte-identical, and a test enforces it.
FAKE_AS=""
for candidate in \
  "${IPMX_SECURITY_ROOT:-$SCRIPT_DIR/../security}/ipmx_fake_as.py" \
  "$SCRIPT_DIR/fake-as/ipmx_fake_as.py"
do
  if [ -f "$candidate" ]; then FAKE_AS="$candidate"; break; fi
done
if [ -z "$FAKE_AS" ]; then
  echo "start-fake-as.sh: no Authorization Server found." >&2
  echo "  Looked for fake-as/ipmx_fake_as.py in this repository and for a" >&2
  echo "  security/ project alongside it. Restore fake-as/ (see" >&2
  echo "  ./sync-fake-as.sh), or use start-node1-noauth2.sh for a rig with" >&2
  echo "  no OAuth 2.0." >&2
  exit 66
fi

# The hostname must be the lowercase form. aiohttp normalises the inbound
# Host header to lowercase before the Controller reads it, and the
# Controller builds its redirect_uri from that header -- while redirect-URI
# matching, unlike TLS hostname matching, is case-sensitive (RFC 6125 makes
# only the latter case-insensitive). Registering the uppercase SAN spelling
# here would reject every real callback.
NODE_HOST="xyz-$(echo "$NODE_SERIAL" | tr '[:upper:]' '[:lower:]')"

# Exact-match redirect URIs -- no wildcards. IS-10 "Behaviour - Clients.md":
# "Redirect URIs MUST be complete (fully-qualified) and not use
# pattern-matching, as this makes them susceptible to Redirect URI
# Validation Attacks." The loopback spellings are here because a browser on
# the Windows side of WSL2 reaches the Controller that way.
REDIRECT_ARGS=()
for host in "$NODE_HOST" "127.0.0.1" "localhost"; do
  REDIRECT_ARGS+=(--redirect-uri "https://${host}:${CONTROL_PORT}/controller/oauth2/callback")
done

echo "Authorization Server (test)   https://XYZ-SNX00000:${AS_PORT}/realms/TR-10-SEC"
echo "  metadata   /.well-known/oauth-authorization-server/realms/TR-10-SEC"
echo "  sign in as ${OPERATOR} / ${PASSWORD}"
echo "  client     ${CLIENT_ID}"
echo "  tokens aud XYZ-${NODE_SERIAL}"
echo

exec python3 "$FAKE_AS" \
  --host XYZ-SNX00000 \
  --port "$AS_PORT" \
  --cert "$AS_CERT" \
  --key  "$AS_KEY" \
  --api-selector realms/TR-10-SEC \
  --default-aud "XYZ-${NODE_SERIAL}" \
  --client-id "$CLIENT_ID" \
  --client-secret "$CLIENT_SECRET" \
  "${REDIRECT_ARGS[@]}" \
  --operator-username "$OPERATOR" \
  --operator-password "$PASSWORD"
