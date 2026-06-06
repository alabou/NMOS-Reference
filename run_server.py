#!/usr/bin/env python3
# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Quick-start NMOS Node API server for testing."""

import sys
sys.path.insert(0, ".")

from aiohttp import web
from nmos.api import create_app
from nmos.node import Node
from nmos.node.config import ConfigBuilder
from nmos.crypto import ExclusiveSession

# Create and initialize node
node = Node()

import socket
HOST_IP = socket.gethostbyname(socket.gethostname())
PORT = 5050

node.init(serial_number="TST12345", host=HOST_IP, port=PORT)
node.exclusive_session = ExclusiveSession()

# Load config1 from JSON file
builder = ConfigBuilder(node, verbose=True)
sender_ids = builder.load_senders("nmos/node/config/builtin/config1.json")
receiver_ids = builder.load_receivers("nmos/node/config/builtin/config1.json")

node.publish()
print(f"Resources: {len(node.senders)} senders, {len(node.receivers)} receivers, "
      f"{len(node.sources)} sources, {len(node.flows)} flows")

# Create app and run
app = create_app(node)

print(f"\nNMOS Node API server starting on http://{HOST_IP}:{PORT}")
print(f"  IS-04: http://{HOST_IP}:{PORT}/x-nmos/node/v1.3/")
print(f"  IS-05: http://{HOST_IP}:{PORT}/x-nmos/connection/v1.1/")
print(f"  Exclusive: http://{HOST_IP}:{PORT}/x-manufacturer/exclusive/v1.0/")
print()

web.run_app(app, host="0.0.0.0", port=PORT)
