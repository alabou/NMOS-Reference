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

# Positional arguments are consumed only while they do not look like an option.
# Taking them by index instead meant `start-node3.sh --rap=2` landed in the first
# positional and was then shifted away: the flag looked accepted and changed
# nothing, so a rig meant to be RAP=2 ran as RAP=0 without a word.
POSITIONAL=()
while [ $# -gt 0 ] && [ "${#POSITIONAL[@]}" -lt 4 ]; do
  case "$1" in
    --*) break ;;
    *)   POSITIONAL+=("$1"); shift ;;
  esac
done

AS_HOST="${POSITIONAL[0]:-XYZ-SNX00000}"
AS_PORT="${POSITIONAL[1]:-9443}"
RDS_HOST="${POSITIONAL[2]:-127.0.0.1}"
RDS_REG_PORT="${POSITIONAL[3]:-8444}"

# Ports arrive on the command line, and arithmetic is no defence: $(( )) treats
# a bare name as a variable and re-evaluates its VALUE as an expression, so a
# non-numeric port becomes 0 and a derived port -1 -- which argparse then
# accepts as a perfectly good int, leaving the failure to surface much later as
# a bind error with nothing pointing back here. Check the value itself, with a
# minimum that leaves room for the ports derived from it.
require_port() {
  case "$2" in
    ''|*[!0-9]*)
      echo "$(basename "$0"): $1 must be a whole number, got '$2'" >&2
      exit 64 ;;
  esac
  if [ "$2" -lt "$3" ] || [ "$2" -gt "$4" ]; then
    echo "$(basename "$0"): $1 must be between $3 and $4, got '$2'" >&2
    exit 64
  fi
}

# The query port is derived as RDS_REG_PORT-1 at the exec below.
require_port "<rds-registration-port>" "$RDS_REG_PORT" 2 65535
# Ports arrive on the command line, and arithmetic is no defence: $(( )) treats
# a bare name as a variable and re-evaluates its VALUE as an expression, so a
# non-numeric port becomes 0 and a derived port -1 -- which argparse then
# accepts as a perfectly good int, leaving the failure to surface much later as
# a bind error with nothing pointing back here. Check the value itself, with a
# minimum that leaves room for the ports derived from it.
require_port() {
  case "$2" in
    ''|*[!0-9]*)
      echo "$(basename "$0"): $1 must be a whole number, got '$2'" >&2
      exit 64 ;;
  esac
  if [ "$2" -lt "$3" ] || [ "$2" -gt "$4" ]; then
    echo "$(basename "$0"): $1 must be between $3 and $4, got '$2'" >&2
    exit 64
  fi
}

if [ -n "${AS_PORT:-}" ]; then
  require_port "<as-port>" "$AS_PORT" 1 65535
fi
# The query port is one below this one, so 1 would leave nothing below it.
require_port "<rds-registration-port>" "$RDS_REG_PORT" 2 65535
RDS_QUERY_PORT=$((RDS_REG_PORT - 1))

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
# different `Certificates/` layout. Default: this repository's own
# Certificates/ tree.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Certificates come from the subset bundled inside this repository, so a
# standalone clone runs the whole rig with no wider workspace: SNX00000 is the
# infrastructure serial (registry + Authorization Server) and SNX00001..
# SNX00003 are the Nodes.
#
# Resolution order: IPMX_CERT_ROOT, then this checkout, then the workspace
# tree one level up. That last step is what lets the IPMX security test suite
# drive this launcher against a PKI carrying serials this repository does not
# ship, so it stays -- but it announces itself, because the silent version of
# it hid a missing serial through an entire 3-node bring-up. Matching nothing
# anywhere is a hard error naming every directory searched.
CERT_PROBE="pem/ExampleDeviceServer.ABC.SNX00001.chain.pem"
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

# The Node's server cert flips between RSA and ECDSA per TCT, BUT
# all OTHER trust anchors (the OAuth AS, the registry, the incoming
# mTLS client cert) are always validated against the RSA root —
# those peers don't change based on which flavour the Node serves.
# The right shape for ``--trustedRootCA`` is therefore a bundle
# concatenating BOTH roots so chain validation succeeds whichever
# side is being checked.
# One file holding both roots -- the RSA and the ECDSA generation of the same
# CA -- so either certificate flavour validates against a single
# --trustedRootCA. It ships in Certificates/ next to the two roots it is built
# from, rather than being written to a scratch path at every start-up.
CA="$CERTS/ExampleRootCA-bundle.pem"
if [ ! -f "$CA" ]; then
  # A PKI supplied from outside this checkout -- IPMX_CERT_ROOT, or the
  # workspace tree the IPMX security test suite drives these launchers with --
  # carries the two roots but not the combined file, so derive it from them.
  # mktemp rather than a fixed path: /tmp/ExampleRootCA-bundle.pem used to be
  # shared by every launcher and rewritten on each start-up.
  for root in "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem"; do
    if [ ! -f "$root" ]; then
      echo "$(basename "$0"): missing $root" >&2
      echo "  Set IPMX_CERT_ROOT to a Certificates/ tree that carries it." >&2
      exit 66
    fi
  done
  CA="$(mktemp -t ExampleRootCA-bundle.XXXXXX)"
  cat "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem" > "$CA"
fi

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
  --debug-in-depth  \
  --nodeConfig config10

