# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Response formatting — JSON, HTML, CORS, error responses.

Implements doResponsePrologue/doResponseEpilogue/doResponseError patterns.
All responses get CORS headers. When a browser sends Accept: text/html,
JSON values are rendered as clickable hyperlinks in an HTML page.
"""

from __future__ import annotations

import html
import re
from typing import Any

from aiohttp import web
from nmos.json.engine import JsonEngine

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
_ABS_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_VERSION_SEGMENT_RE = re.compile(r"^v[0-9]+\.[0-9]+$")
_API_SEGMENTS = {
    "x-nmos",
    "x-manufacturer",
    "node",
    "connection",
    "streamcompatibility",
    "exclusive",
    "single",
    "self",
    "devices",
    "sources",
    "flows",
    "senders",
    "receivers",
    "staged",
    "active",
    "constraints",
    "transportfile",
    "transporttype",
    "status",
    "inputs",
    "outputs",
    "supported",
    "acquire",
    "renew",
    "release",
    "keepalive",
}


# ---------------------------------------------------------------------------
# CORS headers (applied to all responses)
# ---------------------------------------------------------------------------

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, PUT, POST, PATCH, HEAD, OPTIONS, DELETE",
    "Access-Control-Allow-Headers": "Content-Type, Accept, Authorization, PEP-Exclusive-Authorization",
    "Access-Control-Max-Age": "3600",
    "Vary": "Origin",
}


def _add_cors(headers: dict[str, str]) -> dict[str, str]:
    """Add CORS headers to a response header dict."""
    result = dict(CORS_HEADERS)
    result.update(headers)
    return result


# ---------------------------------------------------------------------------
# HTML rendering of JSON (clickable links for browser navigation)
# ---------------------------------------------------------------------------

def _wants_html(request: web.Request) -> bool:
    """Check if the client prefers HTML (browser)."""
    accept = request.headers.get("Accept", "")
    return "text/html" in accept


def _wrap_engine_html(engine_html: str, request_path: str) -> str:
    """Wrap JsonEngine HTML output in a full HTML page.

    The engine produces JSON decorated with <span class="object/array/name/value/string/number">,
    <ol>, <li> tags. This wraps it in a page with CSS that styles the output.
    """
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>NMOS API - {html.escape(request_path)}</title>
<style>
body {{ font-family: monospace; background: #1e1e1e; color: #d4d4d4; padding: 20px; margin: 0; }}
h2 {{ color: #9cdcfe; font-size: 16px; margin-bottom: 10px; }}
a {{ color: #569cd6; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
ol {{ list-style: none; padding-left: 20px; margin: 0; }}
li {{ line-height: 1.4; }}
.object, .array {{ }}
.name {{ color: #9cdcfe; }}
.value {{ }}
.string {{ color: #ce9178; }}
.number {{ color: #b5cea8; }}
.null {{ color: #569cd6; }}
.bool {{ color: #569cd6; }}
.boolean {{ color: #569cd6; }}
</style>
</head>
<body>
<h2>{html.escape(request_path)}</h2>
{engine_html}
</body>
</html>"""


def _json_to_html(json_str: str, request_path: str) -> str:
    """Convert a JSON string to an HTML page with clickable links.

    Only absolute URLs and GUID-like resource IDs become hyperlinks.
    Other string values remain plain text.
    """
    trimmed = request_path.rstrip("/")
    base_path = trimmed + "/"

    # GUID links should target the current collection, not nested under a resource id.
    parts = [p for p in trimmed.split("/") if p]
    last_segment = parts[-1] if parts else ""
    if _UUID_RE.match(last_segment):
        guid_base_path = "/" + "/".join(parts[:-1]) + "/"
    else:
        guid_base_path = base_path

    def is_api_segment(segment: str) -> bool:
        if _UUID_RE.match(segment):
            return True
        if _VERSION_SEGMENT_RE.match(segment):
            return True
        return segment in _API_SEGMENTS

    def is_relative_api_ref(value: str) -> bool:
        if not value or value.startswith("/") or _ABS_URL_RE.match(value):
            return False
        path = value[:-1] if value.endswith("/") else value
        segments = [seg for seg in path.split("/") if seg]
        if not segments:
            return False
        return all(is_api_segment(seg) for seg in segments)

    def resolve_href(raw_value: str) -> str | None:
        if _ABS_URL_RE.match(raw_value):
            return raw_value

        guid = raw_value[:-1] if raw_value.endswith("/") else raw_value
        if _UUID_RE.match(guid):
            if raw_value.endswith("/"):
                return guid_base_path + guid + "/"
            return guid_base_path + guid

        if raw_value.startswith("/"):
            if raw_value.startswith("/x-nmos/") or raw_value.startswith("/x-manufacturer/"):
                return raw_value
            return None

        if is_relative_api_ref(raw_value):
            return base_path + raw_value

        return None

    def render_scalar(value: Any) -> str:
        if isinstance(value, str):
            value_json = html.escape(JsonEngine.dump_any(value, ensure_ascii=False))
            href = resolve_href(value)
            if href is not None:
                href_escaped = html.escape(href)
                return (
                    '<span class="value"><a href="'
                    + href_escaped
                    + '"><span class="string">'
                    + value_json
                    + "</span></a></span>"
                )
            return f'<span class="value"><span class="string">{value_json}</span></span>'

        if isinstance(value, bool):
            return (
                '<span class="value"><span class="boolean">'
                + ("true" if value else "false")
                + "</span></span>"
            )

        if value is None:
            return '<span class="value"><span class="null">null</span></span>'

        if isinstance(value, (int, float)):
            return f'<span class="value"><span class="number">{value}</span></span>'

        fallback_json = html.escape(JsonEngine.dump_any(value, ensure_ascii=False, default=str))
        return f'<span class="value">{fallback_json}</span>'

    def render_value(value: Any) -> str:
        if isinstance(value, dict):
            if not value:
                return '<span class="object">{}</span>'
            items = list(value.items())
            lines: list[str] = ['<span class="object">{<ol>']
            for idx, (key, item_value) in enumerate(items):
                key_json = html.escape(JsonEngine.dump_any(str(key), ensure_ascii=False))
                comma = "," if idx < len(items) - 1 else ""
                lines.append(
                    "<li>"
                    + f'<span class="name">{key_json}</span>: '
                    + render_value(item_value)
                    + comma
                    + "</li>"
                )
            lines.append("</ol>}</span>")
            return "".join(lines)

        if isinstance(value, list):
            if not value:
                return '<span class="array">[]</span>'
            lines = ['<span class="array">[<ol>']
            for idx, item in enumerate(value):
                comma = "," if idx < len(value) - 1 else ""
                lines.append("<li>" + render_value(item) + comma + "</li>")
            lines.append("</ol>]</span>")
            return "".join(lines)

        return render_scalar(value)

    try:
        parsed = JsonEngine.parse_any(json_str)
        rendered = render_value(parsed)
    except (ValueError, TypeError, KeyError):
        rendered = f"<pre>{html.escape(json_str)}</pre>"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>NMOS API - {html.escape(request_path)}</title>
<style>
body {{ font-family: monospace; background: #1e1e1e; color: #d4d4d4; padding: 20px; margin: 0; }}
a {{ color: #569cd6; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
h2 {{ color: #9cdcfe; font-size: 16px; margin-bottom: 10px; }}
ol {{ list-style: none; padding-left: 20px; margin: 0; }}
li {{ line-height: 1.4; }}
.object, .array {{ }}
.name {{ color: #9cdcfe; }}
.value {{ }}
.string {{ color: #ce9178; }}
.number {{ color: #b5cea8; }}
.null {{ color: #569cd6; }}
.bool {{ color: #569cd6; }}
.boolean {{ color: #569cd6; }}
pre {{ white-space: pre-wrap; word-wrap: break-word; font-size: 14px; line-height: 1.5; }}
</style>
</head>
<body>
<h2>{html.escape(request_path)}</h2>
{rendered}
</body>
</html>"""


# ---------------------------------------------------------------------------
# JSON / HTML response (auto-detect from Accept header)
# ---------------------------------------------------------------------------

def json_response(
    data: Any,
    status: int = 200,
    no_store: bool = False,
    request: web.Request | None = None,
) -> web.Response:
    """Create a JSON or HTML response based on Accept header.

    If the client sends Accept: text/html (browser), returns an HTML page
    with clickable links. Otherwise returns plain JSON.
    """
    json_str = JsonEngine.dump_any(data, indent=2, ensure_ascii=False, default=str)

    if request is not None and _wants_html(request):
        html_body = _json_to_html(json_str, str(request.path))
        headers = _add_cors({})
        if no_store:
            headers["Cache-Control"] = "public, no-store"
        return web.Response(text=html_body, status=status, headers=headers,
                            content_type="text/html", charset="utf-8")

    headers = _add_cors({})
    if no_store:
        headers["Cache-Control"] = "public, no-store"
    # Use body= to avoid aiohttp adding charset to application/json
    return web.Response(body=json_str.encode("utf-8"), status=status, headers=headers,
                        content_type="application/json")


def json_response_raw(
    json_str: str,
    status: int = 200,
    no_store: bool = False,
    request: web.Request | None = None,
) -> web.Response:
    """Create a JSON or HTML response from a pre-encoded string.

    If the string contains HTML tags (from JsonEngine generate_html mode),
    wraps it in an HTML page. Otherwise returns plain JSON.
    """
    is_html = request is not None and _wants_html(request)

    if is_html and request is not None:
        # The engine's generate_html output has <span>/<ol>/<li> tags
        # Wrap in a full HTML page with CSS
        html_body = _wrap_engine_html(json_str, str(request.path))
        headers = _add_cors({})
        if no_store:
            headers["Cache-Control"] = "public, no-store"
        return web.Response(text=html_body, status=status, headers=headers,
                            content_type="text/html", charset="utf-8")

    headers = _add_cors({})
    if no_store:
        headers["Cache-Control"] = "public, no-store"
    # Use body= to avoid aiohttp adding charset to application/json
    return web.Response(body=json_str.encode("utf-8"), status=status, headers=headers,
                        content_type="application/json")


# ---------------------------------------------------------------------------
# Error response (NMOS format)
# ---------------------------------------------------------------------------

def error_response(
    status: int,
    debug: str = "",
    headers: dict[str, str] | None = None,
    request: web.Request | None = None,
) -> web.Response:
    """Create an NMOS error response."""
    from http import HTTPStatus
    try:
        status_text = HTTPStatus(status).phrase
    except ValueError:
        status_text = "Unknown Error"

    body = {"code": status, "error": status_text, "debug": debug}
    json_str = JsonEngine.dump_any(body, indent=2)

    if request is not None and _wants_html(request):
        html_body = _json_to_html(json_str, str(request.path))
        response_headers = _add_cors({})
        if headers:
            response_headers.update(headers)
        return web.Response(text=html_body, status=status, headers=response_headers,
                            content_type="text/html", charset="utf-8")

    response_headers = _add_cors({})
    if headers:
        response_headers.update(headers)
    return web.Response(body=json_str.encode("utf-8"), status=status,
                        headers=response_headers, content_type="application/json")


# ---------------------------------------------------------------------------
# OPTIONS response (for CORS preflight)
# ---------------------------------------------------------------------------

async def options_response(request: web.Request) -> web.Response:
    """Handle CORS preflight OPTIONS request."""
    headers = dict(CORS_HEADERS)
    req_headers = request.headers.get("Access-Control-Request-Headers", "")
    if req_headers:
        headers["Access-Control-Allow-Headers"] = req_headers
    return web.Response(status=200, headers=headers)


# ---------------------------------------------------------------------------
# SDP response
# ---------------------------------------------------------------------------

def sdp_response(sdp_text: str) -> web.Response:
    """Create an SDP transport file response."""
    headers = _add_cors({})
    return web.Response(body=sdp_text.encode("utf-8"), status=200, headers=headers,
                        content_type="application/sdp")


# ---------------------------------------------------------------------------
# Status-only response
# ---------------------------------------------------------------------------

def status_response(status: int = 200) -> web.Response:
    """Create a header-only response (no body)."""
    headers = _add_cors({})
    return web.Response(status=status, headers=headers)
