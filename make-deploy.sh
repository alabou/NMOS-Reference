#!/bin/bash
#
# Build self-contained deployment archives (.tar.gz + .zip) for the
# nmos-reference NODE. Extracts into nmos-reference-node/ — on any system with a
# Python 3.12 interpreter, create a venv, install requirements.txt, and run
# nmos_node.py (see the generated RUN.md inside the archive).
#
# Ships runtime source only: the nmos/ caps/ sdp/ pep/ packages (incl. the
# Jinja2 templates, controller static assets, and built-in node configs) plus
# the top-level launchers, requirements.txt and pyproject.toml. Tests, byte-
# code and tool caches are excluded.
#
set -euo pipefail

PROJ="$(cd "$(dirname "$0")" && pwd)"
NAME="nmos-reference-node"
STAGING="${PROJ}/${NAME}"
TGZ="${PROJ}/${NAME}.tar.gz"
ZIP="${PROJ}/${NAME}.zip"

rm -rf "${STAGING}"
mkdir -p "${STAGING}"

# --- Python packages (recursive; runtime only — no tests/caches/bytecode) ---
PACKAGES=(nmos caps sdp pep)
EXCLUDES=(--exclude='__pycache__' --exclude='*.pyc' --exclude='*.pyo'
          --exclude='tests' --exclude='.mypy_cache' --exclude='.pytest_cache')

MISSING=0
for d in "${PACKAGES[@]}"; do
    if [ -d "${PROJ}/${d}" ]; then
        # tar-pipe with excludes — portable and preserves the directory tree
        ( cd "${PROJ}" && tar cf - "${EXCLUDES[@]}" "${d}" ) | tar xf - -C "${STAGING}"
    else
        echo "WARNING: package ${d}/ not found, skipping"
        MISSING=$((MISSING + 1))
    fi
done

# --- Top-level files (launchers, metadata, dependency manifest) ---
TOP_FILES=(
    nmos_node.py        # primary node entry point
    run_server.py
    demo_controller.py
    requirements.txt
    pyproject.toml
    README.md
    LICENSE
)
for f in "${TOP_FILES[@]}"; do
    if [ -f "${PROJ}/${f}" ]; then
        cp "${PROJ}/${f}" "${STAGING}/"
    else
        echo "WARNING: ${f} not found, skipping"
        MISSING=$((MISSING + 1))
    fi
done

# --- Run instructions ---
cat > "${STAGING}/RUN.md" << 'READMEEOF'
# nmos-reference node — run instructions

Self-contained NMOS / AMWA + VSF IPMX reference **node**. Runs on any system
with a **Python 3.12+** interpreter.

## 1. Create a virtual environment and install dependencies

    python3.12 -m venv venv
    . venv/bin/activate              # Windows: venv\Scripts\activate
    pip install -r requirements.txt

`cryptography`, `pycryptodome` and `aiohttp` install platform-specific compiled
wheels, so the first install needs pip + internet to fetch the right wheel for
your OS/CPU. (For an offline install, pre-download wheels with
`pip download -r requirements.txt -d wheelhouse` on a matching platform, ship
`wheelhouse/`, then `pip install --no-index --find-links=wheelhouse -r requirements.txt`.)

## 2. Run the node

Plain HTTP (no TLS), registering with a registry at REG_HOST:

    python nmos_node.py \
        --nodeSerialNumber SNX00001 \
        --nodeAddr 127.0.0.1 --nodePort 7051 \
        --nodeControlPort 5050 --nodeDisableTLS \
        --controllerAdminPassword admin \
        --rdsHost REG_HOST --rdsRegistrationPort 8444 --rdsQueryPort 8443 \
        --rdsDisableTLS \
        --nodeConfig config10 --ipmx

- `--nodeConfig` selects a built-in config; see `nmos/node/config/builtin/*.json`.
- The controller UI is served on `--nodeControlPort` (here `:5050`).
- mTLS / OAuth 2.0 modes additionally require a `Certificates/` directory and
  extra flags — see README.md.

## Optional: developer/test dependencies

    pip install pytest pytest-asyncio pytest-aiohttp mypy
    python -m pytest
READMEEOF

# --- Build the archives ---
cd "${PROJ}"
rm -f "${TGZ}" "${ZIP}"
tar czf "${TGZ}" -C "${PROJ}" "${NAME}/"
( cd "${PROJ}" && zip -rq "${ZIP}" "${NAME}/" )

PY_COUNT=$(find "${STAGING}" -name '*.py' | wc -l | tr -d ' ')
DATA_COUNT=$(find "${STAGING}" \( -name '*.html' -o -name '*.json' -o -name '*.css' -o -name '*.js' \) | wc -l | tr -d ' ')
TGZ_SIZE=$(du -sh "${TGZ}" | cut -f1)
ZIP_SIZE=$(du -sh "${ZIP}" | cut -f1)
echo "Created ${TGZ} (${TGZ_SIZE})"
echo "Created ${ZIP} (${ZIP_SIZE})"
echo "Contents:"
echo "  ${PY_COUNT} Python files (tests excluded)"
echo "  ${DATA_COUNT} data files (templates / configs / static)"
[ ${MISSING} -gt 0 ] && echo "  WARNING: ${MISSING} item(s) not found"

rm -rf "${STAGING}"
