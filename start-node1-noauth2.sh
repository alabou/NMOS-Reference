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

# Positional arguments are consumed only while they do not look like an option.
# Taking them by index instead meant `start-node1-noauth2.sh --rap=2` landed in the first
# positional and was then shifted away: the flag looked accepted and changed
# nothing, so a rig meant to be RAP=2 ran as RAP=0 without a word.
POSITIONAL=()
while [ $# -gt 0 ] && [ "${#POSITIONAL[@]}" -lt 4 ]; do
  case "$1" in
    --*) break ;;
    *)   POSITIONAL+=("$1"); shift ;;
  esac
done

# $1 / $2 are the authorization-server host and port: accepted for launch
# contract symmetry and unused, because Config A contacts no AS.
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

# Global CA bundle for --trustedRootCA. Reference-node validates that
# every per-role trust root (e.g. --nodeTrustedRootCA) chains under
# this global bundle, so when --split-controls is set we must add the
# build.1 / build.2 roots here too — even though their *application
# role* is solely per-listener mTLS client validation. The bundle's
# membership at config-parse time is decoupled from how the device
# actually selects per-role trust at TLS-handshake time.
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
# build.1 / build.2 exist only in a wider PKI reached through IPMX_CERT_ROOT.
# When they are present their roots have to join the bundle, so the file has to
# be derived -- via mktemp, so no fixed scratch path is baked in.
if [ -f "$CERT_ROOT/build.1/ExampleRootCA.pem" ] || \
   [ -f "$CERT_ROOT/build.2/ExampleRootCA.pem" ]; then
  CA_MERGED="$(mktemp -t ExampleRootCA-bundle.XXXXXX)"
  cat "$CA" > "$CA_MERGED"
  for extra in build.1 build.2; do
    if [ -f "$CERT_ROOT/$extra/ExampleRootCA.pem" ]; then
      cat "$CERT_ROOT/$extra/ExampleRootCA.pem" >> "$CA_MERGED"
    fi
  done
  CA="$CA_MERGED"
fi

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

  