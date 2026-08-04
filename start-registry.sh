#!/usr/bin/env bash
# NMOS Registry — TLS, optional mTLS, optional OAuth 2.0 on the Query API.
#
# Usage:
#   start-registry.sh [rap] [registration-port] [--oauth2] [--as-host=H] [--as-port=P] [--tct=T]
#
#   $1 = Registry Access Policy for the Registration API (default: 1)
#          1  Unrestricted Registration, server-authenticated TLS
#          2  Restricted Registration, mutual TLS
#        (RAP=0, plain HTTP, is start-registry-bare.sh)
#   $2 = Registration API port (default: 8444; query = $2-1, ws = $2+4)
#
#   NMOS_REGISTRY_ADDR overrides the bind address (default 127.0.0.1).
#   Set it to 0.0.0.0 or a routable address when the registry has to be
#   reachable from another host -- notably when Nodes run on Windows via
#   start-node1-bare.bat, which points itself at the WSL IP
#   (`wsl.exe hostname -I`) and so cannot reach a loopback-only registry.
#
#   --oauth2        Require OAuth 2.0 on the Query API as well as TLS.
#   --as-host=H     Authorization server host (default: XYZ-SNX00000)
#   --as-port=P     Authorization server port (default: 9443)
#   --tct=T         TLS Certificate Type: 0=RSA (default), 1=ECDSA
#
# TR-10-SEC (specs/NMOS With Control Plane Security.md):
#
#   * §"NMOS Registry" (:105) -- the Registration API MUST NOT require OAuth
#     2.0, and MUST be secured with server or mutual TLS. There is therefore
#     no way to put OAuth 2.0 on Registration from this script, by design;
#     --oauth2 applies to the Query API alone.
#   * §"Registry Access Policy" -- RAP 1 and 2 are the two compliant modes,
#     and both "shall be supported by all compliant devices".
#
# Pair with a node launcher using a matching --rap:
#
#     ./start-registry.sh 2               # mTLS registration
#     ./start-node1.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2
#
# The registry host is given by name, not as 127.0.0.1: under RAP 1 and 2 the
# Node verifies the registry's certificate, which carries the DNS SAN
# XYZ-SNX00000 and no IP SAN, so an IP literal fails hostname verification.
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

RAP="${1:-1}"
REG_PORT="${2:-8444}"
shift $(( $# < 2 ? $# : 2 ))

QUERY_PORT=$((REG_PORT - 1))
WS_PORT=$((REG_PORT + 4))

AS_HOST="XYZ-SNX00000"
AS_PORT="9443"
TCT=0
USE_OAUTH2=0

for arg in "$@"; do
  case "$arg" in
    --oauth2)    USE_OAUTH2=1 ;;
    --as-host=*) AS_HOST="${arg#*=}" ;;
    --as-port=*) AS_PORT="${arg#*=}" ;;
    --tct=*)     TCT="${arg#*=}" ;;
    *) echo "start-registry.sh: unknown arg $arg" >&2; exit 64 ;;
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
CERT_PROBE="pem/ExampleDeviceServer.ABC.SNX00000.chain.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
else
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
fi
CERTS="$CERT_ROOT/build.0"

# The Node launchers build the same bundle, so both ends validate against one
# trust store containing the RSA and ECDSA roots.
CA_BUNDLE="/tmp/ExampleRootCA-bundle.pem"
cat "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem" > "$CA_BUNDLE"
CA="$CA_BUNDLE"

# SNX00000 is the reserved infrastructure serial in this PKI; the registry is
# infrastructure rather than a device, so it uses that identity.
case "$TCT" in
  0) REG_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00000.chain.pem"
     REG_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00000.key" ;;
  1) REG_CERT="$CERTS/pem/ExampleDeviceServer.ABC.SNX00000.chain.ec.pem"
     REG_KEY="$CERTS/key/ExampleDeviceServer.ABC.SNX00000.ec.key" ;;
  *) echo "start-registry.sh: unsupported --tct=$TCT" >&2; exit 64 ;;
esac

# The Registration trust anchor is what selects RAP 1 from RAP 2: with no
# anchor the listener asks for no client certificate; with one it requires a
# certificate that chains to it.
case "$RAP" in
  1) REG_CA_FLAGS=() ;;
  2) REG_CA_FLAGS=(--registrationTrustedRootCA "$CA") ;;
  0) echo "start-registry.sh: RAP=0 (plain HTTP) is start-registry-bare.sh" >&2
     exit 64 ;;
  *) echo "start-registry.sh: unsupported RAP=$RAP" >&2; exit 64 ;;
esac

# The Query API accepts client certificates in every TLS mode here, so a
# Controller may authenticate with mTLS, with OAuth 2.0, or with both.
QUERY_CA_FLAGS=(--queryTrustedRootCA "$CA")

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

exec python3 nmos_registry.py \
  --registryAddr "${NMOS_REGISTRY_ADDR:-127.0.0.1}" \
  --registrySerialNumber SNX00000 \
  --registryCertificate "$REG_CERT" \
  --registryKey         "$REG_KEY" \
  --registrationPort    "$REG_PORT" \
  --queryPort           "$QUERY_PORT" \
  --queryWebSocketPort  "$WS_PORT" \
  "${REG_CA_FLAGS[@]}" \
  "${QUERY_CA_FLAGS[@]}" \
  "${OAUTH2_FLAGS[@]}" \
  --trustedRootCA "$CA" \
  --logFile nmos-registry.log
