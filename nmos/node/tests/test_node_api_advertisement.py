# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The Node self-resource must advertise the API as it is actually served.

``href`` and ``api.endpoints[]`` are what a controller reads to decide how to
reach the Node API. Both were hard-coded to ``http`` with ``authorization``
left false, so a Node running TLS + OAuth 2.0 published
``http://host:port/`` and ``authorization: false`` while in fact serving
HTTPS and answering every unauthenticated request with 401 — sending any
IS-04 client that follows the Node resource (rather than the Device
``controls[]``, which were always correct) to the wrong scheme with no bearer.

These assert the *relationship* between the Node's posture and what it
publishes, in all four combinations, rather than pinning literal strings.
"""

from __future__ import annotations

from typing import Any

import pytest

from nmos.node import Node


def _node(*, tls: bool, oauth2: bool) -> Node:
    node = Node()
    node.init(
        serial_number="SNX00001",
        host="XYZ-SNX00001",
        port=7051,
        tls_enabled=tls,
        oauth2=oauth2,
    )
    return node


def _self_resource(node: Node) -> Any:
    """The IS-04 Node resource as it is published."""
    return node.node_value


@pytest.mark.parametrize("tls", [True, False])
@pytest.mark.parametrize("oauth2", [True, False])
def test_href_and_endpoint_follow_the_tls_posture(
    tls: bool, oauth2: bool,
) -> None:
    node = _node(tls=tls, oauth2=oauth2)
    value = _self_resource(node)
    expected_scheme = "https" if tls else "http"

    href = value.Href.value
    assert href.startswith(f"{expected_scheme}://"), (
        f"tls_enabled={tls} but href advertises {href!r}"
    )

    endpoints = value.Api.value.Endpoints.value
    assert len(endpoints) == 1
    protocol = str(endpoints[0].Protocol.value)
    assert protocol.endswith(expected_scheme), (
        f"tls_enabled={tls} but api.endpoints[0].protocol is {protocol!r}"
    )


@pytest.mark.parametrize("oauth2", [True, False])
def test_endpoint_authorization_mirrors_oauth2(oauth2: bool) -> None:
    """IS-04 v1.3's ``authorization`` says whether this API needs a bearer.

    Left at its default of false, a compliant controller would not send one
    and would be refused 401 by every route.
    """
    node = _node(tls=True, oauth2=oauth2)
    endpoints = _self_resource(node).Api.value.Endpoints.value
    assert endpoints[0].Authorization.value is oauth2


def test_node_href_agrees_with_the_device_controls() -> None:
    """The two places a scheme is published must not disagree.

    The Device ``controls[]`` always derived their scheme correctly, so a
    mismatch between them and ``href`` is the signature of this bug.
    """
    node = _node(tls=True, oauth2=True)
    href = _self_resource(node).Href.value
    controls = node.device_value.Controls.value
    assert controls, "expected the Device to publish controls[]"
    for control in controls:
        control_href = control.Href.value
        assert control_href.split("://")[0] == href.split("://")[0], (
            f"node href {href!r} and control href {control_href!r} "
            f"advertise different schemes"
        )
