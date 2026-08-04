#!/usr/bin/env bash
# Configuration A (mTLS without OAuth 2.0) — TR-10-SEC §12.3 RAAM=0.
#
# IPMX security validator launch contract:
#   start-node1-noauth2.sh <as-host> <as-port> [<rds-host> <rds-port>] \
#                          [--nap=N] [--rap=R] [--tct=T]
#
# Positional args:
#   $1 = AS host  (accepted but ignored — mTLS-only never contacts an AS)
#   $2 = AS port  (accepted but ignored)
#   $3 = Registry host (default: 127.0.0.1)
#   $4 = Registry registration port (default: 8444; query port = $4-1)
#
# Named args:
#   --nap=N    Node Access Policy: 1 (Unrestricted RO, mTLS only via
#              --nodeOptionalClientAuth) or 2 (Restricted RW, default).
#   --rap=R    Registry Access Policy: 0=HTTP, 1=server-TLS, 2=mTLS.
#   --tct=T    TLS Cert Type: 0=RSA, 1=ECDSA, 2=both (dual-stack TODO).
#   --split-controls
#              Split IS-05/IS-08/IS-11 onto a SEPARATE TLS listener
#              (port 7052) with its OWN trust store (CESTCA). The Node
#              IS-04 API stays on port 7051 with NESTCA. Used to wire-
#              test the TR-10-SEC §12.10/§12.12 role separation —
#              clients presenting NESTCA-rooted certs are accepted at
#              the Node API but REFUSED at the control listener, and
#              vice versa.
#
#              Trust-root mapping:
#                NESTCA = build.1/ExampleRootCA.pem (Node API listener)
#                CESTCA = build.2/ExampleRootCA.pem (Control listener)
#                CTCA   = build.0/ExampleRootCA.pem (outgoing — registry,
#                         and (in Config B/C) the OAuth AS)
#
# --oaim is forbidden under Config A (no OAuth2).
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

: "${1:-}"; : "${2:-}"
RDS_HOST="${3:-127.0.0.1}"
RDS_REG_PORT="${4:-8444}"
RDS_QUERY_PORT=$((RDS_REG_PORT - 1))
shift $(( $# < 4 ? $# : 4 ))

NAP=2
RAP=0
TCT=0
SPLIT_CONTROLS=0
GCRL=""
for arg in "$@"; do
  case "$arg" in
    --nap=*)  NAP="${arg#*=}" ;;
    --rap=*)  RAP="${arg#*=}" ;;
    --tct=*)  TCT="${arg#*=}" ;;
    --split-controls) SPLIT_CONTROLS=1 ;;
    --gcrl=*) GCRL="${arg#*=}" ;;
    --oaim=*) echo "start-node1-noauth2.sh: --oaim is forbidden with Config A (no OAuth2)" >&2; exit 64 ;;
    *) echo "start-node1-noauth2.sh: unknown arg $arg" >&2; exit 64 ;;
  esac
done

# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: sibling of this script's
# parent (i.e. <workspace>/Certificates).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Prefer the certificate subset bundled inside this repository, so a
# standalone clone of nmos-reference runs without the wider workspace PKI.
# That subset ships only the serials the quick-start and tutorials use
# (SNX00000 infrastructure, SNX00001, SNX00002); anything else falls back
# to the workspace-level Certificates/ tree. An explicit IPMX_CERT_ROOT
# always wins over both.
CERT_PROBE="pem/ExampleDeviceServer.ABC.SNX00001.chain.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
else
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
fi
CERTS="$CERT_ROOT/build.0"

# Global CA bundle for --trustedRootCA. Reference-node validates that
# every per-role trust root (e.g. --nodeTrustedRootCA) chains under
# this global bundle, so when --split-controls is set we must add the
# build.1 / build.2 roots here too — even though their *application
# role* is solely per-listener mTLS client validation. The bundle's
# membership at config-parse time is decoupled from how the device
# actually selects per-role trust at TLS-handshake time.
CA_BUNDLE="/tmp/ExampleRootCA-bundle.pem"
cat "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem" > "$CA_BUNDLE"
if [ -f "$CERT_ROOT/build.1/ExampleRootCA.pem" ]; then
  cat "$CERT_ROOT/build.1/ExampleRootCA.pem" >> "$CA_BUNDLE"
fi
if [ -f "$CERT_ROOT/build.2/ExampleRootCA.pem" ]; then
  cat "$CERT_ROOT/build.2/ExampleRootCA.pem" >> "$CA_BUNDLE"
fi
CA="$CA_BUNDLE"

case "$TCT" in
  0|2) NODE_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00001.chain.pem"
       NODE_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00001.key" ;;
  1)   NODE_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00001.chain.ec.pem"
       NODE_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00001.ec.key" ;;
  *)   echo "start-node1-noauth2.sh: unsupported --tct=$TCT" >&2; exit 64 ;;
esac

# NAP=1 (Unrestricted RO) sets the SSL context's verify_mode to
# CERT_OPTIONAL via --nodeOptionalClientAuth — middleware lets
# GET/HEAD/OPTIONS through without a peer cert but refuses state-
# changing methods unless one is presented. NAP=2 leaves the default
# (CERT_REQUIRED, full Restricted RW).
NAP_FLAGS=()
case "$NAP" in
  1) NAP_FLAGS=(--nodeOptionalClientAuth) ;;
  2) NAP_FLAGS=() ;;
  *) echo "start-node1-noauth2.sh: unsupported --nap=$NAP" >&2; exit 64 ;;
esac

case "$RAP" in
  0) RDS_FLAGS=(--rdsDisableTLS) ;;
  1) RDS_FLAGS=() ;;
  2) RDS_FLAGS=(
       --rdsClientCertificate "$CERTS/pem/ExampleDeviceClient.ABC.SNX00001.chain.pem"
       --rdsClientKey         "$CERTS/key/ExampleDeviceClient.ABC.SNX00001.key"
     ) ;;
  *) echo "start-node1-noauth2.sh: unsupported --rap=$RAP" >&2; exit 64 ;;
esac

# --split-controls: separate trust stores per listener.
# NESTCA = root used for incoming TLS client auth on Node IS-04 API.
# CESTCA = root used for incoming TLS client auth on IS-05/IS-08/IS-11.
# Without --split-controls, both listeners share --nodeTrustedRootCA.
SPLIT_FLAGS=()
NODE_TRUST_CA="$CA"
if [ "$SPLIT_CONTROLS" = "1" ]; then
  NESTCA="$CERT_ROOT/build.1/ExampleRootCA.pem"
  CESTCA="$CERT_ROOT/build.2/ExampleRootCA.pem"
  if [ ! -f "$NESTCA" ] || [ ! -f "$CESTCA" ]; then
    echo "start-node1-noauth2.sh: --split-controls needs build.1 + build.2 trust roots" >&2
    exit 64
  fi
  NODE_TRUST_CA="$NESTCA"
  SPLIT_FLAGS=(
    --controlTrustedRootCA "$CESTCA"
    --controlPort 7052
  )
fi

GCRL_FLAGS=()
if [ -n "$GCRL" ]; then
  GCRL_FLAGS=(--gcrl "$GCRL")
fi

exec python3 nmos_node.py \
  --nodeSerialNumber SNX00001 \
  --nodeAddr XYZ-SNX00001 \
  --nodePort 7051 \
  --nodeCertificate "$NODE_CERT" \
  --nodeKey         "$NODE_KEY" \
  --nodeTrustedRootCA "$NODE_TRUST_CA" \
  "${SPLIT_FLAGS[@]}" \
  --nodeControlPort 5050 \
  --controllerAdminPassword admin \
  "${NAP_FLAGS[@]}" \
  --rdsHost "${RDS_HOST}" \
  --rdsRegistrationPort "${RDS_REG_PORT}" \
  --rdsQueryPort        "${RDS_QUERY_PORT}" \
  "${RDS_FLAGS[@]}" \
  --trustedRootCA "$CA" \
  "${GCRL_FLAGS[@]}" \
  --debug-in-depth \
  --nodeConfig config10

  