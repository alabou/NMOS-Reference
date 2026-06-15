#!/usr/bin/env bash
# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: sibling of this script's
# parent (i.e. <workspace>/Certificates).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CERT_ROOT="${IPMX_CERT_ROOT:-$SCRIPT_DIR/../Certificates}"
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
  
