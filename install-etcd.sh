#!/usr/bin/env bash
#
# Fetch a version-pinned etcd into the repo-local .etcd/ directory.
#
# pip cannot install etcd: it is a Go binary, not a Python package. This is the
# same shape as `playwright install chromium` in requirements-agentui.txt --
# a self-contained, version-pinned dependency landing in a git-ignored directory
# under the repository, with nothing entering the system package database, no
# apt or sudo step, and `rm -rf .etcd` as the uninstaller.
#
#   ./install-etcd.sh            # install the pinned version
#   ./install-etcd.sh --force    # replace an existing .etcd/ with the same version
#
# A system- or service-managed etcd is equally fine. Point --etcdBinary at it,
# or run it under systemd and let the registry adopt it rather than launch it.
#
# PLATFORM: this installs the etcd SERVER, so it refuses to run on Windows.
# etcd classifies windows/amd64 as Tier 3 -- "considered unstable", with no
# maintainers and no coverage from the functional and robustness suites that
# verify Raft/WAL/fsync durability -- so this project never runs an etcd member
# there. A native-Windows registry is a client only (--distributed implies
# --etcdExternal), and needs nothing installed: `python3 etcd_cluster.py status`
# and `endpoints` are pure client RPCs and work from Windows against a cluster
# running in WSL or on a Linux host. Under WSL you are on Linux and this script
# is the right one.

set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ETCD_VER="v3.6.14"
readonly TARGET_DIR="${SCRIPT_DIR}/.etcd"

# Published SHA-256 of the release archives, from
# https://github.com/etcd-io/etcd/releases/download/v3.6.14/SHA256SUMS
#
# Verified BEFORE extraction: an unverified archive that is extracted first and
# checked afterwards has already written whatever it contained.
#
# Note the archive format differs by platform -- Linux ships .tar.gz, macOS
# ships .zip -- so the extension is part of the platform table rather than a
# constant.
sha_for() {
    case "$1" in
        linux-amd64)  echo "ffe840ff9295808e88cce2794a18a5ac87f12a5203c8314d0bf6aa119b41bac5" ;;
        linux-arm64)  echo "fa1b80565ee6fc2df1ae6f57a508b221f3ecc7d591c317c581cd8281b0842b3e" ;;
        darwin-amd64) echo "17b8639cf303fee6c35958278d557263215a9a8f8c15fd89022b5aa67091b228" ;;
        darwin-arm64) echo "fd154573a1f4c098c214d8233507d2766c43a164f7e339c0385aae71d9a1afaf" ;;
        *) return 1 ;;
    esac
}

archive_extension_for() {
    case "$1" in
        linux-*)  echo "tar.gz" ;;
        darwin-*) echo "zip" ;;
        *) return 1 ;;
    esac
}

die() { printf 'install-etcd.sh: %s\n' "$*" >&2; exit 1; }

FORCE=0
for arg in "$@"; do
    case "$arg" in
        --force) FORCE=1 ;;
        -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "unknown option: $arg" ;;
    esac
done

# --- platform ------------------------------------------------------------

case "$(uname -s)" in
    Linux)  os="linux" ;;
    Darwin) os="darwin" ;;
    MINGW*|MSYS*|CYGWIN*)
        die "this installs the etcd server, which this project runs on POSIX only.
  etcd classifies windows/amd64 as Tier 3 (unstable, unmaintained), so no etcd
  member runs on Windows. Nothing needs installing here: run the cluster under
  WSL (./install-etcd.sh then etcd_cluster.py up), and point the registry at it
  with --distributed --etcdExternal --etcdEndpoints. 'etcd_cluster.py status'
  works from Windows against that cluster without any etcd binary." ;;
    *) die "unsupported operating system: $(uname -s)" ;;
esac

case "$(uname -m)" in
    x86_64|amd64) arch="amd64" ;;
    aarch64|arm64) arch="arm64" ;;
    *) die "unsupported architecture: $(uname -m)" ;;
esac

readonly PLATFORM="${os}-${arch}"
EXTENSION="$(archive_extension_for "$PLATFORM")" || die "unsupported platform: $PLATFORM"
readonly EXTENSION
readonly ARCHIVE="etcd-${ETCD_VER}-${PLATFORM}.${EXTENSION}"

# --- refuse to clobber ---------------------------------------------------

if [[ -e "${TARGET_DIR}/etcd" && $FORCE -eq 0 ]]; then
    installed="$("${TARGET_DIR}/etcd" --version 2>/dev/null | head -1 || true)"
    # Never replace a binary a running cluster may be executing without being
    # asked to: a live member losing its executable mid-run is a failure mode
    # that looks like anything but an installer problem.
    if [[ "$installed" == *"${ETCD_VER#v}"* ]]; then
        printf 'etcd %s already installed in %s\n' "$ETCD_VER" "$TARGET_DIR"
        exit 0
    fi
    die "a different etcd is already installed in ${TARGET_DIR}:
  ${installed:-unknown}
  Wanted ${ETCD_VER}. Stop any running cluster, then re-run with --force."
fi

command -v curl >/dev/null 2>&1 || die "curl is required"
if [[ "$EXTENSION" == "zip" ]]; then
    command -v unzip >/dev/null 2>&1 || die "unzip is required on $os"
else
    command -v tar >/dev/null 2>&1 || die "tar is required"
fi

if command -v sha256sum >/dev/null 2>&1; then
    checksum() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
    checksum() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
    die "sha256sum or shasum is required"
fi

# --- download ------------------------------------------------------------

STAGE="$(mktemp -d "${TMPDIR:-/tmp}/.etcd-install.XXXXXX")"
trap 'rm -rf -- "$STAGE"' EXIT
chmod 0700 "$STAGE"

url="https://github.com/etcd-io/etcd/releases/download/${ETCD_VER}/${ARCHIVE}"
printf 'Downloading %s\n' "$url"
curl -sSfL "$url" -o "${STAGE}/${ARCHIVE}" ||
    die "download failed. Mirror: https://storage.googleapis.com/etcd/${ETCD_VER}/${ARCHIVE}"

expected="$(sha_for "$PLATFORM" || true)"
actual="$(checksum "${STAGE}/${ARCHIVE}")"

if [[ -z "$expected" || "$expected" == REPLACE_WITH_* ]]; then
    # Fail rather than install unverified. A checksum that is "not configured
    # yet" must not silently degrade into "not checked" -- that is precisely the
    # supply-chain gap this step exists to close.
    die "no published SHA-256 recorded for ${PLATFORM}.
  Take it from https://github.com/etcd-io/etcd/releases/tag/${ETCD_VER}
  and put it in sha_for() above. Measured for this download:
    ${actual}"
fi

[[ "$actual" == "$expected" ]] ||
    die "checksum mismatch for ${ARCHIVE}
  expected ${expected}
  actual   ${actual}"

# --- install -------------------------------------------------------------

if [[ "$EXTENSION" == "zip" ]]; then
    unzip -q "${STAGE}/${ARCHIVE}" -d "$STAGE"
else
    tar xzf "${STAGE}/${ARCHIVE}" -C "$STAGE"
fi
mkdir -p "$TARGET_DIR"
for binary in etcd etcdctl etcdutl; do
    install -m 0755 "${STAGE}/etcd-${ETCD_VER}-${PLATFORM}/${binary}" \
        "${TARGET_DIR}/${binary}"
done

printf '\nInstalled into %s:\n' "$TARGET_DIR"
"${TARGET_DIR}/etcd" --version | head -3
printf '\n  Bring a local cluster up with:  python3 etcd_cluster.py up --members 3\n'
printf '  --etcdBinary finds %s automatically.\n' "${TARGET_DIR}/etcd"
