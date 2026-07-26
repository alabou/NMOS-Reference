# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Node-embedded NMOS Controller — per-Node capabilities / connection UI.

Controller flow (sender list → caps → configure;
receiver list → compatible senders → intersected caps → configure +
activate) as a second aiohttp application served on ``--nodeControlPort``
under ``/controller/``.

Access control: a password-only login form at ``/controller/login`` that
issues a session cookie, checked against a single admin password supplied
on the Node command line (``--controllerAdminPassword``). The controller
is the orchestrator of OAuth2 / Reservation on the Node side, so gating
it with those same mechanisms would be circular; TLS transport still
follows the Node's server-auth / mutual-auth mode, but application-level
OAuth2 and Reservation are NOT required to reach the controller.

Outbound calls from the controller to remote Nodes can still carry
``Authorization`` / ``PEP-Exclusive-Authorization`` headers when those
remote Nodes require them — see ``handlers._forwarded_auth``.

The module is self-contained: the only public entry point is
``create_controller_app(node, admin_password=..., ...)``.
"""

from __future__ import annotations

from nmos.controller.app import create_controller_app

__all__ = ["create_controller_app"]
