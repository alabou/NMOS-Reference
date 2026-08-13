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
from typing import Any, Callable

from aiohttp import web
from nmos.json.engine import JsonEngine

#: ``(field_name, value) -> href | None``. ``field_name`` is the JSON key
#: the value sits under, or None at the document root / inside an array
#: whose key is unknown. Returning None defers to the generic link rules.
LinkResolver = Callable[[str | None, str], "str | None"]

_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}$"
)
_ABS_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_VERSION_SEGMENT_RE = re.compile(r"^v[0-9]+\.[0-9]+$")
# Path segments that may appear in a relative API reference. A string value is
# turned into a hyperlink only when EVERY one of its segments appears here, so
# a segment that is missing silently renders as plain text -- the index is then
# partially navigable, which is more confusing than no linking at all.
#
# Grouped by the API that owns them so the next API added is less likely to be
# half-covered.
_API_SEGMENTS = {
    # Roots
    "x-nmos",
    "x-manufacturer",
    # IS-04 Node API
    "node",
    "self",
    # IS-04 Registry -- Query API. The six resource collections are plural
    # here, unlike the Node API's singular "node" root, which is why "nodes"
    # has to be listed separately from it.
    "query",
    "nodes",
    "subscriptions",
    # Shared between the Node API and the Query API
    "devices",
    "sources",
    "flows",
    "senders",
    "receivers",
    # IS-04 Registry -- Registration API.
    #
    # Only the version ladder is listed. "resource" and "health" are
    # deliberately absent: the Registration API is write-only, so
    # ``/resource`` answers 405 (POST and OPTIONS only) and ``/health`` 404
    # (the resource is ``/health/nodes/{id}``). They still appear in the base
    # index because registrationapi-base.json mandates it, but rendering them
    # as links would offer the reader two clicks that cannot work.
    "registration",
    # IS-05 Connection API
    "connection",
    "single",
    "staged",
    "active",
    "constraints",
    "transportfile",
    "transporttype",
    # IS-11 Stream Compatibility API
    "streamcompatibility",
    "status",
    "inputs",
    "outputs",
    "supported",
    # x-manufacturer Exclusive Session API
    "exclusive",
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


def _json_to_html(
    json_str: str,
    request_path: str,
    link_resolver: LinkResolver | None = None,
) -> str:
    """Convert a JSON string to an HTML page with clickable links.

    Only absolute URLs and GUID-like resource IDs become hyperlinks.
    Other string values remain plain text.

    Args:
        link_resolver: Optional ``(field_name, value) -> href | None``
            callback consulted before the generic rules. Without it a UUID can
            only be linked into the collection currently being browsed, which
            is wrong for a cross-reference: a Sender's ``flow_id`` would point
            at ``/senders/<flow id>`` and 404. The callback is what lets a
            caller say "``flow_id`` lives under ``/flows/``".

            The Node API solves the same problem inside the JSON engine via
            ``handlers_node._make_link_resolver``, but that path only applies
            when encoding generated types; callers rendering plain dicts (the
            registry's Query API, which serves the JSON exactly as it was
            registered) need it here.
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
        """Is this string a relative link to a child resource?

        Every segment must look like an API segment. One extra condition
        applies to a value with no trailing slash: at least one of its
        segments must be a *named* segment, not merely version-shaped.

        That rules out a lone ``"v1.3"``, which is data rather than a link —
        it is what a Node's ``api.versions`` array contains — while keeping
        the version *index* linkable, because that is written ``"v1.3/"``
        with the slash. Without the distinction, browsing any Node resource
        renders ``api.versions`` as links to
        ``<current collection>/v1.3``, which 404.
        """
        if not value or value.startswith("/") or _ABS_URL_RE.match(value):
            return False
        has_trailing_slash = value.endswith("/")
        path = value[:-1] if has_trailing_slash else value
        segments = [seg for seg in path.split("/") if seg]
        if not segments:
            return False
        if not all(is_api_segment(seg) for seg in segments):
            return False
        if has_trailing_slash:
            return True
        return any(seg in _API_SEGMENTS for seg in segments)

    def resolve_href(raw_value: str, field_name: str | None) -> str | None:
        if _ABS_URL_RE.match(raw_value):
            return raw_value

        # A caller-supplied mapping wins over the generic rules: it is the
        # only thing that knows which collection a named reference targets.
        if link_resolver is not None:
            resolved = link_resolver(field_name, raw_value)
            if resolved is not None:
                return resolved

        guid = raw_value[:-1] if raw_value.endswith("/") else raw_value
        if _UUID_RE.match(guid):
            # A resolver, once supplied, is authoritative for UUIDs: having
            # declined this one, fall through to plain text rather than
            # guessing.
            #
            # The generic guess is "same collection as the page being
            # browsed", which is right only for a resource's own id. For any
            # other reference it invents a link that 404s -- a BCP-008 monitor
            # Source carries a ``monitor_sibling_id`` naming a *Sender*, so
            # browsing /sources/ would offer /sources/<sender id>. An
            # unlinked value is a smaller failure than a confident wrong one.
            if link_resolver is not None:
                return None
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

    def render_scalar(value: Any, field_name: str | None = None) -> str:
        if isinstance(value, str):
            value_json = html.escape(JsonEngine.dump_any(value, ensure_ascii=False))
            href = resolve_href(value, field_name)
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

    def render_value(value: Any, field_name: str | None = None) -> str:
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
                    + render_value(item_value, str(key))
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
                # Array elements inherit the array's own key, so ``parents``
                # and the deprecated ``senders`` / ``receivers`` arrays of
                # UUIDs resolve like the named reference they are.
                lines.append(
                    "<li>" + render_value(item, field_name) + comma + "</li>",
                )
            lines.append("</ol>]</span>")
            return "".join(lines)

        return render_scalar(value, field_name)

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
    link_resolver: LinkResolver | None = None,
) -> web.Response:
    """Create a JSON or HTML response based on Accept header.

    If the client sends Accept: text/html (browser), returns an HTML page
    with clickable links. Otherwise returns plain JSON.

    Args:
        link_resolver: Optional per-field link mapping used only for the HTML
            rendering; see ``_json_to_html``. Ignored for JSON responses.
    """
    # Indentation is decided by WHO is asking, because it is not free.
    #
    # `indent` disables CPython's C encoder outright -- json/encoder.py only
    # selects `c_make_encoder` when `self.indent is None` -- so a pretty-printed
    # response is built by the pure-Python encoder instead. Measured on a Node
    # resource: 50.4 us pretty vs 14.7 us compact, a 3.4x cost on every single
    # response, plus 25% more bytes on the wire.
    #
    # A browser asking for text/html is a human reading the page, and the HTML
    # renderer needs the indented string anyway. Everything else is a machine,
    # for which indentation is pure overhead in both CPU and bandwidth.
    if request is not None and _wants_html(request):
        json_str = JsonEngine.dump_any(
            data, indent=2, ensure_ascii=False, default=str,
        )
        html_body = _json_to_html(json_str, str(request.path), link_resolver)
        headers = _add_cors({})
        if no_store:
            headers["Cache-Control"] = "public, no-store"
        return web.Response(text=html_body, status=status, headers=headers,
                            content_type="text/html", charset="utf-8")

    json_str = JsonEngine.dump_any(data, ensure_ascii=False, default=str)

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
