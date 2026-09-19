#!/usr/bin/env bash
# Which registry implementation a launcher starts: Python, or Rust with --rust.
#
# Sourced by every start-registry*.sh. It exists rather than being copied into
# each because the launchers already carry a warning about exactly that -- see
# start-registry-raft.sh on why there is one script for two security postures
# and not two -- and a runtime selector duplicated five times is the same
# hazard: they drift, and the one you are not running is the one that is wrong.
#
# The two implementations take the SAME command line. That is not a convenience
# here, it is the point: `rust/crates/nmos-registry-bin` implements
# nmos_registry.py's flags so that a rig can be pointed at either without
# changing anything else, and `cli_parity.rs` asserts the two agree. If a flag
# ever has to be translated in this file, that is a parity bug to fix in the
# Rust rather than paper over here.
#
# Usage, in a launcher:
#
#     SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
#     source "$SCRIPT_DIR/registry-runtime.sh"
#     registry_select_runtime "$@"
#     set -- "${REGISTRY_ARGS[@]}"          # --rust removed
#     ...
#     registry_runtime_command python3 nmos_registry.py
#     exec "${REGISTRY_CMD[@]}" --registryAddr ...
#
# `registry_select_runtime` must run BEFORE the launcher parses its own
# arguments, because those parsers reject what they do not recognise -- which
# is the behaviour that makes an unknown flag loud, and would otherwise make
# --rust loud too.

# Strip --rust from the argument list and record whether it was given.
#
# Sets REGISTRY_RUST (0/1) and REGISTRY_ARGS (everything else, in order).
registry_select_runtime() {
  REGISTRY_RUST=0
  REGISTRY_ARGS=()
  local arg
  for arg in "$@"; do
    case "$arg" in
      --rust) REGISTRY_RUST=1 ;;
      *)      REGISTRY_ARGS+=("$arg") ;;
    esac
  done
}

# Refuse --rust for a launcher the Rust registry cannot serve.
#
# The etcd rigs are the case: the Rust port deliberately defers the etcd
# backend, so it has no --etcd* flags at all. Without this the failure would be
# an unknown-argument error naming one flag, which reads like a typo rather
# than like "this backend does not exist yet".
registry_reject_rust() {
  if [ "${REGISTRY_RUST:-0}" = "1" ]; then
    echo "$(basename "$0"): --rust is not available for this launcher." >&2
    echo "  $1" >&2
    exit 64
  fi
}

# Resolve what to exec.
#
# Arguments are the command to use when --rust was NOT given, so each launcher
# keeps whichever interpreter it already chose rather than having one imposed
# here. Sets REGISTRY_CMD.
registry_runtime_command() {
  if [ "${REGISTRY_RUST:-0}" != "1" ]; then
    REGISTRY_CMD=("$@")
    return
  fi

  local here
  here="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

  # Release first: these launchers are what a rig is driven with, and a debug
  # build is slow enough to change the behaviour being observed. Debug is
  # accepted so an unoptimised build can still be driven deliberately, and it
  # says so, because a rig that is quietly ten times slower is worse than one
  # that refuses to start.
  local release="$here/rust/target/release/nmos-registry"
  local debug="$here/rust/target/debug/nmos-registry"

  if [ -n "${NMOS_RUST_REGISTRY:-}" ]; then
    if [ ! -x "${NMOS_RUST_REGISTRY}" ]; then
      echo "$(basename "$0"): NMOS_RUST_REGISTRY=${NMOS_RUST_REGISTRY} is not executable" >&2
      exit 66
    fi
    REGISTRY_CMD=("${NMOS_RUST_REGISTRY}")
  elif [ -x "$release" ]; then
    REGISTRY_CMD=("$release")
  elif [ -x "$debug" ]; then
    echo "$(basename "$0"): using the DEBUG build at $debug" >&2
    echo "  It is much slower than release; build with" \
         "\`cd rust && cargo build --release -p nmos-registry-bin\`" >&2
    REGISTRY_CMD=("$debug")
  else
    echo "$(basename "$0"): --rust given but no Rust registry is built." >&2
    echo "  Expected $release" >&2
    echo "  Build it with:" >&2
    echo "    cd rust && cargo build --release -p nmos-registry-bin" >&2
    echo "  Or point NMOS_RUST_REGISTRY at a binary elsewhere." >&2
    exit 66
  fi
}
