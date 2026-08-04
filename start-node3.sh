#!/usr/bin/env bash
# Cert directory resolution — override IPMX_CERT_ROOT to point at a
# different `Certificates/` layout. Default: sibling of this script's
# parent (i.e. <workspace>/Certificates).
#
# Requires hosts-file entries. This script addresses its peers by DNS name
# because the certificates carry DNS SANs (XYZ-SNX000nn) and an IP literal
# matches none of them. Map to 127.0.0.1 in /etc/hosts before running:
#
#     127.0.0.1   XYZ-SNX00000    # registry + Authorization Server
#     127.0.0.1   XYZ-SNX00001    # node 1 + Controller UI
#     127.0.0.1   XYZ-SNX00002    # node 2
#
# Passing 127.0.0.1 as the registry-host argument fails TLS verification for
# the same reason -- pass XYZ-SNX00000.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# Prefer the certificate subset bundled inside this repository, so a
# standalone clone of nmos-reference runs without the wider workspace PKI.
# That subset ships only the serials the quick-start and tutorials use
# (SNX00000 infrastructure, SNX00001, SNX00002); anything else falls back
# to the workspace-level Certificates/ tree. An explicit IPMX_CERT_ROOT
# always wins over both.
CERT_PROBE="pem/ExampleDeviceServer.ABC.SNX00003.chain.pem"
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
  
