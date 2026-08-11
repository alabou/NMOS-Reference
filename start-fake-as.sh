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
#                     the registered redirect URIs. REPEATABLE: give it once
#                     per Node that should be reachable, and every token
#                     carries all of them in 'aud'. Nodes left out are the
#                     inaccessible ones -- which is how a rig where the
#                     Controller may configure some devices but not others
#                     gets built. The first value also drives the redirect
#                     URIs, since the Controller UI is served by node1.
#   --control-port=P  Controller UI port to register redirect URIs for
#                     (default: 5050, matching start-node1.sh)
#   --client-id=ID    OAuth 2.0 client_id (default: the value baked into
#                     start-node1.sh's --oauth2ClientId)
#   --client-secret=S Client secret (default: secret, as start-node1.sh)
#   --operator=NAME   Pre-canned sign-in account (default: tr-10-sec-operator)
#   --password=PW     Its password (default: admin, the same password
#                     start-node1.sh gives --controllerAdminPassword)
#   --operator-access=A  readwrite (default) or read. A read-only token
#                     lets the Controller display everything and refuses
#                     every state-changing call with 403 — which the
#                     Controller now predicts and greys out in advance.
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
# Pair with the Config C rig -- three Nodes, two of them accessible:
#
#     ./start-fake-as.sh --serial=SNX00001 --serial=SNX00002 &
#     ./start-registry.sh 2 &
#     ./start-node1.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2 &
#     ./start-node2.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2 &
#     ./start-node3.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2
#
# SNX00003 is deliberately absent from the audience list, so the Controller
# discovers it through the registry and reports it as inaccessible instead of
# offering controls that would 403 on the first click. Drop the --serial
# arguments entirely for the single-node tutorial.
#
# The implementation ships in fake-as/, a byte-identical copy of the one the
# TR-10-SEC validator uses in a separately-released project — so
# the tutorial and the certification suite exercise one Authorization Server
# rather than two lookalikes.
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
NODE_SERIALS=()
CONTROL_PORT=5050
CLIENT_ID="Example.Company.Device.Client.ABC.SNX00001.example.com"
CLIENT_SECRET="secret"
OPERATOR="tr-10-sec-operator"
PASSWORD="admin"
OPERATOR_ACCESS="readwrite"

for arg in "$@"; do
  case "$arg" in
    --port=*)          AS_PORT="${arg#*=}" ;;
    --tct=*)           TCT="${arg#*=}" ;;
    --serial=*)        NODE_SERIALS+=("${arg#*=}") ;;
    --control-port=*)  CONTROL_PORT="${arg#*=}" ;;
    --client-id=*)     CLIENT_ID="${arg#*=}" ;;
    --client-secret=*) CLIENT_SECRET="${arg#*=}" ;;
    --operator=*)      OPERATOR="${arg#*=}" ;;
    --password=*)      PASSWORD="${arg#*=}" ;;
    --operator-access=*) OPERATOR_ACCESS="${arg#*=}" ;;
    *) echo "start-fake-as.sh: unknown arg $arg" >&2; exit 64 ;;
  esac
done

if [ ${#NODE_SERIALS[@]} -eq 0 ]; then
  NODE_SERIALS=("SNX00001")
fi
# The first serial is the Controller's own host: it owns the redirect URIs and
# the default client_id spelling.
NODE_SERIAL="${NODE_SERIALS[0]}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Certificates come from the subset bundled inside this repository, so a
# standalone clone runs with no wider workspace. SNX00000 is the reserved
# infrastructure serial: the registry and this Authorization Server both
# present it. Resolution order is IPMX_CERT_ROOT, then this checkout, then the
# workspace tree one level up -- the last step keeps the IPMX security test
# suite working, and warns when it is taken.
CERT_PROBE="pem/ExampleDeviceServer.ABC.SNX00000.chain.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
elif [ -f "$SCRIPT_DIR/../Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
  echo "$(basename "$0"): $CERT_PROBE is not in this checkout — using the" \
       "workspace PKI at $CERT_ROOT" >&2
else
  echo "$(basename "$0"): missing build.0/$CERT_PROBE" >&2
  echo "  Searched $SCRIPT_DIR/Certificates and $SCRIPT_DIR/../Certificates." >&2
  echo "  Set IPMX_CERT_ROOT to a Certificates/ tree that carries it." >&2
  exit 66
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
# of nmos-reference alone can run the tutorial.
FAKE_AS=""
for candidate in \
  "$SCRIPT_DIR/fake-as/ipmx_fake_as.py"
do
  if [ -f "$candidate" ]; then FAKE_AS="$candidate"; break; fi
done
if [ -z "$FAKE_AS" ]; then
  echo "start-fake-as.sh: no Authorization Server found." >&2
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

AUD_ARGS=()
for serial in "${NODE_SERIALS[@]}"; do
  AUD_ARGS+=(--default-aud "XYZ-${serial}")
done

# One audience is what the vendored server was built for. More than one goes
# through multi_aud_as.py, which rebinds mint_token so each token carries the
# whole list: ipmx_fake_as.py takes --default-aud as a single string, and
# fake-as/ stays byte-identical to the validator's copy rather than growing a
# local edit.
AS_ENTRY="$FAKE_AS"
if [ ${#NODE_SERIALS[@]} -gt 1 ]; then
  AS_ENTRY="$SCRIPT_DIR/multi_aud_as.py"
  if [ ! -f "$AS_ENTRY" ]; then
    echo "start-fake-as.sh: missing $AS_ENTRY" >&2
    exit 66
  fi
fi

echo "Authorization Server (test)   https://XYZ-SNX00000:${AS_PORT}/realms/TR-10-SEC"
echo "  metadata   /.well-known/oauth-authorization-server/realms/TR-10-SEC"
echo "  sign in as ${OPERATOR} / ${PASSWORD} (${OPERATOR_ACCESS})"
echo "  client     ${CLIENT_ID}"
echo "  tokens aud $(printf 'XYZ-%s ' "${NODE_SERIALS[@]}")"
echo

exec python3 "$AS_ENTRY" \
  --host XYZ-SNX00000 \
  --port "$AS_PORT" \
  --cert "$AS_CERT" \
  --key  "$AS_KEY" \
  --api-selector realms/TR-10-SEC \
  "${AUD_ARGS[@]}" \
  --client-id "$CLIENT_ID" \
  --client-secret "$CLIENT_SECRET" \
  "${REDIRECT_ARGS[@]}" \
  --operator-username "$OPERATOR" \
  --operator-password "$PASSWORD" \
  --operator-access   "$OPERATOR_ACCESS"
