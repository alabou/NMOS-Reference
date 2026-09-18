// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Building responses: JSON, the browsing view, CORS and NMOS errors.
//!
//! Port of `nmos/api/response.py`'s response constructors. The browsing view
//! itself lives in [`crate::browse`]; this is what decides when to use it and
//! what headers go out with it.
//!
//! # Indentation is decided by who is asking
//!
//! A browser sending `Accept: text/html` is a human reading a page, and the
//! renderer needs an indented string anyway. Everything else is a machine, for
//! which indentation is pure overhead in bytes and CPU.
//!
//! Python has a sharper reason than taste: `indent` disables CPython's C
//! encoder outright -- `json/encoder.py` selects `c_make_encoder` only when
//! `self.indent is None` -- so a pretty-printed response is built by the pure
//! Python encoder. Measured on a Node resource, 50.4 us pretty against 14.7 us
//! compact, plus 25% more bytes. Rust has no such cliff, but the split is kept
//! because the *output* is a contract and the bandwidth argument stands.
//!
//! # Serving bytes that were never re-encoded
//!
//! [`json_body`] takes JSON that is already encoded and writes it
//! verbatim. That is what lets the registry return a resource exactly as it was
//! registered rather than a re-rendering of its parsed form -- the byte-fidelity
//! guarantee -- and it is also markedly cheaper: Python measured ~2.2 ms to
//! encode a 500-resource page against ~0.26 ms to join 500 pre-encoded
//! fragments, and the gap widens with page size because the encoder walks every
//! value while the join walks none.
//!
//! The HTML branch is deliberately the slow one: it parses the fragments back
//! so that the pretty-printer and the link resolver work on real values. A
//! browser is one human reading one page, and correct links matter more there
//! than the microseconds.
//!
//! # Content type carries no charset
//!
//! `application/json` and nothing else. Python goes out of its way to stop
//! aiohttp appending `; charset=utf-8` (it passes `body=` rather than `text=`),
//! because RFC 8259 gives JSON no charset parameter. The browsing view is the
//! exception and does declare `charset=utf-8`, as `text/html` requires.

use axum::body::Body;
use axum::http::{HeaderMap, HeaderName, HeaderValue, StatusCode, header};
use axum::response::Response;

use nmos_registry_core::links::LinkResolver;

use crate::browse::json_to_html;

/// Headers applied to **every** response, not only to preflight.
///
/// Python decorates every response including 2xx, and registers `OPTIONS` as a
/// real route per path. `tower_http::CorsLayer` would intercept `OPTIONS` as
/// preflight and shadow those routes -- so an unknown path would be answered
/// rather than 404 -- which is why this is hand-rolled. See [`options`] for
/// what preflight does and does not narrow.
pub const CORS_HEADERS: &[(&str, &str)] = &[
    ("access-control-allow-origin", "*"),
    (
        "access-control-allow-methods",
        "GET, PUT, POST, PATCH, HEAD, OPTIONS, DELETE",
    ),
    (
        "access-control-allow-headers",
        "Content-Type, Accept, Authorization, PEP-Exclusive-Authorization",
    ),
    ("access-control-max-age", "3600"),
    ("vary", "Origin"),
];

/// Whether the client prefers HTML -- that is, whether it is a browser.
///
/// Substring rather than a parsed `Accept`: this matches Python
/// (`response.py:120`), and the distinction it is drawing is "is a human
/// looking at this", for which `q`-value negotiation is more machinery than the
/// question deserves.
#[must_use]
pub fn wants_html(headers: &HeaderMap) -> bool {
    headers
        .get(header::ACCEPT)
        .and_then(|value| value.to_str().ok())
        .is_some_and(|accept| accept.contains("text/html"))
}

/// How a response should be cached.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Caching {
    /// No `Cache-Control` header.
    Default,
    /// `Cache-Control: public, no-store`.
    ///
    /// The spelling is Python's, quirk included: `public` and `no-store`
    /// together are contradictory to a strict reading, and it is reproduced
    /// rather than corrected because it is what goes on the wire today.
    NoStore,
}

impl Caching {
    const fn header(self) -> Option<&'static str> {
        match self {
            Self::Default => None,
            Self::NoStore => Some("public, no-store"),
        }
    }
}

/// Start a response with the CORS headers every response carries.
fn base(status: StatusCode, caching: Caching) -> axum::http::response::Builder {
    let mut builder = Response::builder().status(status);
    for (name, value) in CORS_HEADERS {
        builder = builder.header(*name, *value);
    }
    if let Some(cache) = caching.header() {
        builder = builder.header(header::CACHE_CONTROL, cache);
    }
    builder
}

/// A response is being built from values this module controls, so a header
/// value can only be invalid through a programming error. Rather than panic on
/// the serving path, fall back to a bare 500 -- an empty body is a worse answer
/// than the right one, and a much better answer than a dropped connection.
fn finish(builder: axum::http::response::Builder, body: Body) -> Response {
    builder.body(body).unwrap_or_else(|_| {
        let mut response = Response::new(Body::empty());
        *response.status_mut() = StatusCode::INTERNAL_SERVER_ERROR;
        response
    })
}

/// A JSON response, or the browsing view when the client is a browser.
#[must_use]
pub fn json(
    status: StatusCode,
    text: String,
    caching: Caching,
    request: Option<&RequestView<'_>>,
) -> Response {
    json_with_resolver(status, text, caching, request, None)
}

/// Serve a **collection** of already-encoded JSON, without re-encoding it.
///
/// Always an array: with one member, with none, always. Python draws the same
/// line by the caller's *type* rather than by length -- `json_body_response`
/// wraps a `list[str]` and passes a bare `str` through -- so a one-resource
/// collection is `[{...}]` while a single resource is `{...}`.
///
/// Collapsing the first into the second is a real bug and an invisible one: it
/// changes the shape of every `GET /senders` that happens to hold exactly one
/// Sender, and a fixture with two resources never sees it. A single resource
/// does not come through here at all -- it is served with [`json`] or
/// [`json_with_resolver`] from its stored text.
#[must_use]
pub fn json_body(
    status: StatusCode,
    fragments: &[&str],
    caching: Caching,
    request: Option<&RequestView<'_>>,
) -> Response {
    let text = join_fragments(fragments);
    if request.is_some_and(|view| view.wants_html) {
        // The slow branch on purpose -- see the module docs.
        return json(status, text, caching, request);
    }
    finish(
        base(status, caching).header(header::CONTENT_TYPE, "application/json"),
        Body::from(text),
    )
}

/// `[a,b,c]`, in one allocation sized up front.
///
/// One allocation and N memcpys, rather than the repeated growth a naive
/// `join` would do: a 500-resource page is the common case on the Query API.
#[must_use]
pub fn join_fragments(fragments: &[&str]) -> String {
    let payload: usize = fragments.iter().map(|fragment| fragment.len()).sum();
    // `[` + fragments + one `,` between each + `]`.
    let capacity = payload
        .saturating_add(fragments.len().saturating_add(1))
        .saturating_add(1);
    let mut out = String::with_capacity(capacity);
    out.push('[');
    for (index, fragment) in fragments.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        out.push_str(fragment);
    }
    out.push(']');
    out
}

/// An NMOS error response.
///
/// The body is `{"code", "error", "debug"}`, indented by two, where `error` is
/// the HTTP reason phrase and `debug` is the message. `debug` is an observable
/// part of the API: `handlers_registration.py:158` puts a decode exception's
/// text straight into it.
#[must_use]
pub fn error(
    status: StatusCode,
    debug: &str,
    extra: &[(HeaderName, HeaderValue)],
    request: Option<&RequestView<'_>>,
) -> Response {
    let text = error_body(status, debug);

    let mut builder = if request.is_some_and(|view| view.wants_html) {
        base(status, Caching::Default).header(header::CONTENT_TYPE, "text/html; charset=utf-8")
    } else {
        base(status, Caching::Default).header(header::CONTENT_TYPE, "application/json")
    };
    for (name, value) in extra {
        builder = builder.header(name, value);
    }

    let body = match request.filter(|view| view.wants_html) {
        Some(view) => json_to_html(&text, view.path, None),
        None => text,
    };
    finish(builder, Body::from(body))
}

/// The error body, indented by two the way `dump_any(body, indent=2)` is.
///
/// Built by hand rather than through a serializer because the member order is
/// fixed by the Python dict literal (`code`, `error`, `debug`) and because the
/// whole document is three members -- a serializer would be more machinery than
/// the string.
#[must_use]
pub fn error_body(status: StatusCode, debug: &str) -> String {
    let phrase = reason_phrase(status);
    let debug_json = nmos_json::engine::dump_any(&debug).unwrap_or_else(|_| "\"\"".to_owned());
    format!(
        "{{\n  \"code\": {},\n  \"error\": \"{phrase}\",\n  \"debug\": {debug_json}\n}}",
        status.as_u16(),
    )
}

/// The reason phrase, as `http.HTTPStatus(status).phrase` spells it.
///
/// `"Unknown Error"` for a code Python's enum does not know, which is what its
/// `ValueError` branch produces. Deliberately **not** `StatusCode`'s own
/// `canonical_reason`: that returns `None` for unknown codes but also differs
/// in wording from CPython on a few (`http` says "Request Entity Too Large"
/// where CPython says "Payload Too Large"), and this string is in the response
/// body rather than only the status line.
#[must_use]
pub fn reason_phrase(status: StatusCode) -> &'static str {
    match status.as_u16() {
        100 => "Continue",
        101 => "Switching Protocols",
        102 => "Processing",
        103 => "Early Hints",
        200 => "OK",
        201 => "Created",
        202 => "Accepted",
        203 => "Non-Authoritative Information",
        204 => "No Content",
        205 => "Reset Content",
        206 => "Partial Content",
        207 => "Multi-Status",
        208 => "Already Reported",
        226 => "IM Used",
        300 => "Multiple Choices",
        301 => "Moved Permanently",
        302 => "Found",
        303 => "See Other",
        304 => "Not Modified",
        305 => "Use Proxy",
        307 => "Temporary Redirect",
        308 => "Permanent Redirect",
        400 => "Bad Request",
        401 => "Unauthorized",
        402 => "Payment Required",
        403 => "Forbidden",
        404 => "Not Found",
        405 => "Method Not Allowed",
        406 => "Not Acceptable",
        407 => "Proxy Authentication Required",
        408 => "Request Timeout",
        409 => "Conflict",
        410 => "Gone",
        411 => "Length Required",
        412 => "Precondition Failed",
        413 => "Request Entity Too Large",
        414 => "Request-URI Too Long",
        415 => "Unsupported Media Type",
        416 => "Requested Range Not Satisfiable",
        417 => "Expectation Failed",
        418 => "I'm a Teapot",
        421 => "Misdirected Request",
        422 => "Unprocessable Entity",
        423 => "Locked",
        424 => "Failed Dependency",
        425 => "Too Early",
        426 => "Upgrade Required",
        428 => "Precondition Required",
        429 => "Too Many Requests",
        431 => "Request Header Fields Too Large",
        451 => "Unavailable For Legal Reasons",
        500 => "Internal Server Error",
        501 => "Not Implemented",
        502 => "Bad Gateway",
        503 => "Service Unavailable",
        504 => "Gateway Timeout",
        505 => "HTTP Version Not Supported",
        506 => "Variant Also Negotiates",
        507 => "Insufficient Storage",
        508 => "Loop Detected",
        510 => "Not Extended",
        511 => "Network Authentication Required",
        _ => "Unknown Error",
    }
}

/// A body-less response carrying only the status and the CORS headers.
#[must_use]
pub fn status_only(status: StatusCode) -> Response {
    finish(base(status, Caching::Default), Body::empty())
}

/// A CORS preflight answer.
///
/// Port of `options_response` (`response.py:538-544`), and it does less than
/// one might expect: the blanket `CORS_HEADERS`, with
/// `Access-Control-Allow-Headers` echoed back from the request's
/// `Access-Control-Request-Headers` when the client sent one. It sets no
/// `Allow` header and does **not** narrow the advertised method list to what
/// the path actually serves.
///
/// > **Note on the port plan.** The plan says Python "registers `OPTIONS` as a
/// > real route per path so `Allow` is exact". It registers per path, but the
/// > handler is this one and the methods it advertises are the same blanket
/// > list everywhere. What per-path registration buys is that `OPTIONS` is
/// > answered only on paths that exist, so an unknown path still 404s instead
/// > of being swallowed by a blanket preflight layer -- which remains the
/// > reason not to use `tower_http::CorsLayer`. Behaviour here follows the
/// > Python, not the plan's description of it.
#[must_use]
pub fn options(requested_headers: Option<&HeaderValue>) -> Response {
    let mut builder = Response::builder().status(StatusCode::OK);
    for (name, value) in CORS_HEADERS {
        // Echoing the client's requested headers replaces the default list
        // rather than adding to it, so the header is written once.
        if *name == "access-control-allow-headers"
            && let Some(requested) = requested_headers
        {
            builder = builder.header(*name, requested);
            continue;
        }
        builder = builder.header(*name, *value);
    }
    finish(builder, Body::empty())
}

/// What the response builders need to know about the request.
///
/// A small struct rather than the whole `Request`, so that a handler can decide
/// the shape of its answer without the response layer being able to read a
/// body or a header it has no business reading.
#[derive(Debug, Clone, Copy)]
pub struct RequestView<'a> {
    /// The path **as the client sent it**, not normalised.
    ///
    /// Echoed in the browsing view's heading and used as the base for every
    /// relative link on the page, so rewriting it retargets the page. It is
    /// also why the trailing-slash middleware was dropped in favour of
    /// registering both spellings.
    pub path: &'a str,
    /// Whether this client wants the browsing view.
    pub wants_html: bool,
}

impl<'a> RequestView<'a> {
    /// Derive a view from a path and the request's headers.
    #[must_use]
    pub fn new(path: &'a str, headers: &HeaderMap) -> Self {
        Self {
            path,
            wants_html: wants_html(headers),
        }
    }
}

/// [`json_body`], with a link resolver for the browsing view.
///
/// The fragments are still written verbatim for a JSON client; the resolver is
/// consulted only on the browsing branch, which parses them back.
#[must_use]
pub fn json_body_with_resolver(
    status: StatusCode,
    fragments: &[&str],
    caching: Caching,
    request: Option<&RequestView<'_>>,
    resolver: Option<&LinkResolver>,
) -> Response {
    let text = join_fragments(fragments);
    if request.is_some_and(|view| view.wants_html) {
        return json_with_resolver(status, text, caching, request, resolver);
    }
    finish(
        base(status, caching).header(header::CONTENT_TYPE, "application/json"),
        Body::from(text),
    )
}

/// [`json`], with a link resolver for the browsing view.
///
/// The resolver is ignored for JSON responses. It exists so that a named
/// cross-reference -- a Sender's `flow_id`, say -- links into `/flows/` rather
/// than into the collection being browsed, which is the one thing the generic
/// UUID rule cannot know.
#[must_use]
pub fn json_with_resolver(
    status: StatusCode,
    text: String,
    caching: Caching,
    request: Option<&RequestView<'_>>,
    resolver: Option<&LinkResolver>,
) -> Response {
    match request.filter(|view| view.wants_html) {
        Some(view) => finish(
            base(status, caching).header(header::CONTENT_TYPE, "text/html; charset=utf-8"),
            Body::from(json_to_html(&text, view.path, resolver)),
        ),
        None => finish(
            base(status, caching).header(header::CONTENT_TYPE, "application/json"),
            Body::from(text),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn accept(value: &str) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert(
            header::ACCEPT,
            HeaderValue::from_str(value).expect("a test header"),
        );
        headers
    }

    fn body_of(response: Response) -> String {
        // The bodies here are built from `String`, so they are always whole.
        let bytes = futures_lite_block_on(response.into_body());
        String::from_utf8(bytes).expect("a response body this module built is UTF-8")
    }

    fn futures_lite_block_on(body: Body) -> Vec<u8> {
        tokio::runtime::Builder::new_current_thread()
            .build()
            .expect("a current-thread runtime")
            .block_on(async {
                axum::body::to_bytes(body, usize::MAX)
                    .await
                    .expect("a complete body")
                    .to_vec()
            })
    }

    // -- content negotiation -----------------------------------------------

    #[test]
    fn a_browser_is_recognised_by_a_substring_of_accept() {
        assert!(wants_html(&accept("text/html")));
        assert!(wants_html(&accept(
            "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        )));
        assert!(!wants_html(&accept("application/json")));
        assert!(!wants_html(&accept("*/*")));
        assert!(!wants_html(&HeaderMap::new()), "no Accept is not a browser");
    }

    #[test]
    fn json_is_served_without_a_charset() {
        // RFC 8259 gives JSON no charset parameter, and Python goes out of its
        // way to stop aiohttp appending one.
        let response = json(StatusCode::OK, "{}".to_owned(), Caching::Default, None);
        assert_eq!(
            response
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok()),
            Some("application/json"),
        );
    }

    #[test]
    fn the_browsing_view_does_declare_a_charset() {
        let headers = accept("text/html");
        let view = RequestView::new("/x-nmos/", &headers);
        let response = json(
            StatusCode::OK,
            "{\"a\":1}".to_owned(),
            Caching::Default,
            Some(&view),
        );
        assert_eq!(
            response
                .headers()
                .get(header::CONTENT_TYPE)
                .and_then(|value| value.to_str().ok()),
            Some("text/html; charset=utf-8"),
        );
        assert!(body_of(response).starts_with("<!DOCTYPE html>"));
    }

    // -- CORS --------------------------------------------------------------

    #[test]
    fn every_response_carries_cors_not_only_preflight() {
        // Python decorates every response including 2xx, which is why this is
        // hand-rolled rather than a CorsLayer.
        for response in [
            json(StatusCode::OK, "{}".to_owned(), Caching::Default, None),
            error(StatusCode::NOT_FOUND, "gone", &[], None),
            status_only(StatusCode::NO_CONTENT),
            options(None),
        ] {
            assert_eq!(
                response
                    .headers()
                    .get("access-control-allow-origin")
                    .and_then(|value| value.to_str().ok()),
                Some("*"),
                "a response went out without CORS",
            );
        }
    }

    #[test]
    fn preflight_advertises_the_blanket_method_list() {
        // Python does NOT narrow this per path and sets no `Allow` header --
        // see the note on `options`. Reproduced, not corrected.
        let response = options(None);
        assert!(
            response.headers().get(header::ALLOW).is_none(),
            "Python's preflight sets no Allow header",
        );
        assert_eq!(
            response
                .headers()
                .get("access-control-allow-methods")
                .and_then(|value| value.to_str().ok()),
            Some("GET, PUT, POST, PATCH, HEAD, OPTIONS, DELETE"),
        );
    }

    #[test]
    fn preflight_echoes_the_headers_the_client_asked_about() {
        // `response.py:541-542` -- the one thing preflight does adapt.
        let requested = HeaderValue::from_static("X-Custom, Authorization");
        let response = options(Some(&requested));
        let allow: Vec<&str> = response
            .headers()
            .get_all("access-control-allow-headers")
            .iter()
            .filter_map(|value| value.to_str().ok())
            .collect();
        assert_eq!(
            allow,
            ["X-Custom, Authorization"],
            "the echoed list must replace the default, not be added beside it",
        );
    }

    // -- caching -----------------------------------------------------------

    #[test]
    fn no_store_is_spelled_the_way_python_spells_it() {
        let response = json(StatusCode::OK, "{}".to_owned(), Caching::NoStore, None);
        assert_eq!(
            response
                .headers()
                .get(header::CACHE_CONTROL)
                .and_then(|value| value.to_str().ok()),
            Some("public, no-store"),
        );
        let plain = json(StatusCode::OK, "{}".to_owned(), Caching::Default, None);
        assert!(plain.headers().get(header::CACHE_CONTROL).is_none());
    }

    // -- error bodies ------------------------------------------------------

    #[test]
    fn the_error_body_is_indented_by_two_in_the_order_python_writes_it() {
        // Captured from CPython:
        //   '{\n  "code": 400,\n  "error": "Bad Request",\n  "debug": "x"\n}'
        assert_eq!(
            error_body(StatusCode::BAD_REQUEST, "x"),
            "{\n  \"code\": 400,\n  \"error\": \"Bad Request\",\n  \"debug\": \"x\"\n}",
        );
    }

    #[test]
    fn an_empty_debug_is_still_present() {
        assert_eq!(
            error_body(StatusCode::NOT_FOUND, ""),
            "{\n  \"code\": 404,\n  \"error\": \"Not Found\",\n  \"debug\": \"\"\n}",
        );
    }

    #[test]
    fn a_debug_message_is_json_escaped_but_not_ascii_escaped() {
        // `dump_any` defaults to `ensure_ascii=False`, so non-ASCII goes out as
        // itself. Measured against CPython rather than assumed -- the opposite
        // was assumed first, and was wrong.
        assert_eq!(
            error_body(StatusCode::BAD_REQUEST, "café"),
            "{\n  \"code\": 400,\n  \"error\": \"Bad Request\",\n  \"debug\": \"café\"\n}",
        );
        assert_eq!(
            error_body(StatusCode::BAD_REQUEST, r#"quote " and \ back"#),
            "{\n  \"code\": 400,\n  \"error\": \"Bad Request\",\n  \
             \"debug\": \"quote \\\" and \\\\ back\"\n}",
        );
    }

    #[test]
    fn the_reason_phrase_follows_cpython_including_its_unknown_case() {
        // Captured from `http.HTTPStatus`.
        for (code, phrase) in [
            (200_u16, "OK"),
            (201, "Created"),
            (204, "No Content"),
            (400, "Bad Request"),
            (403, "Forbidden"),
            (404, "Not Found"),
            (405, "Method Not Allowed"),
            (409, "Conflict"),
            (500, "Internal Server Error"),
            (501, "Not Implemented"),
            (503, "Service Unavailable"),
        ] {
            let status = StatusCode::from_u16(code).expect("a real status");
            assert_eq!(reason_phrase(status), phrase, "{code}");
        }
        let unknown = StatusCode::from_u16(599).expect("599 is a valid status code");
        assert_eq!(reason_phrase(unknown), "Unknown Error");
    }

    #[test]
    fn an_error_can_carry_extra_headers() {
        // `MutationUnavailable` answers 503 with `Retry-After`.
        let response = error(
            StatusCode::SERVICE_UNAVAILABLE,
            "no leader",
            &[(
                HeaderName::from_static("retry-after"),
                HeaderValue::from_static("5"),
            )],
            None,
        );
        assert_eq!(
            response
                .headers()
                .get("retry-after")
                .and_then(|value| value.to_str().ok()),
            Some("5"),
        );
        assert_eq!(response.status(), StatusCode::SERVICE_UNAVAILABLE);
    }

    #[test]
    fn an_error_renders_as_a_page_for_a_browser() {
        let headers = accept("text/html");
        let view = RequestView::new("/x-nmos/query/v1.3/nodes/missing", &headers);
        let response = error(StatusCode::NOT_FOUND, "no such resource", &[], Some(&view));
        let body = body_of(response);
        assert!(body.starts_with("<!DOCTYPE html>"));
        assert!(body.contains("no such resource"), "{body}");
        assert!(
            body.contains("<h2>/x-nmos/query/v1.3/nodes/missing</h2>"),
            "the page must echo the path the client sent",
        );
    }

    // -- fragments ---------------------------------------------------------

    #[test]
    fn a_one_member_collection_is_still_an_array() {
        // The bug this replaced: a shortcut that served one fragment bare made
        // `GET /senders` answer with an object whenever exactly one Sender was
        // registered.
        assert_eq!(join_fragments(&[r#"{"id":"a"}"#]), r#"[{"id":"a"}]"#);
    }

    #[test]
    fn several_fragments_become_one_array_with_no_whitespace() {
        assert_eq!(
            join_fragments(&[r#"{"id":"a"}"#, r#"{"id":"b"}"#]),
            r#"[{"id":"a"},{"id":"b"}]"#,
        );
    }

    #[test]
    fn no_fragments_is_an_empty_array() {
        // An empty collection is `[]`, not `""` -- a Query API GET over a type
        // with nothing registered must still be valid JSON.
        assert_eq!(join_fragments(&[]), "[]");
    }

    #[test]
    fn fragments_are_written_verbatim() {
        // The byte-fidelity guarantee: a spelling a parse would normalise must
        // not be respelled on the way out. The escape is built character by
        // character rather than written literally -- a source file holding the
        // character itself would pass against a registry that folds escapes,
        // which is the failure this is for.
        let escape: String = ['\\', 'u', '0', '0', 'e', '9'].iter().collect();
        let stored = format!(r#"{{"label":"caf{escape}","n":1e3}}"#);
        let response = json_body(StatusCode::OK, &[&stored], Caching::Default, None);
        let body = body_of(response);
        assert_eq!(body, format!("[{stored}]"));
        assert!(body.contains(&escape), "the escape was folded: {body}");
        assert!(body.contains("1e3"), "the exponent was normalised: {body}");
    }

    #[test]
    fn a_single_resource_is_served_bare_through_the_other_door() {
        // `GET /senders/{id}` uses `json`, not `json_body`, so its shape stays
        // an object rather than a one-member array.
        let stored = r#"{"id":"a"}"#;
        let response = json(StatusCode::OK, stored.to_owned(), Caching::Default, None);
        assert_eq!(body_of(response), stored);
    }

    #[test]
    fn a_browser_asking_for_fragments_gets_the_parsed_page() {
        let headers = accept("text/html");
        let view = RequestView::new("/x-nmos/query/v1.3/nodes/", &headers);
        let response = json_body(
            StatusCode::OK,
            &[r#"{"id":"a"}"#, r#"{"id":"b"}"#],
            Caching::Default,
            Some(&view),
        );
        let body = body_of(response);
        assert!(body.starts_with("<!DOCTYPE html>"));
        assert!(body.contains(r#"<span class="array">"#), "{body}");
    }

    // -- status-only -------------------------------------------------------

    #[test]
    fn a_status_only_response_has_no_body() {
        let response = status_only(StatusCode::NO_CONTENT);
        assert_eq!(response.status(), StatusCode::NO_CONTENT);
        assert!(body_of(response).is_empty());
    }
}
