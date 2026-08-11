#!/usr/bin/env bash
# No certificate handling here: this launcher runs the Node with
# --nodeDisableTLS and --rdsDisableTLS, so it needs no PKI at all and stays
# runnable in a checkout that has no Certificates/ tree.

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
  
