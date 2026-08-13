#!/usr/bin/env bash
#
# Bring up the local etcd cluster the distributed registry runs on.
#
#   ./start-etcd-cluster.sh              # 3 members, foreground, Ctrl-C stops all
#   ./start-etcd-cluster.sh 5            # 5 members
#   ./start-etcd-cluster.sh 3 --detach   # background; stop with `down`
#
# A thin wrapper over etcd_cluster.py, which derives the whole topology from
# nmos/etcd/cluster.py -- the SAME code the registry uses. That is deliberate:
# a hand-maintained shell topology is a second implementation that can disagree
# with the first, which is exactly how the scripts this replaces ended up
# deleting data directories on every start.
#
# POSIX only. etcd rates windows/amd64 Tier 3 (unstable, unmaintained), so this
# project never runs a member there -- run this inside WSL and point a
# native-Windows registry at it with --distributed --etcdExternal.

set -Eeuo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MEMBERS="${1:-3}"
shift || true

PYTHON="./.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

if [ ! -x ./.etcd/etcd ] && ! command -v etcd >/dev/null 2>&1; then
    echo "etcd not installed. Run ./install-etcd.sh first." >&2
    exit 1
fi

exec "$PYTHON" etcd_cluster.py --members "$MEMBERS" up "$@"
