#!/usr/bin/env bash
# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: sibling of this script's
# parent (i.e. <workspace>/Certificates).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Prefer the certificate subset bundled inside this repository, so a
# standalone clone of nmos-reference runs without the wider workspace PKI.
# That subset ships only the serials the quick-start and tutorials use
# (SNX00000 infrastructure, SNX00001, SNX00002); anything else falls back
# to the workspace-level Certificates/ tree. An explicit IPMX_CERT_ROOT
# always wins over both.
CERT_PROBE="ExampleRootCA.pem"
if [ -n "${IPMX_CERT_ROOT:-}" ]; then
  CERT_ROOT="$IPMX_CERT_ROOT"
elif [ -f "$SCRIPT_DIR/Certificates/build.0/$CERT_PROBE" ]; then
  CERT_ROOT="$SCRIPT_DIR/Certificates"
else
  CERT_ROOT="$SCRIPT_DIR/../Certificates"
fi
CERTS="$CERT_ROOT/build.0"

exec python3 nmos_node.py \
  --nodeSerialNumber SNX00003 \
  --nodeAddr 127.0.0.1 \
  --nodePort 7053 \
  --nodeDisableTLS \
  --rdsHost 127.0.0.1 \
  --rdsRegistrationPort 8444 \
  --rdsQueryPort 8443 \
  --rdsDisableTLS \
  --nodeConfig config1
  
