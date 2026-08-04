#!/usr/bin/env bash
# Configuration C (mTLS + OAuth 2.0) — TR-10-SEC §12.3 RAAM=2. Second node.
#
# The peer of start-node1.sh: same argument contract, same policy matrix, same
# flag surface, with SNX00002 substituted throughout. The two differ only in
# serial, port and node configuration — so a two-node rig exercises the same
# security posture at both ends of a cross-node route, rather than a secured
# node talking to a simplified one.
#
# IPMX security validator launch contract:
#   start-node2.sh <as-host> <as-port> [<rds-host> <rds-port>] \
#                  [--nap=N] [--rap=R] [--oaim=O] [--tct=T]
#
# Positional args:
#   $1 = OAuth 2.0 authorization server host (default: XYZ-SNX00000)
#   $2 = OAuth 2.0 authorization server port (default: 9443)
#   $3 = Registry host (default: 127.0.0.1)
#   $4 = Registry registration port (default: 8444; query port = $4-1)
#
# Named args:
#   --nap=N    Node Access Policy. Config C pins NAP=2 per §9.2.
#   --rap=R    Registry Access Policy: 0=HTTP, 1=server-TLS, 2=mTLS.
#   --oaim=O   OAuth2 Audience ID Mode: 0=serial, 1=cert, 2=either.
#   --tct=T    TLS Cert Type: 0=RSA, 1=ECDSA, 2=both (dual-stack TODO).
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
#
# On tokens: an Authorization Server scopes a token to the devices named in
# its `aud` claim, so a token minted for SNX00001 alone is rejected here — and
# the Controller reports that rather than failing at the first click. That is
# what `tutorial-security` demonstrates, and it needs both nodes running.

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
for arg in "$@"; do
  case "$arg" in
    --nap=*)  NAP="${arg#*=}" ;;
    --rap=*)  RAP="${arg#*=}" ;;
    --oaim=*) OAIM="${arg#*=}" ;;
    --tct=*)  TCT="${arg#*=}" ;;
    *) echo "start-node2.sh: unknown arg $arg" >&2; exit 64 ;;
  esac
done

if [ "$NAP" != "2" ]; then
  echo "start-node2.sh: Config C (RAAM=2) pins NAP=2; got --nap=$NAP" >&2
  exit 64
fi

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
CERT_PROBE="pem/ExampleDeviceServer.ABC.SNX00002.chain.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
else
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
fi
CERTS="$CERT_ROOT/build.0"

CA_BUNDLE="/tmp/ExampleRootCA-bundle.pem"
cat "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem" > "$CA_BUNDLE"
CA="$CA_BUNDLE"

case "$TCT" in
  0|2) NODE_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00002.chain.pem"
       NODE_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00002.key" ;;
  1)   NODE_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00002.chain.ec.pem"
       NODE_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00002.ec.key" ;;
  *)   echo "start-node2.sh: unsupported --tct=$TCT" >&2; exit 64 ;;
esac

case "$OAIM" in
  0) OAIM_FLAG="serial" ;;
  1) OAIM_FLAG="cert" ;;
  2) OAIM_FLAG="either" ;;
  *) echo "start-node2.sh: unsupported --oaim=$OAIM" >&2; exit 64 ;;
esac

case "$RAP" in
  0) RDS_FLAGS=(--rdsDisableTLS) ;;
  1) RDS_FLAGS=() ;;
  2) RDS_FLAGS=(
       --rdsClientCertificate "$CERTS/pem/ExampleDeviceClient.ABC.SNX00002.chain.pem"
       --rdsClientKey         "$CERTS/key/ExampleDeviceClient.ABC.SNX00002.key"
     ) ;;
  *) echo "start-node2.sh: unsupported --rap=$RAP" >&2; exit 64 ;;
esac

# --nodeControlPort is deliberately absent, unlike node1. The Controller is a
# single-instance affordance in this rig: node1 serves it on 5050 and shows
# every node it discovers through the registry, this one included. A second
# Controller would work but would present the same devices twice and give the
# tutorials two URLs to explain. Add --nodeControlPort 5060 and
# --controllerAdminPassword here if you want one.
exec python3 nmos_node.py \
  --nodeSerialNumber SNX00002 \
  --nodeAddr XYZ-SNX00002 \
  --nodePort 7052 \
  --nodeCertificate "$NODE_CERT" \
  --nodeKey         "$NODE_KEY" \
  --nodeTrustedRootCA "$CA" \
  --nodeClientCertificate "$CERTS/pem/ExampleDeviceClient.ABC.SNX00002.chain.pem" \
  --nodeClientKey         "$CERTS/key/ExampleDeviceClient.ABC.SNX00002.key" \
  --oauth2 \
  --oauth2Host "${AS_HOST}" \
  --oauth2Port "${AS_PORT}" \
  --oauth2TrustedRootCA "$CA" \
  --oauth2ClientSecret secret \
  --oauth2ApiSelector realms/TR-10-SEC \
  --oauth2AudienceMode "${OAIM_FLAG}" \
  --oauth2ClientId Example.Company.Device.Client.ABC.SNX00002.example.com \
  --rdsHost "${RDS_HOST}" \
  --rdsRegistrationPort "${RDS_REG_PORT}" \
  --rdsQueryPort        "${RDS_QUERY_PORT}" \
  "${RDS_FLAGS[@]}" \
  --trustedRootCA "$CA" \
  --debug-in-depth \
  --nodeConfig config_av_usb_tb_A
