#!/usr/bin/env bash
# Configuration A (no transport security) — TR-10-SEC §12.3 RAAM=0.
#
# The Node API is plain HTTP on 127.0.0.1 (--nodeDisableTLS) and no OAuth 2.0
# is configured, so nothing here presents or validates a token. This is the
# launcher for working on Node behaviour — IS-04/05/11 resources, the
# Controller UI, streaming — with no PKI, no Authorization Server and no
# hosts-file entries in the way.
#
# The registry leg is the one thing that can still be secured: --rap selects
# how this Node reaches the registry, independently of its own listener.
#
# IPMX security validator launch contract:
#   start-node1-bare-no-usb.sh <as-host> <as-port> [<rds-host> <rds-port>] \
#                             [--nap=N] [--rap=R] [--oaim=O] [--tct=T]
#
# Positional args:
#   $1 = Authorization Server host — accepted and IGNORED (no --oauth2 here)
#   $2 = Authorization Server port — accepted and IGNORED, likewise
#   $3 = Registry host (default: 127.0.0.1)
#   $4 = Registry registration port (default: 8444; query port = $4-1)
#
# Named args:
#   --rap=R    Registry Access Policy: 0=HTTP (default), 1=server-TLS,
#              2=mTLS. The only flag with an effect in this launcher. 1 and 2
#              use the test PKI to verify the registry (and, for 2, to present
#              a client certificate); 0 needs no certificates at all.
#
#   --nap=N, --oaim=O, --tct=T, --split-controls
#              Accepted so the launch contract stays uniform across the
#              launchers, then IGNORED: every one of them configures Node-side
#              TLS or OAuth 2.0, and this launcher enables neither. A
#              non-default value warns on stderr rather than passing
#              unnoticed. For the postures they describe, use start-node1.sh
#              (Configuration C, mTLS + OAuth 2.0) or start-node1-nomtls.sh
#              (Configuration B, server TLS + OAuth 2.0).
#
# Manual invocation with no args: Node on 127.0.0.1:7051 with the Controller
# UI on :5050, registering over plain HTTP to 127.0.0.1:8444.

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
    *) echo "$(basename "$0"): unknown arg $arg" >&2; exit 64 ;;
  esac
done

# Accepted-and-ignored is fine; accepted-and-silently-ignored is not. These
# flags select Node-side TLS and OAuth 2.0 behaviour, and this launcher runs
# with --nodeDisableTLS and no --oauth2, so there is nothing for them to act
# on. Say so, so a run configured by mistake does not read as a passing one.
IGNORED=()
[ "$NAP" != "0" ] && IGNORED+=("--nap=$NAP")
[ "$OAIM" != "0" ] && IGNORED+=("--oaim=$OAIM")
[ "$TCT" != "0" ] && IGNORED+=("--tct=$TCT")
[ "$SPLIT_CONTROLS" != "0" ] && IGNORED+=("--split-controls")
if [ ${#IGNORED[@]} -gt 0 ]; then
  echo "$(basename "$0"): ignoring ${IGNORED[*]} — this launcher has no" \
       "Node-side TLS or OAuth 2.0 to configure. Use start-node1.sh" \
       "(Config C) or start-node1-nomtls.sh (Config B) instead." >&2
fi

# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: this repository's own
# Certificates/ tree.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Resolution order: IPMX_CERT_ROOT, then this checkout, then the workspace
# tree one level up (which is how the IPMX security test suite supplies a PKI
# with serials this repository does not ship).
CERT_PROBE="pem/ExampleDeviceClient.ABC.SNX00001.chain.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
elif [ -f "$SCRIPT_DIR/../Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
else
  CERT_ROOT="$SCRIPT_DIR/Certificates"
fi
CERTS="$CERT_ROOT/build.0"

# Nothing is verified at this point on purpose, and matching no tree above is
# not fatal here: this launcher runs the Node with --nodeDisableTLS, so the
# default RAP=0 touches no certificate at all. Only the RAP=1 and RAP=2
# branches need files, and each checks exactly what it passes.
CA="$CERTS/ExampleRootCA-bundle.pem"
RDS_CLIENT_CERT="$CERTS/pem/ExampleDeviceClient.ABC.SNX00001.chain.pem"
RDS_CLIENT_KEY="$CERTS/key/ExampleDeviceClient.ABC.SNX00001.key"

require_files() {
  for required in "$@"; do
    if [ ! -f "$required" ]; then
      echo "$(basename "$0"): missing $required" >&2
      echo "  Set IPMX_CERT_ROOT to a Certificates/ tree that carries it." >&2
      exit 66
    fi
  done
}

# Same reasoning as the other launchers: an outside PKI has the two roots but
# not the combined file. Skipped for RAP=0, which needs no certificate at all.
if [ ! -f "$CA" ] && [ "$RAP" != "0" ]; then
  require_files "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem"
  CA="$(mktemp -t ExampleRootCA-bundle.XXXXXX)"
  cat "$CERTS/ExampleRootCA.pem" "$CERTS/ExampleRootCA.ec.pem" > "$CA"
fi

# RAP=1 and RAP=2 both make the Node verify the registry's certificate, so both
# need the trust anchor. With neither --rdsTrustedRootCA nor --trustedRootCA
# given, build_registry_ssl_context() in nmos_node.py falls back to
# load_default_certs(), and the system store does not contain this test PKI's
# private root -- every registration attempt would fail verification.
case "$RAP" in
  0) RDS_FLAGS=(--rdsDisableTLS) ;;
  1) require_files "$CA"
     RDS_FLAGS=(--rdsTrustedRootCA "$CA") ;;
  2) require_files "$CA" "$RDS_CLIENT_CERT" "$RDS_CLIENT_KEY"
     RDS_FLAGS=(
       --rdsTrustedRootCA     "$CA"
       --rdsClientCertificate "$RDS_CLIENT_CERT"
       --rdsClientKey         "$RDS_CLIENT_KEY"
     ) ;;
  *) echo "$(basename "$0"): unsupported --rap=$RAP" >&2; exit 64 ;;
esac

exec python3 nmos_node.py \
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

