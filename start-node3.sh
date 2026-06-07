#!/usr/bin/env bash
# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: sibling of this script's
# parent (i.e. <workspace>/Certificates).
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CERT_ROOT="${IPMX_CERT_ROOT:-$SCRIPT_DIR/../Certificates}"
CERTS="$CERT_ROOT/build.0"

exec python3 nmos_node.py \
  --nodeSerialNumber SNX00003 \
  --nodeAddr XYZ-SNX00003 \
  --nodePort 7053 \
  --nodeCertificate "$CERTS/pem/ExampleDeviceServer.ABC.SNX00003.chain.pem" \
  --nodeKey         "$CERTS/key/ExampleDeviceServer.ABC.SNX00003.key" \
  --oauth2 \
  --oauth2Host XYZ-SNX00000 \
  --oauth2Port 9443 \
  --oauth2TrustedRootCA "$CERTS/ExampleRootCA.pem" \
  --oauth2ClientSecret secret \
  --oauth2ApiSelector realms/TR-10-SEC \
  --rdsHost 127.0.0.1 \
  --rdsRegistrationPort 8444 \
  --rdsQueryPort 8443 \
  --rdsDisableTLS \
  --trustedRootCA "$CERTS/ExampleRootCA.pem" \
  --nodeConfig config_av_usb_tb_B
  
