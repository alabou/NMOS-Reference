#!/usr/bin/env bash
# Configuration B (OAuth 2.0 with server TLS) — TR-10-SEC §12.3 RAAM=1.
#
# IPMX security validator launch contract:
#   start-node1-nomtls.sh <as-host> <as-port> [<rds-host> <rds-port>] \
#                         [--nap=N] [--rap=R] [--oaim=O] [--tct=T]
#
# Positional args:
#   $1 = OAuth 2.0 authorization server host (default: XYZ-SNX00000)
#   $2 = OAuth 2.0 authorization server port (default: 9443)
#   $3 = Registry host (default: 127.0.0.1)
#   $4 = Registry registration port (default: 8444; query port = $4-1)
#
# Named args (the validator drives the configuration matrix via these):
#   --nap=N    Node Access Policy. Config B pins NAP=2 (Restricted RW)
#              per §9.2; any other value is rejected.
#   --rap=R    Registry Access Policy: 0=HTTP, 1=server-TLS, 2=mTLS.
#   --oaim=O   OAuth2 Audience ID Mode: 0=serial, 1=cert, 2=either.
#   --tct=T    TLS Cert Type: 0=RSA, 1=ECDSA, 2=both (dual-stack).
#   --split-controls
#              Split IS-05/IS-08/IS-11 control APIs onto a separate
#              TLS listener (port 7052) with its own trust store
#              (CESTCA). Node IS-04 stays on port 7051 with NESTCA.
#              Lets the validator wire-test the §12.10/§12.12
#              NESTCA-vs-CESTCA distinction.
#
# Manual invocation with no args keeps the previous defaults
# (Keycloak at XYZ-SNX00000:9443, in-process registry, NAP=2 RAP=0
# OAIM=0 TCT=0 — the validator's baseline).

set -e

AS_HOST="${1:-XYZ-SNX00000}"
AS_PORT="${2:-9443}"
RDS_HOST="${3:-127.0.0.1}"
RDS_REG_PORT="${4:-8444}"
RDS_QUERY_PORT=$((RDS_REG_PORT - 1))
shift $(( $# < 4 ? $# : 4 ))

NAP=2
RAP=0
OAIM=0
TCT=0
SPLIT_CONTROLS=0
for arg in "$@"; do
  case "$arg" in
    --nap=*)  NAP="${arg#*=}" ;;
    --rap=*)  RAP="${arg#*=}" ;;
    --oaim=*) OAIM="${arg#*=}" ;;
    --tct=*)  TCT="${arg#*=}" ;;
    --split-controls) SPLIT_CONTROLS=1 ;;
    *) echo "start-node1-nomtls.sh: unknown arg $arg" >&2; exit 64 ;;
  esac
done

if [ "$NAP" != "2" ]; then
  echo "start-node1-nomtls.sh: Config B (RAAM=1) pins NAP=2; got --nap=$NAP" >&2
  exit 64
fi

# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: sibling of this script's
# parent (i.e. <workspace>/Certificates).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CERT_ROOT="${IPMX_CERT_ROOT:-$SCRIPT_DIR/../Certificates}"
CERTS="$CERT_ROOT/build.0"

# The Node's server cert flips between RSA and ECDSA per TCT, BUT
# all OTHER trust anchors (the OAuth AS, the registry, the incoming
# mTLS client cert) are always validated against the RSA root —
# those peers don't change based on which flavour the Node serves.
# The right shape for ``--trustedRootCA`` is therefore a bundle
# concatenating BOTH roots so chain validation succeeds whichever
# side is being checked.
CA_BUNDLE="/tmp/ExampleRootCA-bundle.pem"
cat "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem" > "$CA_BUNDLE"
CA="$CA_BUNDLE"

case "$TCT" in
  0|2) NODE_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00001.chain.pem"
       NODE_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00001.key" ;;
  1)   NODE_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00001.chain.ec.pem"
       NODE_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00001.ec.key" ;;
  *)   echo "start-node1-nomtls.sh: unsupported --tct=$TCT" >&2; exit 64 ;;
esac

case "$OAIM" in
  0) OAIM_FLAG="serial" ;;
  1) OAIM_FLAG="cert" ;;
  2) OAIM_FLAG="either" ;;
  *) echo "start-node1-nomtls.sh: unsupported --oaim=$OAIM" >&2; exit 64 ;;
esac

case "$RAP" in
  0) RDS_FLAGS=(--rdsDisableTLS) ;;
  1) RDS_FLAGS=() ;;
  2) RDS_FLAGS=(
       --rdsClientCertificate "$CERTS/pem/ExampleDeviceClient.ABC.SNX00001.chain.pem"
       --rdsClientKey         "$CERTS/key/ExampleDeviceClient.ABC.SNX00001.key"
     ) ;;
  *) echo "start-node1-nomtls.sh: unsupported --rap=$RAP" >&2; exit 64 ;;
esac

exec python3 nmos_node.py \
  --nodeSerialNumber SNX00001 \
  --nodeAddr XYZ-SNX00001 \
  --nodePort 7051 \
  --nodeCertificate "$NODE_CERT" \
  --nodeKey         "$NODE_KEY" \
  --nodeControlPort 5050 \
  --controllerAdminPassword admin \
  --oauth2 \
  --oauth2Host "${AS_HOST}" \
  --oauth2Port "${AS_PORT}" \
  --oauth2TrustedRootCA "$CA" \
  --oauth2ClientSecret secret \
  --oauth2ApiSelector realms/TR-10-SEC \
  --oauth2AudienceMode "${OAIM_FLAG}" \
  --rdsHost "${RDS_HOST}" \
  --rdsRegistrationPort "${RDS_REG_PORT}" \
  --rdsQueryPort        "${RDS_QUERY_PORT}" \
  "${RDS_FLAGS[@]}" \
  --trustedRootCA "$CA" \
  --debug-in-depth
