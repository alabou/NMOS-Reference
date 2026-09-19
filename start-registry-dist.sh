#!/usr/bin/env bash
#
# Start one member of a distributed NMOS registry.
#
#   ./start-registry-dist.sh 0           # member 0 of 3 (default)
#   ./start-registry-dist.sh 1 3         # member 1 of 3
#
# Bring the etcd cluster up FIRST with ./start-etcd-cluster.sh, then start one
# registry per member, each in its own window.
#
# No TLS on either the NMOS listeners or etcd: this is the development rig.
# The secured equivalent is ./start-registry-dist-secure.sh, over a cluster
# started with ./start-etcd-cluster.sh N --secure -- NOT start-registry.sh,
# which is the standalone launcher and rejects --distributed.
#
# Nothing here accepts --oauth2, deliberately. The listeners are plain HTTP, and
# TR-10-SEC classifies that as NAP=0, a configuration a device "MUST not claim
# compliance" with; adding OAuth 2.0 on top would put bearer tokens on the wire
# in the clear while reporting a policy the deployment does not have. Use
# start-registry-dist-secure.sh --oauth2 instead.
#
# Registration ports follow the repository convention (8444 + index * 10), so
# several members coexist on one machine.

set -Eeuo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The Rust registry deliberately defers the etcd backend, so this launcher has
# no --rust. Sourced anyway, so that passing it says why rather than failing on
# an unrecognised flag as if it were a typo.
REGISTRY_RUNTIME_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$REGISTRY_RUNTIME_DIR/registry-runtime.sh"
registry_select_runtime "$@"
set -- "${REGISTRY_ARGS[@]}"
registry_reject_rust "The Rust registry has no etcd backend yet, so it cannot \
serve this rig. Use start-registry-raft.sh --rust for a distributed Rust \
registry, or drop --rust to run the Python one here."

INDEX="${1:-0}"
MEMBERS="${2:-3}"
shift 2 2>/dev/null || shift $# 

PYTHON="./.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

if ! [[ "$INDEX" =~ ^[0-9]+$ ]] || [ "$INDEX" -ge "$MEMBERS" ]; then
    echo "member index must be 0..$((MEMBERS - 1))" >&2
    exit 1
fi

# Ask the cluster tool for the endpoints rather than hard-coding them, so this
# script cannot drift from the topology the cluster actually formed.
ENDPOINTS="$("$PYTHON" etcd_cluster.py --members "$MEMBERS" endpoints)"

# One port block of 10 per member, so every member's three listeners move
# together and adding a member can never collide with an existing one.
REG_PORT=$((8444 + INDEX * 10))
QUERY_PORT=$((8443 + INDEX * 10))
WS_PORT=$((8448 + INDEX * 10))

echo "Registry member $INDEX of $MEMBERS"
echo "  Registration : http://127.0.0.1:${REG_PORT}/x-nmos/registration/v1.3/"
echo "  Query        : http://127.0.0.1:${QUERY_PORT}/x-nmos/query/v1.3/"
echo "  etcd         : ${ENDPOINTS}"
echo

exec "$PYTHON" nmos_registry.py \
    --registryDisableTLS \
    --registryAddr 127.0.0.1 \
    --registrationPort "$REG_PORT" \
    --queryPort "$QUERY_PORT" \
    --queryWebSocketPort "$WS_PORT" \
    --distributed \
    --distributedBackend etcd \
    --etcdExternal \
    --etcdDisableTLS \
    --registryAdvertisedHost 127.0.0.1 \
    --etcdEndpoints "$ENDPOINTS" \
    "$@"
