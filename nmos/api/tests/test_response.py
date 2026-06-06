# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Tests for HTML link rendering in nmos.api.response."""

from __future__ import annotations

from nmos.api.response import _json_to_html


def test_json_to_html_links_api_refs_guid_and_urls() -> None:
    sender_id = "9ca4292b-2000-4000-8006-802425e60405"
    json_str = (
        "{\n"
        f'  "id": "{sender_id}",\n'
        '  "url": "http://example.com/stream.sdp",\n'
        '  "mode": "auto",\n'
        '  "next": "staged/",\n'
        '  "leaf": "transportfile",\n'
        '  "non_api": "not_a_resource"\n'
        "}"
    )

    html = _json_to_html(json_str, "/x-nmos/connection/v1.0/single/senders/")

    assert f'href="/x-nmos/connection/v1.0/single/senders/{sender_id}"' in html
    assert 'href="http://example.com/stream.sdp"' in html
    assert 'href="/x-nmos/connection/v1.0/single/senders/staged/"' in html
    assert 'href="/x-nmos/connection/v1.0/single/senders/transportfile"' in html
    assert 'href="/x-nmos/connection/v1.0/single/senders/not_a_resource"' not in html


def test_json_to_html_guid_on_resource_page_links_to_collection() -> None:
    sender_id = "9ca4292b-2000-4000-8006-802425e60405"
    json_str = f'{{"id":"{sender_id}"}}'

    html = _json_to_html(json_str, f"/x-nmos/node/v1.3/senders/{sender_id}")

    assert f'href="/x-nmos/node/v1.3/senders/{sender_id}"' in html
    assert f'href="/x-nmos/node/v1.3/senders/{sender_id}/{sender_id}"' not in html
