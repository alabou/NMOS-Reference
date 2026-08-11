#!/usr/bin/env bash
# NMOS Registry — TLS, optional mTLS, optional OAuth 2.0 on the Query API.
#
# Usage:
#   start-registry.sh [rap] [registration-port] [--oauth2] [--as-host=H]
#                     [--as-port=P] [--tct=T] [--nap=N]
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
#   --nap=N         Query API access policy (default: 2)
#                     1  Unrestricted Read Only  -- reads open to any
#                        client trusting the registry cert; subscription
#                        create/delete still needs a client certificate.
#                        Use this to browse the Query API's HTML views
#                        without putting a client cert in your browser.
#                     2  Restricted Read Write -- mutual TLS for every
#                        request. Not available with --oauth2 (see below).
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

# Positional arguments are consumed only while they do not look like an option.
# Taking them by index instead meant `start-registry.sh --oauth2` landed in the first
# positional and was then shifted away: the flag looked accepted and changed
# nothing, so a rig meant to be RAP=2 ran as RAP=0 without a word.
POSITIONAL=()
while [ $# -gt 0 ] && [ "${#POSITIONAL[@]}" -lt 2 ]; do
  case "$1" in
    --*) break ;;
    *)   POSITIONAL+=("$1"); shift ;;
  esac
done

RAP="${POSITIONAL[0]:-1}"
REG_PORT="${POSITIONAL[1]:-8444}"

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

# Query is REG-1 and the WebSocket listener REG+4, so both ends of the range
# have to leave room: hence 2 rather than 1, and 65531 rather than 65535.
require_port "<registration-port>" "$REG_PORT" 2 65531
QUERY_PORT=$((REG_PORT - 1))
WS_PORT=$((REG_PORT + 4))

AS_HOST="XYZ-SNX00000"
AS_PORT="9443"
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
    *) echo "start-registry.sh: unknown arg $arg" >&2; exit 64 ;;
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

# The Node launchers build the same bundle, so both ends validate against one
# trust store containing the RSA and ECDSA roots.
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

# The Query API's own access policy, classified exactly as a Node's API is —
# see nmos_registry.py::classify_query_nap, which reuses the rules in
# nmos/node/security_tags.py. Both modes accept client certificates, so a
# Controller may authenticate with mTLS, with OAuth 2.0, or with both; they
# differ in what an *unauthenticated* client may do.
#
#   NAP=1  Unrestricted Read Only. Reads are open to any client that trusts
#          the registry's certificate; state-changing verbs (creating and
#          deleting subscriptions) still require a client certificate. This
#          is the mode that lets a browser read the Query API's HTML views
#          without provisioning a client certificate into it.
#   NAP=2  Restricted Read Write. Every request needs a client certificate.
#
# NAP=0 (no TLS at all) is start-registry-bare.sh, mirroring RAP=0.
case "$NAP" in
  1) QUERY_CA_FLAGS=(--queryTrustedRootCA "$CA" --queryOptionalClientAuth) ;;
  2) QUERY_CA_FLAGS=(--queryTrustedRootCA "$CA") ;;
  0) echo "start-registry.sh: NAP=0 (plain HTTP) is start-registry-bare.sh" >&2
     exit 64 ;;
  *) echo "start-registry.sh: unsupported --nap=$NAP" >&2; exit 64 ;;
esac

# §"Unrestricted Read Only" is not available under OAuth 2.0: "even read
# access MUST be explicitly provided by the OAuth 2.0 authorizations". The
# registry honours that — every read route is wrapped in check_oauth2, so the
# deployment really is NAP=2 — but silently accepting --nap=1 here would let
# an operator believe reads were open when they are not.
if [ "$NAP" = "1" ] && [ "$USE_OAUTH2" = "1" ]; then
  echo "start-registry.sh: --nap=1 (Unrestricted Read Only) is not allowed" >&2
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
