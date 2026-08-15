#!/usr/bin/env bash
# One member of a SECURED distributed NMOS registry: TLS (optionally mutual) on
# the Registration and Query interfaces, mutual TLS to and between the etcd
# members holding the shared state.
#
# Usage:
#   start-registry-dist-secure.sh <index> [members] [rap] [--oauth2]
#                                 [--as-host=H] [--as-port=P] [--tct=T] [--nap=N]
#
#   <index>   Which member this is, 0..members-1.
#   [members] Cluster size: 1, 3 or 5 (default 3).
#   [rap]     Registry Access Policy for the Registration API (default 1)
#               1  Unrestricted Registration, server-authenticated TLS
#               2  Restricted Registration, mutual TLS
#             RAP=0 (plain HTTP) is start-registry-dist.sh, the unsecured rig.
#
# The option vocabulary is start-registry.sh's, deliberately: --oauth2,
# --as-host, --as-port, --tct and --nap mean exactly what they mean there, so
# one rig has one vocabulary whether or not the registry is distributed.
#
#   Bring the cluster up FIRST, secured, then start one registry per member:
#
#     ./start-etcd-cluster.sh 3 --secure
#     ./start-registry-dist-secure.sh 0 3 2 --oauth2      # window 1
#     ./start-registry-dist-secure.sh 1 3 2 --oauth2      # window 2
#     ./start-registry-dist-secure.sh 2 3 2 --oauth2      # window 3
#
# One certificate per member, from Certificates/build.0.etcd/, serves FIVE
# roles: this registry's Registration listener, its Query listener, its etcd
# member's client listener, that member's peer listener and outbound peer
# connections, and this registry's own client channel to etcd. That is what the
# dual serverAuth, clientAuth EKU is for, and why the certificate carries both
# the ordinary device-server SANs (XYZ-SNX1000n) and the shared etcd SAN.
#
# HOSTS FILE. Every member name must resolve to 127.0.0.1:
#
#     127.0.0.1   XYZ-SNX10000    # etcd member 0 + registry member 0
#     127.0.0.1   XYZ-SNX10001    # etcd member 1 + registry member 1
#     127.0.0.1   XYZ-SNX10002    # etcd member 2 + registry member 2
#
# Members co-located on one machine must share its address and separate by
# port, because etcd verifies the certificate a peer presents against the
# address that peer's connection arrives from -- and every connection between
# loopback addresses is sourced from 127.0.0.1 whatever its destination. Give
# the members separate addresses and no peer handshake succeeds. Deployed on
# separate machines the same configuration needs no change: each name resolves
# to its own host, which is also where its peer traffic originates.

set -Eeuo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Positionals stop at the first option, so `... 0 3 --oauth2` cannot silently
# land --oauth2 in the RAP slot. Same guard, same reason, as start-registry.sh.
POSITIONAL=()
while [ $# -gt 0 ] && [ "${#POSITIONAL[@]}" -lt 3 ]; do
  case "$1" in
    --*) break ;;
    *)   POSITIONAL+=("$1"); shift ;;
  esac
done

INDEX="${POSITIONAL[0]:-}"
MEMBERS="${POSITIONAL[1]:-3}"
RAP="${POSITIONAL[2]:-1}"

AS_HOST=XYZ-SNX00000
AS_PORT=9443
TCT=0
NAP=2
USE_OAUTH2=0

for arg in "$@"; do
  case "$arg" in
    --oauth2)    USE_OAUTH2=1 ;;
    --as-host=*) AS_HOST="${arg#*=}" ;;
    --as-port=*) AS_PORT="${arg#*=}" ;;
    --tct=*)     TCT="${arg#*=}" ;;
    --nap=*)     NAP="${arg#*=}" ;;
    *) echo "$(basename "$0"): unknown arg $arg" >&2; exit 64 ;;
  esac
done

if ! [[ "$INDEX" =~ ^[0-9]+$ ]]; then
  echo "$(basename "$0"): first argument must be the member index" >&2
  exit 64
fi
case "$MEMBERS" in
  1|3|5) ;;
  *) echo "$(basename "$0"): members must be 1, 3 or 5" >&2; exit 64 ;;
esac
if [ "$INDEX" -ge "$MEMBERS" ]; then
  echo "$(basename "$0"): member index must be 0..$((MEMBERS - 1))" >&2
  exit 64
fi

PYTHON="./.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

# --- certificates ----------------------------------------------------------
#
# Same resolution order as start-registry.sh: IPMX_CERT_ROOT, this checkout,
# then the workspace tree one level up, announcing the fallback rather than
# taking it silently.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CERT_PROBE="build.0.etcd/pem/ExampleDeviceServer.ABC.SNX10000.etcd.chain.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
elif [ -f "$SCRIPT_DIR/../Certificates/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
  echo "$(basename "$0"): $CERT_PROBE is not in this checkout — using the" \
       "workspace PKI at $CERT_ROOT" >&2
else
  echo "$(basename "$0"): missing $CERT_PROBE" >&2
  echo "  Searched $SCRIPT_DIR/Certificates and $SCRIPT_DIR/../Certificates." >&2
  echo "  Set IPMX_CERT_ROOT to a Certificates/ tree that carries it." >&2
  exit 66
fi

SERIAL="SNX1000${INDEX}"
ETCD_PEM="$CERT_ROOT/build.0.etcd/pem"
ETCD_KEY="$CERT_ROOT/build.0.etcd/key"
case "$TCT" in
  0) CERT="$ETCD_PEM/ExampleDeviceServer.ABC.$SERIAL.etcd.chain.pem"
     KEY="$ETCD_KEY/ExampleDeviceServer.ABC.$SERIAL.etcd.key" ;;
  1) CERT="$ETCD_PEM/ExampleDeviceServer.ABC.$SERIAL.etcd.ec.chain.pem"
     KEY="$ETCD_KEY/ExampleDeviceServer.ABC.$SERIAL.etcd.ec.key" ;;
  *) echo "$(basename "$0"): unsupported --tct=$TCT" >&2; exit 64 ;;
esac
for path in "$CERT" "$KEY"; do
  [ -f "$path" ] || { echo "$(basename "$0"): missing $path" >&2; exit 66; }
done

# One file holding both generations of the root CA, so either certificate
# flavour validates against a single anchor. Passing the two roots separately
# would work too -- the supervisor combines them for etcd, which takes one
# --trusted-ca-file -- but one file keeps what etcd receives identical to what
# is on disk.
CA="$CERT_ROOT/build.0/ExampleRootCA-bundle.pem"
if [ ! -f "$CA" ]; then
  for root in "$CERT_ROOT/build.0/ExampleRootCA.pem" \
              "$CERT_ROOT/build.0/ExampleRootCA.ec.pem"; do
    [ -f "$root" ] || { echo "$(basename "$0"): missing $root" >&2; exit 66; }
  done
  CA="$(mktemp -t ExampleRootCA-bundle.XXXXXX)"
  cat "$CERT_ROOT/build.0/ExampleRootCA.pem" \
      "$CERT_ROOT/build.0/ExampleRootCA.ec.pem" > "$CA"
fi

# --- access policies -------------------------------------------------------
#
# Identical to start-registry.sh: the Registration trust anchor is what selects
# RAP 1 from RAP 2, and the Query anchor plus --queryOptionalClientAuth select
# NAP 1 from NAP 2.
case "$RAP" in
  1) REG_CA_FLAGS=() ;;
  2) REG_CA_FLAGS=(--registrationTrustedRootCA "$CA") ;;
  0) echo "$(basename "$0"): RAP=0 (plain HTTP) is start-registry-dist.sh" >&2
     exit 64 ;;
  *) echo "$(basename "$0"): unsupported RAP=$RAP" >&2; exit 64 ;;
esac

case "$NAP" in
  1) QUERY_CA_FLAGS=(--queryTrustedRootCA "$CA" --queryOptionalClientAuth) ;;
  2) QUERY_CA_FLAGS=(--queryTrustedRootCA "$CA") ;;
  0) echo "$(basename "$0"): NAP=0 (plain HTTP) is start-registry-dist.sh" >&2
     exit 64 ;;
  *) echo "$(basename "$0"): unsupported --nap=$NAP" >&2; exit 64 ;;
esac

# TR-10-SEC §"Unrestricted Read Only": read access MUST be granted by the
# OAuth 2.0 authorizations, so NAP=1 cannot be claimed alongside --oauth2.
if [ "$NAP" = "1" ] && [ "$USE_OAUTH2" = "1" ]; then
  echo "$(basename "$0"): --nap=1 (Unrestricted Read Only) is not allowed" >&2
  echo "  with --oauth2; the specification requires read access to be granted" >&2
  echo "  by the OAuth 2.0 authorizations. Use --nap=2, or drop --oauth2." >&2
  exit 64
fi

if [ "$USE_OAUTH2" = "1" ]; then
  OAUTH2_FLAGS=(
    --oauth2
    --oauth2Host "$AS_HOST"
    --oauth2Port "$AS_PORT"
    --oauth2TrustedRootCA "$CA"
    --oauth2ApiSelector realms/TR-10-SEC
  )
else
  OAUTH2_FLAGS=()
fi

# --- topology --------------------------------------------------------------
#
# The member list is derived here rather than hard-coded so it cannot drift
# from the cluster etcd_cluster.py actually forms: same port block per member,
# same names. Members share one address and differ by port -- see the HOSTS
# FILE note above for why that is not a shortcut but a requirement.
ETCD_CLIENT_PORT=$((2381 + INDEX * 10))
MEMBER_FLAGS=(--registryAdvertisedHost "XYZ-SNX1000${INDEX}:${ETCD_CLIENT_PORT}")
for peer in $(seq 0 $((MEMBERS - 1))); do
  [ "$peer" = "$INDEX" ] && continue
  MEMBER_FLAGS+=(--registryNeighbour "XYZ-SNX1000${peer}:$((2381 + peer * 10))")
done

ENDPOINTS="$("$PYTHON" etcd_cluster.py --members "$MEMBERS" --secure endpoints)"

# One port block of 10 per member, matching start-registry-dist.sh so the two
# rigs never collide when both are on a developer's machine.
REG_PORT=$((8444 + INDEX * 10))
QUERY_PORT=$((8443 + INDEX * 10))
WS_PORT=$((8448 + INDEX * 10))

echo "Secured registry member $INDEX of $MEMBERS  (RAP=$RAP NAP=$NAP OAuth2=$USE_OAUTH2)"
echo "  Registration : https://XYZ-SNX1000${INDEX}:${REG_PORT}/x-nmos/registration/v1.3/"
echo "  Query        : https://XYZ-SNX1000${INDEX}:${QUERY_PORT}/x-nmos/query/v1.3/"
echo "  Identity     : $SERIAL"
echo "  etcd         : ${ENDPOINTS}  (mutual TLS)"
echo

exec "$PYTHON" nmos_registry.py \
  --registryAddr 127.0.0.1 \
  --registrySerialNumber "$SERIAL" \
  --registryCertificate "$CERT" \
  --registryKey         "$KEY" \
  --registrationPort    "$REG_PORT" \
  --queryPort           "$QUERY_PORT" \
  --queryWebSocketPort  "$WS_PORT" \
  "${REG_CA_FLAGS[@]}" \
  "${QUERY_CA_FLAGS[@]}" \
  "${OAUTH2_FLAGS[@]}" \
  --trustedRootCA "$CA" \
  --distributed \
  --etcdExternal \
  "${MEMBER_FLAGS[@]}" \
  --etcdEndpoints "$ENDPOINTS" \
  --etcdCertificate "$CERT" \
  --etcdKey "$KEY" \
  --etcdTrustedRootCA "$CA" \
  --logFile "nmos-registry-${INDEX}.log"
