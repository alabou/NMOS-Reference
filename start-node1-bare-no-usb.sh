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

NAP=0
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

# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: sibling of this script's
# parent (i.e. <workspace>/Certificates).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

case "$RAP" in
  0) RDS_FLAGS=(--rdsDisableTLS) ;;
  1) RDS_FLAGS=() ;;
  2) RDS_FLAGS=(
       --rdsClientCertificate "$CERTS/pem/ExampleDeviceClient.ABC.SNX00001.chain.pem"
       --rdsClientKey         "$CERTS/key/ExampleDeviceClient.ABC.SNX00001.key"
     ) ;;
  *) echo "start-node1-nomtls.sh: unsupported --rap=$RAP" >&2; exit 64 ;;
esac

exec python3.12 nmos_node.py \
  --nodeSerialNumber SNX00001 \
  --nodeAddr 127.0.0.1 \
  --nodePort 7051 \
  --nodeControlPort 5050 \
  --nodeDisableTLS \
  --controllerAdminPassword admin \
  --rdsHost "${RDS_HOST}" \
  --rdsRegistrationPort "${RDS_REG_PORT}" \
  --rdsQueryPort        "${RDS_QUERY_PORT}" \
  "${RDS_FLAGS[@]}" \
  --debug-in-depth  \
  --nodeConfig config10_nousb \
  --ipmx

