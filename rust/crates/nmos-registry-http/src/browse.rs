// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The browsing view: JSON rendered as a navigable HTML page.
//!
//! Port of `_json_to_html` (`nmos/api/response.py:158-366`). When a browser
//! sends `Accept: text/html` the registry serves the same data as an indented
//! page whose references are clickable, which is how a person walks the
//! registry without a client.
//!
//! # Why this is string building rather than a template engine
//!
//! `test_html_links.py` asserts exact `<a href>` targets and exact span
//! classes. A template engine would put a layer of its own escaping and
//! whitespace policy between the code and those assertions, and every
//! difference would be a test failure with no behavioural meaning. The markup
//! is small and entirely determined by the data, so it is built directly.
//!
//! # Key order, and the one place the workspace pays for it
//!
//! `serde_json/preserve_order` is deliberately **not** enabled: it makes
//! `Value`'s object an `IndexMap` and costs about 25% on every parse in the
//! workspace, on a path where parsing dominates. Nothing else needs document
//! order -- decode looks members up by name, stored bodies are served back as
//! their original text, and grains splice `RawValue`.
//!
//! This renderer is the exception, because a page whose keys come out in a
//! different order than the document is a different page. So it parses into its
//! own ordered representation ([`Ordered`]) instead of taxing every other
//! parse in the workspace.
//!
//! # What becomes a link, and why the rules are this cautious
//!
//! Only absolute URLs, UUID-shaped values and relative API references. A
//! string is turned into a link only when **every** one of its segments is a
//! known API segment, because a partially navigable index is more confusing
//! than an unlinked one.
//!
//! A supplied [`LinkResolver`] is authoritative for UUIDs: having declined one,
//! this falls through to plain text rather than guessing. The generic guess is
//! "the same collection as the page being browsed", which is right only for a
//! resource's own id -- a BCP-008 monitor Source carries a
//! `monitor_sibling_id` naming a *Sender*, so browsing `/sources/` would offer
//! `/sources/<sender id>`, which 404s. An unlinked value is a smaller failure
//! than a confident wrong one.

use std::fmt::Write as _;
use std::sync::LazyLock;

use indexmap::IndexMap;
use regex::Regex;

use nmos_registry_core::links::LinkResolver;

use crate::escape::escape;

// The panic-free lints exist to keep the *write path* from leaving a
// half-applied store behind a lock that does not poison. A literal pattern that
// fails to compile is a different animal: it is a build-time bug, discovered by
// the first test that renders anything, and there is no fallible-construction
// alternative the `regex` crate offers.
//
// `#[expect]` rather than `#[allow]` on purpose -- it fails the build if the
// lint ever stops firing here, so the exemption cannot quietly outlive its
// reason.
#[expect(
    clippy::expect_used,
    reason = "literal patterns; a failure to compile is a build-time bug, not a runtime condition"
)]
mod patterns {
    use super::{LazyLock, Regex};

    /// `^[a-zA-Z][a-zA-Z0-9+.-]*://`
    pub(super) static ABS_URL: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://").expect("a constant pattern compiles")
    });

    /// `^v[0-9]+\.[0-9]+\Z`
    pub(super) static VERSION_SEGMENT: LazyLock<Regex> =
        LazyLock::new(|| Regex::new(r"^v[0-9]+\.[0-9]+\z").expect("a constant pattern compiles"));

    /// `^[0-9a-fA-F]{8}-...\Z`
    ///
    /// Case-insensitive and not anchored to the canonical form, unlike the
    /// validator that guards registration: this one asks "does this look like
    /// something worth offering a link to", not "is this a legal resource id".
    pub(super) static UUID_SHAPED: LazyLock<Regex> = LazyLock::new(|| {
        Regex::new(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\z",
        )
        .expect("a constant pattern compiles")
    });
}

use patterns::{ABS_URL, UUID_SHAPED, VERSION_SEGMENT};

/// Path segments that may appear in a relative API reference.
///
/// A string value becomes a hyperlink only when **every** one of its segments
/// appears here, so a missing segment renders as plain text rather than making
/// the index half-navigable.
///
/// Grouped by the API that owns them, so the next API added is less likely to
/// be half-covered.
const API_SEGMENTS: &[&str] = &[
    // Roots
    "x-nmos",
    "x-manufacturer",
    // IS-04 Node API
    "node",
    "self",
    // IS-04 Registry -- Query API. The six resource collections are plural
    // here, unlike the Node API's singular "node" root, which is why "nodes"
    // has to be listed separately from it.
    "query",
    "nodes",
    "subscriptions",
    // Shared between the Node API and the Query API
    "devices",
    "sources",
    "flows",
    "senders",
    "receivers",
    // IS-04 Registry -- Registration API.
    //
    // Only the version ladder is listed. "resource" and "health" are
    // deliberately absent: the Registration API is write-only, so `/resource`
    // answers 405 (POST and OPTIONS only) and `/health` 404 (the resource is
    // `/health/nodes/{id}`). They still appear in the base index because
    // registrationapi-base.json mandates it, but rendering them as links would
    // offer the reader two clicks that cannot work.
    "registration",
    // IS-05 Connection API
    "connection",
    "single",
    "staged",
    "active",
    "constraints",
    "transportfile",
    "transporttype",
    // IS-11 Stream Compatibility API
    "streamcompatibility",
    "status",
    "inputs",
    "outputs",
    "supported",
    // x-manufacturer Exclusive Session API
    "exclusive",
    "acquire",
    "renew",
    "release",
    "keepalive",
];

/// A JSON value that remembers the order its object keys arrived in.
///
/// See the module docs for why this exists rather than
/// `serde_json/preserve_order`.
#[derive(Debug, Clone, PartialEq, serde::Deserialize)]
#[serde(untagged)]
pub enum Ordered {
    /// `null`.
    Null,
    /// `true` or `false`.
    ///
    /// Before `Number`, because `serde(untagged)` tries variants in order and
    /// a bool would otherwise be offered to the number parser first.
    Bool(bool),
    /// Any JSON number, kept as `serde_json` parsed it so that integers stay
    /// integers.
    Number(serde_json::Number),
    /// A string.
    Str(String),
    /// An array.
    Array(Vec<Ordered>),
    /// An object, in document order.
    Object(IndexMap<String, Ordered>),
}

impl Ordered {
    /// Parse JSON text, preserving object key order.
    ///
    /// # Errors
    ///
    /// The text is not JSON.
    pub fn parse(text: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(text)
    }
}

/// Render JSON text as a navigable HTML page.
///
/// `request_path` is the path the **client** sent, not a normalised form: it is
/// echoed in the `<h2>` and is the base every relative link is built from, so
/// rewriting it would silently retarget every link on the page.
#[must_use]
pub fn json_to_html(
    json_text: &str,
    request_path: &str,
    resolver: Option<&LinkResolver>,
) -> String {
    let rendered = Ordered::parse(json_text).map_or_else(
        |_| {
            // Not JSON. Python falls back to a <pre> block rather than failing
            // the response, because this is a browsing convenience and the
            // bytes are still what the client asked for.
            format!("<pre>{}</pre>", escape(json_text))
        },
        |parsed| Renderer::new(request_path, resolver).value(&parsed, None),
    );
    page(request_path, &rendered)
}

/// The page shell. Kept byte-identical to Python's, including the stylesheet.
fn page(request_path: &str, body: &str) -> String {
    let title = escape(request_path);
    format!(
        r#"<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>NMOS API - {title}</title>
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
<h2>{title}</h2>
{body}
</body>
</html>"#
    )
}

struct Renderer<'a> {
    /// `request_path` with trailing slashes trimmed, plus one slash. Relative
    /// references hang off this.
    base_path: String,
    /// Where a bare UUID points.
    ///
    /// Not the same as `base_path`: when the page being browsed *is* a
    /// resource, its own id is the last segment, and linking a UUID under it
    /// would produce `/senders/<id>/<id>`. So the last segment is dropped when
    /// it is itself a UUID.
    guid_base_path: String,
    resolver: Option<&'a LinkResolver>,
}

impl<'a> Renderer<'a> {
    fn new(request_path: &str, resolver: Option<&'a LinkResolver>) -> Self {
        let trimmed = request_path.trim_end_matches('/');
        let base_path = format!("{trimmed}/");

        let parts: Vec<&str> = trimmed.split('/').filter(|part| !part.is_empty()).collect();
        let last = parts.last().copied().unwrap_or("");
        let guid_base_path = if UUID_SHAPED.is_match(last) {
            let parent = parts
                .get(..parts.len().saturating_sub(1))
                .unwrap_or(&[])
                .join("/");
            format!("/{parent}/")
        } else {
            base_path.clone()
        };

        Self {
            base_path,
            guid_base_path,
            resolver,
        }
    }

    fn value(&self, value: &Ordered, field: Option<&str>) -> String {
        match value {
            Ordered::Object(members) => self.object(members),
            Ordered::Array(items) => self.array(items, field),
            scalar => self.scalar(scalar, field),
        }
    }

    fn object(&self, members: &IndexMap<String, Ordered>) -> String {
        if members.is_empty() {
            return r#"<span class="object">{}</span>"#.to_owned();
        }
        let last = members.len().saturating_sub(1);
        let mut out = String::from(r#"<span class="object">{<ol>"#);
        for (index, (key, member)) in members.iter().enumerate() {
            let key_json = escape(&dump_string(key));
            let comma = if index < last { "," } else { "" };
            let rendered = self.value(member, Some(key));
            let _ = write!(
                out,
                r#"<li><span class="name">{key_json}</span>: {rendered}{comma}</li>"#,
            );
        }
        out.push_str("</ol>}</span>");
        out
    }

    fn array(&self, items: &[Ordered], field: Option<&str>) -> String {
        if items.is_empty() {
            return r#"<span class="array">[]</span>"#.to_owned();
        }
        let last = items.len().saturating_sub(1);
        let mut out = String::from(r#"<span class="array">[<ol>"#);
        for (index, item) in items.iter().enumerate() {
            let comma = if index < last { "," } else { "" };
            // Array elements inherit the array's own key, so `parents` and the
            // deprecated `senders` / `receivers` arrays of UUIDs resolve like
            // the named reference they are.
            let rendered = self.value(item, field);
            let _ = write!(out, "<li>{rendered}{comma}</li>");
        }
        out.push_str("</ol>]</span>");
        out
    }

    fn scalar(&self, value: &Ordered, field: Option<&str>) -> String {
        match value {
            Ordered::Str(text) => {
                let value_json = escape(&dump_string(text));
                match self.href(text, field) {
                    Some(href) => format!(
                        r#"<span class="value"><a href="{}"><span class="string">{value_json}</span></a></span>"#,
                        escape(&href),
                    ),
                    None => format!(
                        r#"<span class="value"><span class="string">{value_json}</span></span>"#
                    ),
                }
            }
            Ordered::Bool(flag) => format!(
                r#"<span class="value"><span class="boolean">{}</span></span>"#,
                if *flag { "true" } else { "false" },
            ),
            Ordered::Null => {
                r#"<span class="value"><span class="null">null</span></span>"#.to_owned()
            }
            Ordered::Number(number) => format!(
                r#"<span class="value"><span class="number">{}</span></span>"#,
                python_number(number),
            ),
            // Unreachable: `value` routes objects and arrays elsewhere.
            Ordered::Object(_) | Ordered::Array(_) => String::new(),
        }
    }

    fn href(&self, raw: &str, field: Option<&str>) -> Option<String> {
        if ABS_URL.is_match(raw) {
            return Some(raw.to_owned());
        }

        // A caller-supplied mapping wins over the generic rules: it is the only
        // thing that knows which collection a named reference targets.
        if let Some(resolver) = self.resolver
            && let Some(resolved) = resolver.resolve(field, raw)
        {
            return Some(resolved);
        }

        let guid = raw.strip_suffix('/').unwrap_or(raw);
        if UUID_SHAPED.is_match(guid) {
            // A resolver, once supplied, is authoritative for UUIDs: having
            // declined this one, fall through to plain text rather than
            // guessing. See the module docs.
            if self.resolver.is_some() {
                return None;
            }
            return Some(if raw.ends_with('/') {
                format!("{}{guid}/", self.guid_base_path)
            } else {
                format!("{}{guid}", self.guid_base_path)
            });
        }

        if raw.starts_with('/') {
            return if raw.starts_with("/x-nmos/") || raw.starts_with("/x-manufacturer/") {
                Some(raw.to_owned())
            } else {
                None
            };
        }

        is_relative_api_ref(raw).then(|| format!("{}{raw}", self.base_path))
    }
}

/// Whether a segment may appear in a relative API reference.
fn is_api_segment(segment: &str) -> bool {
    UUID_SHAPED.is_match(segment)
        || VERSION_SEGMENT.is_match(segment)
        || API_SEGMENTS.contains(&segment)
}

/// Whether a string is a relative link to a child resource.
///
/// Every segment must look like an API segment. One extra condition applies to
/// a value with no trailing slash: at least one segment must be a *named*
/// segment, not merely version-shaped.
///
/// That rules out a lone `"v1.3"`, which is data rather than a link -- it is
/// what a Node's `api.versions` array contains -- while keeping the version
/// *index* linkable, because that is written `"v1.3/"` with the slash. Without
/// the distinction, browsing any Node resource renders `api.versions` as links
/// to `<current collection>/v1.3`, which 404.
fn is_relative_api_ref(value: &str) -> bool {
    if value.is_empty() || value.starts_with('/') || ABS_URL.is_match(value) {
        return false;
    }
    let has_trailing_slash = value.ends_with('/');
    let path = value.strip_suffix('/').unwrap_or(value);
    let segments: Vec<&str> = path.split('/').filter(|part| !part.is_empty()).collect();
    if segments.is_empty() {
        return false;
    }
    if !segments.iter().copied().all(is_api_segment) {
        return false;
    }
    has_trailing_slash || segments.iter().any(|seg| API_SEGMENTS.contains(seg))
}

/// A number spelled the way Python's `f"{value}"` spells it.
///
/// Not `dump_any`: `render_scalar` interpolates the number directly
/// (`response.py:292`), which is `str()`, not `json.dumps()`. For an integer
/// the two agree; for a float they do not, because `str(float)` is `repr` --
/// `1e-05` rather than `0.00001`, and `1000000.0` rather than `1000000`.
///
/// The distinction is carried by whether the document said `1` or `1.0`, which
/// is exactly what `serde_json::Number` remembers.
fn python_number(number: &serde_json::Number) -> String {
    if let Some(signed) = number.as_i64() {
        return signed.to_string();
    }
    if let Some(unsigned) = number.as_u64() {
        return unsigned.to_string();
    }
    number
        .as_f64()
        .map_or_else(|| number.to_string(), nmos_json::engine::format_repr)
}

/// A JSON string literal, spelled the way `dump_any(s, ensure_ascii=False)` is.
fn dump_string(text: &str) -> String {
    nmos_json::engine::dump_any(&text).unwrap_or_else(|_| format!("\"{text}\""))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn render(json: &str, path: &str) -> String {
        json_to_html(json, path, None)
    }

    // -- key order ---------------------------------------------------------

    #[test]
    fn object_keys_keep_their_document_order() {
        // The whole reason this module parses into its own representation. A
        // BTreeMap would sort these alphabetically and the page would not be
        // the document.
        let html = render(r#"{"zulu":1,"alpha":2,"mike":3}"#, "/x-nmos/");
        let zulu = html.find("zulu").expect("zulu is rendered");
        let alpha = html.find("alpha").expect("alpha is rendered");
        let mike = html.find("mike").expect("mike is rendered");
        assert!(zulu < alpha && alpha < mike, "keys were reordered");
    }

    // -- scalars -----------------------------------------------------------

    #[test]
    fn each_scalar_kind_gets_its_own_span_class() {
        // The classes the stylesheet colours by, and what the tests assert.
        // A string keeps its JSON quotes, and those are then HTML-escaped --
        // `html.escape(quote=True)` is what Python calls, so `"` becomes
        // `&quot;` inside the span. Captured from the Python, not composed.
        assert!(
            render(r#"{"a":"text"}"#, "/")
                .contains(r#"<span class="string">&quot;text&quot;</span>"#)
        );
        assert!(render(r#"{"a":true}"#, "/").contains(r#"<span class="boolean">true</span>"#));
        assert!(render(r#"{"a":false}"#, "/").contains(r#"<span class="boolean">false</span>"#));
        assert!(render(r#"{"a":null}"#, "/").contains(r#"<span class="null">null</span>"#));
        assert!(render(r#"{"a":42}"#, "/").contains(r#"<span class="number">42</span>"#));
    }

    #[test]
    fn a_float_is_spelled_the_way_python_str_spells_it() {
        // `render_scalar` interpolates the number directly (`response.py:292`),
        // which is `str()`, not `json.dumps()`. Measured against CPython:
        //   {"port":8080,"f":1.0,"tiny":1e-05}
        //     -> 8080 ... 1.0 ... 1e-05
        let html = render(r#"{"port":8080,"f":1.0,"tiny":1e-05}"#, "/");
        assert!(
            html.contains(r#"<span class="number">8080</span>"#),
            "{html}"
        );
        assert!(
            html.contains(r#"<span class="number">1.0</span>"#),
            "{html}"
        );
        assert!(
            html.contains(r#"<span class="number">1e-05</span>"#),
            "a float must use Python's repr spelling, not the shortest \
             round-trip one: {html}",
        );
    }

    #[test]
    fn a_bool_is_not_rendered_as_a_number() {
        // `serde(untagged)` tries variants in declaration order, so `Bool`
        // has to precede `Number` -- otherwise `true` would be offered to the
        // number parser first.
        let html = render(r#"{"a":true}"#, "/");
        assert!(!html.contains(r#"class="number""#), "{html}");
    }

    #[test]
    fn an_integer_does_not_acquire_a_decimal_point() {
        let html = render(r#"{"port":8080}"#, "/");
        assert!(html.contains(">8080<"), "{html}");
        assert!(!html.contains("8080.0"), "{html}");
    }

    #[test]
    fn empty_containers_render_without_a_list() {
        assert!(render(r#"{"a":{}}"#, "/").contains(r#"<span class="object">{}</span>"#));
        assert!(render(r#"{"a":[]}"#, "/").contains(r#"<span class="array">[]</span>"#));
    }

    #[test]
    fn commas_separate_all_but_the_last_entry() {
        let html = render(r#"{"a":1,"b":2}"#, "/");
        assert!(
            html.contains("</span>,</li>"),
            "no separating comma: {html}"
        );
        assert!(
            !html.contains("</span>,</li></ol>}"),
            "the last entry has a trailing comma: {html}",
        );
    }

    // -- escaping ----------------------------------------------------------

    #[test]
    fn values_and_keys_are_escaped() {
        let html = render(r#"{"<k>":"<script>"}"#, "/");
        assert!(
            !html.contains("<script>"),
            "markup survived escaping: {html}"
        );
        assert!(html.contains("&lt;script&gt;"), "{html}");
        assert!(html.contains("&lt;k&gt;"), "{html}");
    }

    #[test]
    fn the_request_path_is_escaped_in_the_title_and_heading() {
        let html = render("{}", "/x-nmos/<script>");
        assert_eq!(html.matches("&lt;script&gt;").count(), 2, "title and h2");
        assert!(!html.contains("/<script>"));
    }

    // -- links: absolute ---------------------------------------------------

    #[test]
    fn an_absolute_url_becomes_a_link_to_itself() {
        let html = render(r#"{"href":"https://example.test/a"}"#, "/x-nmos/");
        assert!(
            html.contains(r#"<a href="https://example.test/a">"#),
            "{html}",
        );
    }

    #[test]
    fn a_non_url_string_is_not_a_link() {
        let html = render(r#"{"label":"my sender"}"#, "/x-nmos/query/v1.3/senders/");
        assert!(!html.contains("<a href"), "{html}");
    }

    // -- links: UUIDs ------------------------------------------------------

    #[test]
    fn a_uuid_links_into_the_collection_being_browsed() {
        let html = render(
            r#"{"id":"3b8be755-08ff-452b-b217-c9151eb21193"}"#,
            "/x-nmos/query/v1.3/senders/",
        );
        assert!(
            html.contains(
                r#"<a href="/x-nmos/query/v1.3/senders/3b8be755-08ff-452b-b217-c9151eb21193">"#
            ),
            "{html}",
        );
    }

    #[test]
    fn browsing_a_resource_does_not_nest_its_own_id_under_itself() {
        // Without dropping the trailing UUID segment this produces
        // `/senders/<id>/<id>`, which 404s.
        let html = render(
            r#"{"id":"3b8be755-08ff-452b-b217-c9151eb21193"}"#,
            "/x-nmos/query/v1.3/senders/3b8be755-08ff-452b-b217-c9151eb21193",
        );
        assert!(
            html.contains(
                r#"<a href="/x-nmos/query/v1.3/senders/3b8be755-08ff-452b-b217-c9151eb21193">"#
            ),
            "{html}",
        );
        assert!(
            !html.contains("21193/3b8be755"),
            "the id was nested under itself: {html}",
        );
    }

    #[test]
    fn array_elements_inherit_the_arrays_key() {
        // `parents` and the deprecated `senders`/`receivers` arrays are arrays
        // of UUIDs, and must resolve like the named reference they are.
        let html = render(
            r#"{"parents":["3b8be755-08ff-452b-b217-c9151eb21193"]}"#,
            "/x-nmos/query/v1.3/sources/",
        );
        assert!(
            html.contains("<a href="),
            "an array element lost its key: {html}"
        );
    }

    // -- links: relative ---------------------------------------------------

    #[test]
    fn a_relative_api_reference_hangs_off_the_browsed_path() {
        let html = render(r#"{"a":"nodes/"}"#, "/x-nmos/query/v1.3");
        assert!(
            html.contains(r#"<a href="/x-nmos/query/v1.3/nodes/">"#),
            "{html}",
        );
    }

    #[test]
    fn a_bare_version_is_data_not_a_link() {
        // What a Node's `api.versions` array contains. Linking it produces
        // `<current collection>/v1.3`, which 404s.
        let html = render(r#"{"versions":["v1.3"]}"#, "/x-nmos/query/v1.3/nodes/");
        assert!(
            !html.contains("<a href"),
            "a bare version became a link: {html}"
        );
    }

    #[test]
    fn a_version_index_with_a_slash_is_a_link() {
        let html = render(r#"{"a":"v1.3/"}"#, "/x-nmos/query");
        assert!(html.contains(r#"<a href="/x-nmos/query/v1.3/">"#), "{html}");
    }

    #[test]
    fn a_reference_with_an_unknown_segment_is_not_linked() {
        // Every segment must be known, or the index becomes half-navigable --
        // which is more confusing than no linking at all.
        let html = render(r#"{"a":"nodes/wat/"}"#, "/x-nmos/query/v1.3");
        assert!(!html.contains("<a href"), "{html}");
    }

    #[test]
    fn a_root_relative_path_links_only_under_the_two_api_roots() {
        assert!(
            render(r#"{"a":"/x-nmos/query/v1.3/"}"#, "/")
                .contains(r#"<a href="/x-nmos/query/v1.3/">"#),
        );
        assert!(
            render(r#"{"a":"/x-manufacturer/exclusive/"}"#, "/")
                .contains(r#"<a href="/x-manufacturer/exclusive/">"#),
        );
        assert!(
            !render(r#"{"a":"/etc/passwd"}"#, "/").contains("<a href"),
            "an arbitrary absolute path became a link",
        );
    }

    // -- the resolver ------------------------------------------------------

    #[test]
    fn a_resolver_wins_over_the_generic_uuid_rule() {
        // A Sender's `flow_id` names a Flow, and the generic rule would point
        // it at `/senders/<flow id>`.
        let resolver = LinkResolver::new("/x-nmos/query/v1.3/senders/", "/x-nmos/query/v1.3");
        let html = json_to_html(
            r#"{"flow_id":"3b8be755-08ff-452b-b217-c9151eb21193"}"#,
            "/x-nmos/query/v1.3/senders/",
            Some(&resolver),
        );
        assert!(
            html.contains(
                r#"href="/x-nmos/query/v1.3/flows/3b8be755-08ff-452b-b217-c9151eb21193""#
            ),
            "{html}",
        );
    }

    #[test]
    fn a_resolver_that_declines_a_uuid_leaves_it_unlinked() {
        // Authoritative once supplied: a confident wrong link is worse than no
        // link. A BCP-008 `monitor_sibling_id` on a Source names a Sender.
        let resolver = LinkResolver::new("/x-nmos/query/v1.3/sources/", "/x-nmos/query/v1.3");
        let html = json_to_html(
            r#"{"monitor_sibling_id":"3b8be755-08ff-452b-b217-c9151eb21193"}"#,
            "/x-nmos/query/v1.3/sources/",
            Some(&resolver),
        );
        assert!(
            !html.contains("<a href"),
            "the renderer guessed a collection the resolver declined: {html}",
        );
    }

    // -- malformed input ---------------------------------------------------

    #[test]
    fn text_that_is_not_json_falls_back_to_a_pre_block() {
        // A browsing convenience must not turn into a failed response.
        let html = render("not json at all <b>", "/x-nmos/");
        assert!(
            html.contains("<pre>not json at all &lt;b&gt;</pre>"),
            "{html}"
        );
    }

    // -- the shell ---------------------------------------------------------

    #[test]
    fn the_page_declares_utf8_and_echoes_the_path_the_client_sent() {
        // The path is NOT normalised: it is echoed in the heading and is the
        // base for every relative link, so rewriting it retargets the page.
        let html = render("{}", "/x-nmos/query/v1.3//");
        assert!(html.contains(r#"<meta charset="utf-8">"#));
        assert!(html.contains("<h2>/x-nmos/query/v1.3//</h2>"), "{html}");
    }

    #[test]
    fn non_ascii_survives_as_itself() {
        let html = render(r#"{"label":"café"}"#, "/x-nmos/");
        assert!(html.contains("café"), "{html}");
        assert!(
            !html.contains("caf\\u00e9"),
            "the page re-escaped it: {html}",
        );
    }
}
