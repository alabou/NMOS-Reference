#!/usr/bin/env bash
# Configuration A (mTLS without OAuth 2.0) on alternate ports.
#
# Byte-for-byte the same Node configuration as start-node1-noauth2.sh — same
# serial, same certificates, same trust anchor, same --nodeConfig — with only
# --nodePort and --nodeControlPort moved. It exists so the security validator
# can run while another Node already occupies 7051/5050, which is the normal
# state on a developer box running the quick-start.
#
# The argument contract is the validator's, not this script's: it passes
#   <as-host> <as-port> <rds-host> <rds-port> [--nap=N] [--rap=R] [--tct=T] ...
# to whatever it is given as --launch-dut. Those are accepted and handled the
# same way start-node1-noauth2.sh handles them, so this stays a drop-in
# replacement. Ports come from the environment instead:
#
#   NMOS_ALT_NODE_PORT     (default 17051)
#   NMOS_ALT_CONTROL_PORT  (default 15050)
#
# TLS is unaffected by the move: the certificate's SAN is the hostname
# XYZ-SNX00001, and a SAN does not constrain the port.
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

NODE_PORT="${NMOS_ALT_NODE_PORT:-17051}"
CONTROL_UI_PORT="${NMOS_ALT_CONTROL_PORT:-15050}"

# Positional arguments are consumed only while they do not look like an option.
# Taking them by index instead meant `start-node1-noauth2-altport.sh --rap=2` landed in the first
# positional and was then shifted away: the flag looked accepted and changed
# nothing, so a rig meant to be RAP=2 ran as RAP=0 without a word.
POSITIONAL=()
while [ $# -gt 0 ] && [ "${#POSITIONAL[@]}" -lt 4 ]; do
  case "$1" in
    --*) break ;;
    *)   POSITIONAL+=("$1"); shift ;;
  esac
done

# Positional args, matching start-node1-noauth2.sh. Config A contacts no
# authorization server, so $1/$2 are accepted and unused.
RDS_HOST="${POSITIONAL[2]:-}"
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

NAP=2
RAP=2
TCT=0
for arg in "$@"; do
  case "$arg" in
    --nap=*)  NAP="${arg#*=}" ;;
    --rap=*)  RAP="${arg#*=}" ;;
    --oaim=*) ;;   # Config A contacts no AS; OAIM is not applicable
    --tct=*)  TCT="${arg#*=}" ;;
    --split-controls) ;;
    *) echo "start-node1-noauth2-altport.sh: unknown arg $arg" >&2; exit 64 ;;
  esac
done

# Settled before the certificate probe below. These checks used to live in the
# same `case` statements that build the certificate paths, underneath the
# probe -- so on a checkout with no resolvable PKI an unsupported value
# answered "missing ExampleRootCA.pem" and exited 66 (EX_NOINPUT) instead of
# naming the argument and exiting 64 (EX_USAGE). Each value is decided once
# here, into a token that names the choice without naming a path.

# "" for RSA, ".ec" for ECDSA. TCT=2 takes the RSA certificate as TCT=0 does.
# One infix per identity the Node presents. TR-10-SEC gives TCT=2 as "Both":
# the listener then holds an RSA and an ECDSA identity at once and serves each
# client whichever its ClientHello can verify. TCT=2 selects the RSA
# certificate as TCT=0 does *and* the ECDSA one; it is not a third flavour.
case "$TCT" in
  0) TCT_INFIXES=("") ;;
  1) TCT_INFIXES=(".ec") ;;
  2) TCT_INFIXES=("" ".ec") ;;
  *) echo "start-node1-noauth2-altport.sh: unsupported --tct=$TCT" >&2; exit 64 ;;
esac

# How the Node presents itself to the Registration API. Named rather than left
# as the digit, so the block that turns it into flags is exhaustive over values
# this script chose itself.
case "$RAP" in
  0) RDS_MODE="plaintext" ;;
  1) RDS_MODE="server-tls" ;;
  2) RDS_MODE="mutual-tls" ;;
  *) echo "start-node1-noauth2-altport.sh: unsupported --rap=$RAP" >&2; exit 64 ;;
esac

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

# NAP=1 is "Unrestricted Read Only": the TLS layer accepts a client without a
# certificate and the application enforces one on state-changing verbs.
if [ "$NAP" = "1" ]; then
  NAP_FLAGS=(--nodeOptionalClientAuth)
else
  NAP_FLAGS=()
fi

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

RDS_ARGS=()
if [ -n "$RDS_HOST" ]; then
  RDS_ARGS=(
    --rdsHost "$RDS_HOST"
    --rdsRegistrationPort "$RDS_REG_PORT"
    --rdsQueryPort "$((RDS_REG_PORT - 1))"
  )
fi

exec python3 nmos_node.py \
  --nodeSerialNumber SNX00001 \
  --nodeAddr XYZ-SNX00001 \
  --nodePort "$NODE_PORT" \
  "${NODE_CERT_ARGS[@]}" \
  --nodeTrustedRootCA "$CA" \
  --nodeControlPort "$CONTROL_UI_PORT" \
  --controllerAdminPassword admin \
  "${NAP_FLAGS[@]}" \
  "${RDS_ARGS[@]}" \
  "${RDS_FLAGS[@]}" \
  --trustedRootCA "$CA" \
  --nodeConfig config10
