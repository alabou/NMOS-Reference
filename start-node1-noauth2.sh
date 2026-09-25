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
#   --tct=T    TLS Cert Type: 0=RSA (default), 1=ECDSA, 2=both (presents whichever each client asks for)
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

# Settled before the certificate probe below. These checks used to live in the
# same `case` statements that build the certificate paths, underneath the
# probe -- so on a checkout with no resolvable PKI an unsupported value
# answered "missing ExampleRootCA.pem" and exited 66 (EX_NOINPUT) instead of
# naming the argument and exiting 64 (EX_USAGE).

# "" for RSA, ".ec" for ECDSA. TCT=2 takes the RSA certificate as TCT=0 does.
# One infix per identity the Node presents. TR-10-SEC gives TCT=2 as "Both":
# the listener then holds an RSA and an ECDSA identity at once and serves each
# client whichever its ClientHello can verify. TCT=2 selects the RSA
# certificate as TCT=0 does *and* the ECDSA one; it is not a third flavour.
case "$TCT" in
  0) TCT_INFIXES=("") ;;
  1) TCT_INFIXES=(".ec") ;;
  2) TCT_INFIXES=("" ".ec") ;;
  *) echo "start-node1-noauth2.sh: unsupported --tct=$TCT" >&2; exit 64 ;;
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

# How the Node presents itself to the Registration API.
case "$RAP" in
  0) RDS_MODE="plaintext" ;;
  1) RDS_MODE="server-tls" ;;
  2) RDS_MODE="mutual-tls" ;;
  *) echo "start-node1-noauth2.sh: unsupported --rap=$RAP" >&2; exit 64 ;;
esac

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

# Trust follows the certificate type, like the identities below: TR-10-SEC
# §12.5 makes the TCT "common to all certificates and Root CAs of the
# device", so a TCT=0 Node trusts the RSA root only, a TCT=1 Node the ECDSA
# root only, and only a TCT=2 Node both.
CA_ROOTS=()
for infix in "${TCT_INFIXES[@]}"; do
  CA_ROOTS+=("$CERTS/ExampleRootCA${infix}.pem")
done
for root in "${CA_ROOTS[@]}"; do
  if [ ! -f "$root" ]; then
    echo "$(basename "$0"): missing $root" >&2
    echo "  Set IPMX_CERT_ROOT to a Certificates/ tree that carries it." >&2
    exit 66
  fi
done
CA="${CA_ROOTS[0]}"
if [ "${#CA_ROOTS[@]}" -gt 1 ]; then
  # TCT=2: one file holding both roots -- the RSA and the ECDSA generation of
  # the same CA -- so either certificate flavour validates against a single
  # --trustedRootCA. It ships in Certificates/ next to the two roots it is
  # built from, rather than being written to a scratch path at every start-up.
  CA="$CERTS/ExampleRootCA-bundle.pem"
  if [ ! -f "$CA" ]; then
    # A PKI supplied from outside this checkout -- IPMX_CERT_ROOT, or the
    # workspace tree the IPMX security test suite drives these launchers with --
    # carries the two roots but not the combined file, so derive it from them.
    # mktemp rather than a fixed path: /tmp/ExampleRootCA-bundle.pem used to be
    # shared by every launcher and rewritten on each start-up.
    CA="$(mktemp -t ExampleRootCA-bundle.XXXXXX)"
    cat "${CA_ROOTS[@]}" > "$CA"
  fi
fi

# $TCT was validated above; one --nodeCertificate/--nodeKey pair per
# identity. Repeating the flags is how TCT=2 reaches the listener --
# see nmos/tls_identity.py for why one context holds them all.
NODE_CERT_ARGS=()
for infix in "${TCT_INFIXES[@]}"; do
  NODE_CERT_ARGS+=(--nodeCertificate "$CERTS/pem/ExampleDeviceServer.ABC.SNX00001.chain${infix}.pem")
  NODE_CERT_ARGS+=(--nodeKey "$CERTS/key/ExampleDeviceServer.ABC.SNX00001${infix}.key")
done

# The client identities follow the same infixes: TR-10-SEC applies the
# certificate type "to both endpoint and client accesses, and to server and
# client certificates" (§11), so a TCT=1 Node authenticates with its
# ECDSA certificate and a TCT=2 Node holds both -- one pair per identity, as
# for the listener above.
RDS_CLIENT_ARGS=()
for infix in "${TCT_INFIXES[@]}"; do
  RDS_CLIENT_ARGS+=(--rdsClientCertificate "$CERTS/pem/ExampleDeviceClient.ABC.SNX00001.chain${infix}.pem")
  RDS_CLIENT_ARGS+=(--rdsClientKey "$CERTS/key/ExampleDeviceClient.ABC.SNX00001${infix}.key")
done

case "$RDS_MODE" in
  plaintext)  RDS_FLAGS=(--rdsDisableTLS) ;;
  server-tls) RDS_FLAGS=() ;;
  mutual-tls) RDS_FLAGS=("${RDS_CLIENT_ARGS[@]}") ;;
esac

# --split-controls: separate trust stores per listener.
# NESTCA = root used for incoming TLS client auth on Node IS-04 API.
# CESTCA = root used for incoming TLS client auth on IS-05/IS-08/IS-11.
# Without --split-controls, both listeners share --nodeTrustedRootCA. The two
# follow the certificate type like every other root, one flag per root (the
# trust flags are repeatable). build.1 / build.2 exist only in a wider PKI
# reached through IPMX_CERT_ROOT.
#
# Reference-node validates that every per-role trust root chains under the
# global --trustedRootCA set, so the per-listener roots join that set too --
# even though their *application role* is solely per-listener mTLS client
# validation. Only under --split-controls: adding them whenever they were
# present made every other configuration trust extra CAs.
SPLIT_FLAGS=()
NODE_TRUST_ARGS=(--nodeTrustedRootCA "$CA")
GLOBAL_TRUST_ARGS=(--trustedRootCA "$CA")
if [ "$SPLIT_CONTROLS" = "1" ]; then
  NODE_TRUST_ARGS=()
  for infix in "${TCT_INFIXES[@]}"; do
    NESTCA="$CERT_ROOT/build.1/ExampleRootCA${infix}.pem"
    CESTCA="$CERT_ROOT/build.2/ExampleRootCA${infix}.pem"
    if [ ! -f "$NESTCA" ] || [ ! -f "$CESTCA" ]; then
      echo "start-node1-noauth2.sh: --split-controls needs build.1 + build.2 trust roots" >&2
      exit 64
    fi
    NODE_TRUST_ARGS+=(--nodeTrustedRootCA "$NESTCA")
    SPLIT_FLAGS+=(--controlTrustedRootCA "$CESTCA")
    GLOBAL_TRUST_ARGS+=(--trustedRootCA "$NESTCA" --trustedRootCA "$CESTCA")
  done
  SPLIT_FLAGS+=(--controlPort 7052)
fi

GCRL_FLAGS=()
if [ -n "$GCRL" ]; then
  GCRL_FLAGS=(--gcrl "$GCRL")
fi

exec python3 nmos_node.py \
  --nodeSerialNumber SNX00001 \
  --nodeAddr XYZ-SNX00001 \
  --nodePort 7051 \
  "${NODE_CERT_ARGS[@]}" \
  "${NODE_TRUST_ARGS[@]}" \
  "${SPLIT_FLAGS[@]}" \
  --nodeControlPort 5050 \
  --controllerAdminPassword admin \
  "${NAP_FLAGS[@]}" \
  --rdsHost "${RDS_HOST}" \
  --rdsRegistrationPort "${RDS_REG_PORT}" \
  --rdsQueryPort        "${RDS_QUERY_PORT}" \
  "${RDS_FLAGS[@]}" \
  "${GLOBAL_TRUST_ARGS[@]}" \
  "${GCRL_FLAGS[@]}" \
  --debug-in-depth \
  --nodeConfig config10

  