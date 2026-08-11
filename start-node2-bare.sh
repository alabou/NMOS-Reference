#!/usr/bin/env bash
# No certificate handling here: this launcher runs the Node with
# --nodeDisableTLS and --rdsDisableTLS, so it needs no PKI at all and stays
# runnable in a checkout that has no Certificates/ tree.

exec python3 nmos_node.py \
  --nodeSerialNumber SNX00002 \
  --nodeAddr 127.0.0.1 \
  --nodePort 7052 \
  --nodeDisableTLS \
  --rdsHost 127.0.0.1 \
  --rdsRegistrationPort 8444 \
  --rdsQueryPort 8443 \
  --rdsDisableTLS \
  --nodeConfig config10 \
  --ipmx
  
  
