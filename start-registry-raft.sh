#!/usr/bin/env bash
#
# Start one member of a distributed NMOS registry backed by the native raft
# consensus layer.
#
#   ./start-registry-raft.sh 0              # member 0 of 3 (default), plain HTTP
#   ./start-registry-raft.sh 1 3            # member 1 of 3
#   ./start-registry-raft.sh 0 3 --secure   # TLS everywhere, RAP=1
#   ./start-registry-raft.sh 0 3 2 --secure # ... RAP=2, mutual TLS Registration
#
# Usage:
#   start-registry-raft.sh <index> [members] [rap] [--secure]
#
#   <index>   Which member this is, 0..members-1.
#   [members] Cluster size: 1, 3 or 5 (default 3).
#   [rap]     Registry Access Policy for the Registration API, --secure only
#             (default 1). Same vocabulary as start-registry-dist-secure.sh:
#               1  Unrestricted Registration, server-authenticated TLS
#               2  Restricted Registration, mutual TLS
#             RAP=0 (plain HTTP) is this script without --secure.
#
# There is NO cluster to bring up first, and that is the whole difference from
# start-registry-dist.sh. With etcd the registry is a *client* of a separate
# cluster, so the rig is ./start-etcd-cluster.sh followed by one registry per
# member. Here the registries **are** the cluster: start N of these, in N
# windows, and they elect a leader among themselves. Nothing else is installed,
# and nothing else is running when they stop.
#
# Start them all. A 3-member cluster has no quorum until two are up, so the
# first window will report DEGRADED and refuse writes until the second one
# starts -- that is the cluster working, not failing.
#
# ONE script for both security postures, unlike the etcd pair. The etcd rig
# needs two because its two postures differ in what has to be brought up
# beforehand; raft's differ only in whether certificates are passed, so a
# second near-identical file would be a place for the two to drift apart.
#
# --secure turns on TLS in both places at once:
#   * the Registration and Query listeners -- Query always mutual (NAP=2),
#     Registration according to [rap] above, and
#   * the raft peer transport between members, always mutual.
# Without it, both are plain and the members must be on the loopback -- the
# configuration layer refuses an unencrypted raft transport anywhere else,
# because that transport carries every registration and every write.
#
# Nothing here accepts --oauth2. Same reason as start-registry-dist.sh: over
# plain HTTP that is NAP=0, which TR-10-SEC says a device "MUST not claim
# compliance" with, and putting bearer tokens on the wire in the clear while
# reporting a policy the deployment does not have is worse than not having it.
#
# PORTS. Registration 8544 + index * 10, clear of the etcd rig's 8444 block and
# the raft transport on 2482 + index * 10, clear of etcd's 2382 -- so a raft rig
# and an etcd rig can both be up on one developer's machine.

set -Eeuo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Positionals stop at the first option, so `... 0 3 --secure` cannot silently
# land --secure in the RAP slot. Same guard, same reason, as start-registry.sh.
POSITIONAL=()
while [ $# -gt 0 ] && [ "${#POSITIONAL[@]}" -lt 3 ]; do
  case "$1" in
    --*) break ;;
    *)   POSITIONAL+=("$1"); shift ;;
  esac
done

INDEX="${POSITIONAL[0]:-0}"
MEMBERS="${POSITIONAL[1]:-3}"
RAP="${POSITIONAL[2]:-1}"
SECURE=0

for arg in "$@"; do
  case "$arg" in
    --secure) SECURE=1 ;;
    *) echo "$(basename "$0"): unknown arg $arg" >&2; exit 64 ;;
  esac
done

if ! [[ "$INDEX" =~ ^[0-9]+$ ]]; then
  echo "$(basename "$0"): first argument must be the member index" >&2
  exit 64
fi
case "$MEMBERS" in
  1|3|5) ;;
  *) echo "$(basename "$0"): members must be 1, 3 or 5" >&2; exit 64 ;;
esac
if [ "$INDEX" -ge "$MEMBERS" ]; then
  echo "$(basename "$0"): member index must be 0..$((MEMBERS - 1))" >&2
  exit 64
fi
case "$RAP" in
  1|2) ;;
  0) echo "$(basename "$0"): RAP=0 (plain HTTP) is this script without" \
          "--secure" >&2
     exit 64 ;;
  *) echo "$(basename "$0"): unsupported RAP=$RAP" >&2; exit 64 ;;
esac
if [ "$SECURE" = "0" ] && [ "${POSITIONAL[2]:-}" != "" ]; then
  # Silently ignoring it would let an operator believe they had asked for
  # Restricted Registration and got it, on a listener that is plain HTTP.
  echo "$(basename "$0"): a RAP only means something with --secure" >&2
  exit 64
fi

PYTHON="./.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERIAL="SNX1000${INDEX}"

# The term/vote file. Repo-local and git-ignored, unlike the production default
# of /var/lib/nmos-registry/raft which needs root and outlives the rig.
#
# It is NOT a database -- about 24 bytes, written when the election term
# changes. Deleting it between runs is safe and is what the rig wants: a member
# that has never voted is a member starting from scratch. Deleting it under a
# LIVE cluster is the one thing that is not safe, which is why this happens
# before the registry starts and never while it is running.
STATE_DIR="$SCRIPT_DIR/.raft/$SERIAL"
mkdir -p "$STATE_DIR"

# --- topology --------------------------------------------------------------
#
# Members co-located on one machine share its address and separate by port.
#
# NO HOSTS FILE, in either posture -- and that is a real difference from the
# etcd rig, not an omission. etcd verifies the certificate a peer presents
# against the address the connection arrives from, which on one machine is
# always 127.0.0.1, so its members must be named and those names must resolve.
# Raft verifies against the shared SAN instead (--raftCertificateName), passed
# as the TLS server name, so the address is free to be a bare 127.0.0.1 for
# every member.
RAFT_PORT=$((2482 + INDEX * 10))

# --registryAdvertisedHost carries host:client_port and the peer port is
# client_port + 1, so the pair moves together. 2481 + index * 10 is the client
# side of the same block.
MEMBER_FLAGS=(--registryAdvertisedHost "127.0.0.1:$((2481 + INDEX * 10))")
for peer in $(seq 0 $((MEMBERS - 1))); do
  [ "$peer" = "$INDEX" ] && continue
  MEMBER_FLAGS+=(--registryNeighbour "127.0.0.1:$((2481 + peer * 10))")
done

REG_PORT=$((8544 + INDEX * 10))
QUERY_PORT=$((8543 + INDEX * 10))
WS_PORT=$((8548 + INDEX * 10))

if [ "$SECURE" = "1" ]; then
  # Same resolution order as start-registry-dist-secure.sh: IPMX_CERT_ROOT,
  # this checkout, then the workspace tree one level up, announcing the
  # fallback rather than taking it silently.
  CERT_PROBE="build.0.etcd/pem/ExampleDeviceServer.ABC.SNX10000.etcd.chain.pem"
  if [ -n "${IPMX_CERT_ROOT:-}" ]; then
    CERT_ROOT="$IPMX_CERT_ROOT"
  elif [ -f "$SCRIPT_DIR/Certificates/$CERT_PROBE" ]; then
    CERT_ROOT="$SCRIPT_DIR/Certificates"
  elif [ -f "$SCRIPT_DIR/../Certificates/$CERT_PROBE" ]; then
    CERT_ROOT="$SCRIPT_DIR/../Certificates"
    echo "$(basename "$0"): $CERT_PROBE is not in this checkout — using the" \
         "workspace PKI at $CERT_ROOT" >&2
  else
    echo "$(basename "$0"): missing $CERT_PROBE" >&2
    echo "  Searched $SCRIPT_DIR/Certificates and $SCRIPT_DIR/../Certificates." >&2
    echo "  Set IPMX_CERT_ROOT to a Certificates/ tree that carries it." >&2
    exit 66
  fi

  # The etcd certificate set serves raft unchanged. The roles are identical --
  # one certificate that both listens and dials, which is what its dual
  # serverAuth+clientAuth EKU is for -- and it carries the shared SAN that
  # --raftCertificateName defaults to.
  CERT="$CERT_ROOT/build.0.etcd/pem/ExampleDeviceServer.ABC.$SERIAL.etcd.chain.pem"
  KEY="$CERT_ROOT/build.0.etcd/key/ExampleDeviceServer.ABC.$SERIAL.etcd.key"
  for path in "$CERT" "$KEY"; do
    [ -f "$path" ] || { echo "$(basename "$0"): missing $path" >&2; exit 66; }
  done

  CA="$CERT_ROOT/build.0/ExampleRootCA-bundle.pem"
  if [ ! -f "$CA" ]; then
    for root in "$CERT_ROOT/build.0/ExampleRootCA.pem" \
                "$CERT_ROOT/build.0/ExampleRootCA.ec.pem"; do
      [ -f "$root" ] || { echo "$(basename "$0"): missing $root" >&2; exit 66; }
    done
    CA="$(mktemp -t ExampleRootCA-bundle.XXXXXX)"
    cat "$CERT_ROOT/build.0/ExampleRootCA.pem" \
        "$CERT_ROOT/build.0/ExampleRootCA.ec.pem" > "$CA"
  fi

  # RAP 2 is Restricted Registration: the Registration trust anchor is what
  # selects it from RAP 1, exactly as in start-registry-dist-secure.sh.
  if [ "$RAP" = "2" ]; then
    REG_CA_FLAGS=(--registrationTrustedRootCA "$CA")
  else
    REG_CA_FLAGS=()
  fi
  LISTENER_FLAGS=(
    --registrySerialNumber "$SERIAL"
    --registryCertificate "$CERT"
    --registryKey "$KEY"
    "${REG_CA_FLAGS[@]}"
    --queryTrustedRootCA "$CA"
    --trustedRootCA "$CA"
  )
  RAFT_FLAGS=(
    --raftCertificate "$CERT"
    --raftKey "$KEY"
    --raftTrustedRootCA "$CA"
  )
  SCHEME=https
  # The listeners are reached by the certificate's own name, which is what a
  # client verifies. The raft members are not: they reach each other at
  # 127.0.0.1 and verify the shared SAN. Two different names for two different
  # checks, on one certificate.
  LISTENER_HOST="XYZ-SNX1000${INDEX}"
  TRANSPORT_DESCRIPTION="mutual TLS, peers verified against the shared etcd SAN"
else
  LISTENER_FLAGS=(--registryDisableTLS)
  RAFT_FLAGS=(--raftDisableTLS)
  SCHEME=http
  LISTENER_HOST="127.0.0.1"
  TRANSPORT_DESCRIPTION="PLAINTEXT (loopback only) -- development rig"
fi

if [ "$SECURE" = "1" ]; then
  echo "Raft registry member $INDEX of $MEMBERS  (RAP=$RAP)"
else
  echo "Raft registry member $INDEX of $MEMBERS"
fi
echo "  Registration : ${SCHEME}://${LISTENER_HOST}:${REG_PORT}/x-nmos/registration/v1.3/"
echo "  Query        : ${SCHEME}://${LISTENER_HOST}:${QUERY_PORT}/x-nmos/query/v1.3/"
echo "  raft         : in-process on port ${RAFT_PORT}, ${TRANSPORT_DESCRIPTION}"
echo "  state-dir    : ${STATE_DIR}  (term/vote only -- the log is in memory)"
if [ "$MEMBERS" -gt 1 ]; then
  echo
  echo "  Start all $MEMBERS members. Until $(( MEMBERS / 2 + 1 )) are up there"
  echo "  is no quorum and writes are refused with 503; that is the cluster"
  echo "  working, not failing."
fi
echo

exec "$PYTHON" nmos_registry.py \
    --registryAddr 127.0.0.1 \
    --registrationPort "$REG_PORT" \
    --queryPort "$QUERY_PORT" \
    --queryWebSocketPort "$WS_PORT" \
    "${LISTENER_FLAGS[@]}" \
    --distributed \
    --distributedBackend raft \
    "${RAFT_FLAGS[@]}" \
    --raftStateDir "$STATE_DIR" \
    "${MEMBER_FLAGS[@]}" \
    --logFile "nmos-registry-raft-${INDEX}.log"
