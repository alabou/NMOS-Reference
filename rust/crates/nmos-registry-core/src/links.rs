// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Hyperlink resolution for the browsable HTML rendering of the APIs.
//!
//! Both registry APIs answer `Accept: text/html` with a navigable page, so a
//! browser can walk the whole registry by clicking. Making that work needs one
//! piece of knowledge the generic renderer does not have: **which collection a
//! given reference attribute points at**.
//!
//! Without it, every UUID in a document can only be linked into the collection
//! currently being browsed. A Sender's `flow_id` would then link to
//! `/senders/<flow id>` and 404 -- the page looks indexed but the links are
//! wrong, which is worse than no links at all. `device_id`, `source_id` and the
//! rest have the same problem.
//!
//! The Node API solves this inside the JSON engine, but that path only applies
//! while encoding generated types. The registry serves each resource as the raw
//! JSON it was registered with -- deliberately, so vendor extensions survive --
//! so it renders plain values and needs the equivalent mapping here.

use crate::resource_type::ResourceType;

/// Which collection a reference attribute's UUID lives in.
///
/// `id` and `parents` resolve to the collection *being browsed*: a resource's
/// own id addresses itself, and IS-04 constrains `parents` to the same type as
/// the resource holding it -- a Flow's parents are Flows, a Source's are
/// Sources.
///
/// `senders` and `receivers` are the deprecated Device arrays. They are still
/// linked, because a registry will be handed them by real Nodes and a dead link
/// is worse than a plain string.
fn collection_for(field: &str) -> Option<Collection> {
    match field {
        "id" | "parents" => Some(Collection::Current),
        "node_id" => Some(Collection::Named(ResourceType::Node.plural())),
        "device_id" => Some(Collection::Named(ResourceType::Device.plural())),
        "source_id" => Some(Collection::Named(ResourceType::Source.plural())),
        "flow_id" => Some(Collection::Named(ResourceType::Flow.plural())),
        "sender_id" | "senders" => Some(Collection::Named(ResourceType::Sender.plural())),
        "receiver_id" | "receivers" => Some(Collection::Named(ResourceType::Receiver.plural())),
        _ => None,
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Collection {
    /// The collection currently being browsed.
    Current,
    /// A named collection under the API root.
    Named(&'static str),
}

/// Resolves a reference attribute to an href, for one rendered document.
#[derive(Debug, Clone)]
pub struct LinkResolver {
    /// The collection being browsed, with both slashes.
    collection_base: String,
    /// The versioned API root, without a trailing slash.
    api_base: String,
}

impl LinkResolver {
    /// Build a resolver for a document served at `request_path`.
    ///
    /// `api_base` is the versioned API root, e.g. `/x-nmos/query/v1.3`. Named
    /// references resolve against it rather than against the request path, so a
    /// cross-reference from inside `/senders/{id}` still lands in `/flows/`.
    #[must_use]
    pub fn new(request_path: &str, api_base: &str) -> Self {
        let trimmed = request_path.trim_end_matches('/');
        let segments: Vec<&str> = trimmed.split('/').filter(|s| !s.is_empty()).collect();

        // A single-resource path ends in the resource's own id, so the
        // collection is the path with that id removed. A collection path is
        // already the collection.
        let collection_base = match segments.split_last() {
            Some((last, rest)) if looks_like_uuid(last) => {
                format!("/{}/", rest.join("/"))
            }
            _ => format!("{trimmed}/"),
        };

        Self {
            collection_base,
            api_base: api_base.trim_end_matches('/').to_owned(),
        }
    }

    /// The href for one `(field, value)` pair, or `None` to leave it as text.
    ///
    /// `None` is deliberate rather than a fallback. The renderer treats this
    /// resolver as authoritative for UUIDs, so returning `None` leaves the
    /// value as plain text instead of guessing a collection. A BCP-008 monitor
    /// Source's `monitor_sibling_id` is the motivating case: it names a Sender
    /// **or** a Receiver depending on the sibling's `monitor_type`, which a
    /// per-field resolver cannot see.
    #[must_use]
    pub fn resolve(&self, field: Option<&str>, value: &str) -> Option<String> {
        let field = field?;
        if !looks_like_uuid(value) {
            return None;
        }

        let collection = collection_for(field).or_else(|| {
            // Vendor extensions namespace their keys, e.g. Matrox's
            // `urn:x-matrox:receiver_id`. The suffix after the last colon
            // carries the same meaning as the plain attribute, so it is worth
            // one more lookup before giving up.
            let suffix = field.rsplit(':').next()?;
            collection_for(suffix)
        })?;

        Some(match collection {
            Collection::Current => format!("{}{value}", self.collection_base),
            Collection::Named(name) => format!("{}/{name}/{value}", self.api_base),
        })
    }
}

/// A cheap UUID shape test.
///
/// Deliberately more permissive than the RAML pattern about the version and
/// variant nibbles. This only decides whether to render a hyperlink, and
/// refusing to link a resource a Node actually registered would be the worse
/// error.
#[must_use]
pub fn looks_like_uuid(value: &str) -> bool {
    if value.len() != 36 {
        return false;
    }
    let bytes = value.as_bytes();
    for position in [8, 13, 18, 23] {
        if bytes.get(position) != Some(&b'-') {
            return false;
        }
    }
    value.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-')
}

#[cfg(test)]
mod tests {
    use super::*;

    const API: &str = "/x-nmos/query/v1.3";
    const A_UUID: &str = "3b8be755-08ff-452b-b217-c9151eb21193";

    #[test]
    fn a_cross_reference_lands_in_its_own_collection() {
        // The whole point. A Sender's `flow_id` must link to `/flows/`, not to
        // `/senders/<flow id>`, which would 404.
        let resolver = LinkResolver::new(&format!("{API}/senders/{A_UUID}"), API);
        assert_eq!(
            resolver.resolve(Some("flow_id"), A_UUID),
            Some(format!("{API}/flows/{A_UUID}")),
        );
        assert_eq!(
            resolver.resolve(Some("device_id"), A_UUID),
            Some(format!("{API}/devices/{A_UUID}")),
        );
    }

    #[test]
    fn an_own_id_links_within_the_collection_being_browsed() {
        let from_collection = LinkResolver::new(&format!("{API}/senders"), API);
        assert_eq!(
            from_collection.resolve(Some("id"), A_UUID),
            Some(format!("{API}/senders/{A_UUID}")),
        );

        // From a single-resource path, the id is stripped to find the
        // collection -- otherwise the link would nest indefinitely.
        let from_resource = LinkResolver::new(&format!("{API}/senders/{A_UUID}"), API);
        assert_eq!(
            from_resource.resolve(Some("id"), A_UUID),
            Some(format!("{API}/senders/{A_UUID}")),
        );
    }

    #[test]
    fn parents_stay_in_the_current_collection() {
        // IS-04 constrains `parents` to the resource's own type.
        let resolver = LinkResolver::new(&format!("{API}/flows/{A_UUID}"), API);
        assert_eq!(
            resolver.resolve(Some("parents"), A_UUID),
            Some(format!("{API}/flows/{A_UUID}")),
        );
    }

    #[test]
    fn a_trailing_slash_does_not_double_up() {
        let resolver = LinkResolver::new(&format!("{API}/senders/"), API);
        assert_eq!(
            resolver.resolve(Some("id"), A_UUID),
            Some(format!("{API}/senders/{A_UUID}")),
        );
    }

    #[test]
    fn a_vendor_namespaced_field_resolves_on_its_suffix() {
        // Matrox's `urn:x-matrox:receiver_id` means what `receiver_id` means.
        let resolver = LinkResolver::new(&format!("{API}/sources/{A_UUID}"), API);
        assert_eq!(
            resolver.resolve(Some("urn:x-matrox:receiver_id"), A_UUID),
            Some(format!("{API}/receivers/{A_UUID}")),
        );
    }

    #[test]
    fn an_unrecognised_field_is_left_as_text() {
        // Not a guess. `monitor_sibling_id` names a Sender *or* a Receiver
        // depending on a field this resolver cannot see, so a link would be
        // wrong half the time -- worse than none.
        let resolver = LinkResolver::new(&format!("{API}/sources/{A_UUID}"), API);
        assert_eq!(resolver.resolve(Some("monitor_sibling_id"), A_UUID), None);
        assert_eq!(resolver.resolve(None, A_UUID), None);
    }

    #[test]
    fn a_value_that_is_not_a_uuid_is_left_as_text() {
        let resolver = LinkResolver::new(&format!("{API}/senders/{A_UUID}"), API);
        assert_eq!(resolver.resolve(Some("flow_id"), "not-a-uuid"), None);
        assert_eq!(resolver.resolve(Some("flow_id"), ""), None);
    }

    #[test]
    fn the_deprecated_device_arrays_are_still_linked() {
        // A dead link is worse than a plain string, and real Nodes send these.
        let resolver = LinkResolver::new(&format!("{API}/devices/{A_UUID}"), API);
        assert_eq!(
            resolver.resolve(Some("senders"), A_UUID),
            Some(format!("{API}/senders/{A_UUID}")),
        );
        assert_eq!(
            resolver.resolve(Some("receivers"), A_UUID),
            Some(format!("{API}/receivers/{A_UUID}")),
        );
    }

    #[test]
    fn the_uuid_shape_test_is_permissive_about_version_and_variant() {
        // The RAML pattern pins `[1-5]` and `[89ab]`; this does not, because
        // refusing to link a resource a Node actually registered is the worse
        // error.
        assert!(looks_like_uuid("00000000-0000-0000-0000-000000000000"));
        assert!(looks_like_uuid("FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"));
        assert!(looks_like_uuid(A_UUID));
    }

    #[test]
    fn the_uuid_shape_test_still_rejects_the_wrong_shape() {
        for bad in [
            "",
            "3b8be755-08ff-452b-b217-c9151eb2119", // too short
            "3b8be755-08ff-452b-b217-c9151eb211933", // too long
            "3b8be755_08ff_452b_b217_c9151eb21193", // wrong separators
            "3b8be755-08ff-452b-b217-c9151eb2119g", // not hex
            "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz",
        ] {
            assert!(!looks_like_uuid(bad), "accepted {bad:?}");
        }
    }
}
