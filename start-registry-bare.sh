#!/usr/bin/env bash
# NMOS Registry — no TLS. The "just try it" configuration.
#
# Pairs with start-node1-bare.sh / start-node2-bare.sh with no arguments on
# either side:
#
#     ./start-registry-bare.sh      # terminal 1
#     ./start-node1-bare.sh         # terminal 2
#     ./start-node2-bare.sh         # terminal 3
#
# then open the Controller UI that node1 serves on http://127.0.0.1:5050/controller/
#
# Usage:
#   start-registry-bare.sh [registration-port] [bind-address]
#
#   $1 = Registration API port (default: 8444; query port = $1-1, ws = $1+4)
#   $2 = Bind address (default: 127.0.0.1)
#
# Reaching the registry from another host
# ---------------------------------------
# The default 127.0.0.1 binds loopback only, which is right when the registry
# and the Nodes share a host. Bind something routable when they do not --
# in particular when Nodes run on Windows against a registry in WSL, because
# start-node1-bare.bat points itself at the WSL IP (`wsl.exe hostname -I`)
# and so cannot reach a loopback-only registry:
#
#     ./start-registry-bare.sh 8444 0.0.0.0            # all interfaces
#     ./start-registry-bare.sh 8444 "$(hostname -I | awk '{print $1}')"
#
# A Windows browser reaching a WSL-hosted Controller UI on 127.0.0.1 is a
# separate matter and normally works through WSL2's localhost forwarding. If
# it stops working, suspect the WSL network stack rather than the bind
# address: `wsl --shutdown` from Windows restarts it.
#
# Ports default to 8444/8443 to match the node launchers, which default
# RDS_REG_PORT=8444 and derive RDS_QUERY_PORT=$((RDS_REG_PORT - 1)). Note
# those differ from nmos_registry.py's own argparse defaults (8447/8446/8448),
# which match nmos_node.py's --rds* defaults instead. Passing them explicitly
# here is what keeps the two families of launcher consistent with each other.
#
# TR-10-SEC note: with no TLS the Registration API runs under Registry Access
# Policy 0 (Unrestricted Registration over HTTP). That is a development
# configuration -- §"NMOS Registry" requires TLS for a compliant deployment.
# Use start-registry.sh for that.

set -e

REG_PORT="${1:-8444}"
BIND_ADDR="${2:-${NMOS_REGISTRY_ADDR:-127.0.0.1}}"
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

exec python3 nmos_registry.py \
  --registryAddr "${BIND_ADDR}" \
  --registryDisableTLS \
  --registrationPort "${REG_PORT}" \
  --queryPort        "${QUERY_PORT}" \
  --queryWebSocketPort "${WS_PORT}" \
  --logFile nmos-registry.log
