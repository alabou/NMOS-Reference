// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Validating a `POST /resource` envelope, and keeping its bytes.
//!
//! Port of `nmos/registry/decode.py`. This is the **only** place a resource is
//! decoded against its generated type, and that decode *is* the `APIs.md:22`
//! schema validation.
//!
//! # Why it takes text rather than a parsed value
//!
//! The `data` value has to be sliced out of the request verbatim. Parsing
//! normalises number spelling and string escaping irreversibly -- `1e3` becomes
//! `1000.0`, `A` becomes `A` -- and the registry promises to serve back
//! what a Node registered rather than a re-rendering of it. So the span comes
//! out of the source and the parse happens beside it, not instead of it.
//!
//! # The decoded object is thrown away, on purpose
//!
//! Nothing downstream reads it: the Query API, the basic-query filters and the
//! WebSocket grains all serve the stored body. Retaining it cost roughly three
//! times the memory of the resource itself. The call is kept purely for its
//! validation side effect, and removing it would remove schema validation from
//! the registry.
//!
//! # Where this diverges from the Python, and why it is not a divergence
//!
//! Python's `member_spans` returns `(span_text, parsed_value)` per member,
//! because `raw_decode` builds each value in order to find where it ends -- so
//! one pass yields both. The Rust `member_spans` returns spans only, and this
//! module parses the two spans it actually needs.
//!
//! That is divergence **D10** doing its job rather than a shortcut. Python then
//! hands the parsed `data` to `Body`, so every registered resource holds both
//! representations for its lifetime -- roughly 50 MB of `Value` beside the text
//! at 15,000 resources. Here the parse is used for validation and dropped, and
//! `Body` re-parses lazily if a filter ever needs it. The bytes served are
//! identical either way.

use nmos_json::spans::member_spans;
use nmos_registry_core::body::Body;
use nmos_registry_core::resource_type::ResourceType;

/// A `POST /resource` body the registry must refuse with 400.
///
/// Carries the message, because `handlers_registration.py:158` puts it straight
/// into the response body -- the text is an observable part of the API, which is
/// the same reason the decode path is generated rather than derived.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodeFailure(String);

impl DecodeFailure {
    /// The message, as the 400 body will carry it.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for DecodeFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for DecodeFailure {}

fn fail<T>(reason: impl Into<String>) -> Result<T, DecodeFailure> {
    Err(DecodeFailure(reason.into()))
}

/// Validate a `POST /resource` envelope and return what the registry keeps.
///
/// The envelope is `{"type": <singular>, "data": {...}}`, per
/// `registrationapi-resource-post-request.json`.
///
/// # Errors
///
/// [`DecodeFailure`] when the envelope is malformed, names an unknown type, or
/// its `data` does not validate against the generated type.
pub fn decode_post_envelope(source: &str) -> Result<(ResourceType, Body), DecodeFailure> {
    let members = match member_spans(source) {
        Ok(members) => members,
        Err(scan_error) => {
            // Distinguish "not JSON at all" from "valid JSON, wrong shape", so
            // the 400 says which. Failure path only, so the extra parse costs
            // nothing on any request that succeeds.
            //
            // The message is the **span scanner's**, not `serde_json`'s.
            // Python interpolates `JsonSpanError` here, and that text reaches
            // the client: `expected '{' at offset 0, found 'n'`, where
            // `serde_json` would say `expected ident at line 1 column 2`.
            // Reaching for the parser's error instead is a quiet divergence in
            // an observable 400 body -- it was written that way first, and
            // measured against the Python.
            return match serde_json::from_str::<serde_json::Value>(source) {
                Ok(_) => fail("expected a JSON object"),
                Err(_) => fail(format!("invalid JSON body: {scan_error}")),
            };
        }
    };

    let type_name = members
        .get("type")
        .and_then(|span| serde_json::from_str::<String>(span).ok());
    let Some(type_name) = type_name else {
        return fail("missing or non-string 'type' in registration envelope");
    };

    let Some(resource_type) = ResourceType::from_singular(&type_name) else {
        let permitted = ResourceType::ALL
            .iter()
            .map(|kind| kind.singular())
            .collect::<Vec<_>>()
            .join(", ");
        return fail(format!(
            "unknown resource type {}; expected one of: {permitted}",
            nmos_json::error::python_repr(&type_name),
        ));
    };

    let Some(data_text) = members.get("data") else {
        return fail("missing or non-object 'data' in registration envelope");
    };
    // An object, not merely valid JSON. `data: []` and `data: "x"` are both
    // parseable and both wrong, and the generated decode would report them in
    // its own words rather than in the envelope's.
    let Ok(data) = serde_json::from_str::<serde_json::Value>(data_text) else {
        return fail("missing or non-object 'data' in registration envelope");
    };
    if !data.is_object() {
        return fail("missing or non-object 'data' in registration envelope");
    }

    // Called purely for its validation side effect -- this call IS the
    // `APIs.md:22` schema check, so it must stay.
    validate(resource_type, &data)?;

    // The span, not a re-encoding.
    Ok((resource_type, Body::new((*data_text).to_owned())))
}

/// Decode a resource against its generated type, and discard the result.
fn validate(resource_type: ResourceType, data: &serde_json::Value) -> Result<(), DecodeFailure> {
    use nmos_types::generated as types;

    let outcome = match resource_type {
        ResourceType::Node => types::nnode::NNode::decode(data).map(|_| ()),
        ResourceType::Device => types::ndevice::NDevice::decode(data).map(|_| ()),
        ResourceType::Source => types::nsource::NSource::decode(data).map(|_| ()),
        ResourceType::Flow => types::nflow::NFlow::decode(data).map(|_| ()),
        ResourceType::Sender => types::nsender::NSender::decode(data).map(|_| ()),
        ResourceType::Receiver => types::nreceiver::NReceiver::decode(data).map(|_| ()),
    };
    outcome.map_err(|error| {
        DecodeFailure(format!(
            "{} failed validation: {error}",
            resource_type.singular(),
        ))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const NODE: &str = r#"{
        "id": "3b8be755-08ff-452b-b217-c9151eb21193",
        "version": "1600000000:0",
        "label": "n",
        "description": "",
        "tags": {},
        "href": "http://example.test/",
        "hostname": "example",
        "caps": {},
        "api": {"versions": ["v1.3"], "endpoints": []},
        "services": [],
        "clocks": [],
        "interfaces": []
    }"#;

    fn envelope(kind: &str, data: &str) -> String {
        format!(r#"{{"type":"{kind}","data":{data}}}"#)
    }

    // -- the happy path ----------------------------------------------------

    #[test]
    fn a_valid_envelope_yields_its_type_and_its_bytes() {
        let source = envelope("node", NODE);
        let (kind, body) = decode_post_envelope(&source).expect("a valid Node");
        assert_eq!(kind, ResourceType::Node);
        assert_eq!(
            body.text(),
            NODE,
            "the stored body must be the span, not a re-encoding",
        );
    }

    #[test]
    fn the_stored_bytes_survive_spellings_a_parse_would_normalise() {
        // The whole reason this takes text. `1e3` and `A` both survive a
        // round trip through `serde_json` as different bytes.
        let data = NODE.replace(r#""label": "n""#, r#""label": "A", "x-spelling": 1e3"#);
        let source = envelope("node", &data);
        let (_, body) = decode_post_envelope(&source).expect("a valid Node");
        assert!(body.text().contains(r"A"), "{}", body.text());
        assert!(body.text().contains("1e3"), "{}", body.text());
    }

    #[test]
    fn every_resource_type_is_dispatched() {
        // A missing arm would be a type that cannot be registered at all.
        for kind in ResourceType::ALL {
            let source = envelope(kind.singular(), "{}");
            let error =
                decode_post_envelope(&source).expect_err("an empty object is not a valid resource");
            assert!(
                error
                    .message()
                    .starts_with(&format!("{} failed", kind.singular())),
                "{kind:?}: {}",
                error.message(),
            );
        }
    }

    // -- envelope shape ----------------------------------------------------

    #[test]
    fn text_that_is_not_json_says_so() {
        let error = decode_post_envelope("not json").expect_err("rejects");
        assert!(
            error.message().starts_with("invalid JSON body: "),
            "{}",
            error.message(),
        );
    }

    #[test]
    fn valid_json_that_is_not_an_object_says_something_different() {
        // The distinction is the point: "not JSON at all" and "JSON, wrong
        // shape" are different mistakes and the 400 should say which.
        for source in ["[]", r#""a string""#, "42", "null", "true"] {
            let error = decode_post_envelope(source).expect_err("rejects");
            assert_eq!(error.message(), "expected a JSON object", "{source}");
        }
    }

    #[test]
    fn a_missing_or_non_string_type_is_named() {
        for source in [
            r#"{"data":{}}"#,
            r#"{"type":42,"data":{}}"#,
            r#"{"type":null,"data":{}}"#,
            r#"{"type":["node"],"data":{}}"#,
        ] {
            let error = decode_post_envelope(source).expect_err("rejects");
            assert_eq!(
                error.message(),
                "missing or non-string 'type' in registration envelope",
                "{source}",
            );
        }
    }

    #[test]
    fn an_unknown_type_lists_what_is_permitted() {
        let error = decode_post_envelope(r#"{"type":"widget","data":{}}"#).expect_err("rejects");
        assert_eq!(
            error.message(),
            "unknown resource type 'widget'; expected one of: \
             node, device, source, flow, sender, receiver",
        );
    }

    #[test]
    fn the_plural_is_not_accepted_where_the_singular_is_required() {
        // `registrationapi-resource-post-request.json` names the singular. The
        // AMWA mock derives one from the other with `rstrip("s")`, which is
        // exactly the coercion this refuses to do.
        let error = decode_post_envelope(r#"{"type":"nodes","data":{}}"#).expect_err("rejects");
        assert!(
            error.message().starts_with("unknown resource type 'nodes'"),
            "{}",
            error.message(),
        );
    }

    #[test]
    fn a_missing_or_non_object_data_is_named() {
        for source in [
            r#"{"type":"node"}"#,
            r#"{"type":"node","data":[]}"#,
            r#"{"type":"node","data":"x"}"#,
            r#"{"type":"node","data":null}"#,
            r#"{"type":"node","data":42}"#,
        ] {
            let error = decode_post_envelope(source).expect_err("rejects");
            assert_eq!(
                error.message(),
                "missing or non-object 'data' in registration envelope",
                "{source}",
            );
        }
    }

    // -- validation --------------------------------------------------------

    #[test]
    fn a_data_that_fails_its_schema_is_reported_as_a_validation_failure() {
        let source = envelope("node", r#"{"id":"not-a-uuid"}"#);
        let error = decode_post_envelope(&source).expect_err("rejects");
        assert!(
            error.message().starts_with("node failed validation: "),
            "{}",
            error.message(),
        );
    }

    #[test]
    fn member_order_in_the_envelope_does_not_matter() {
        let reordered = format!(r#"{{"data":{NODE},"type":"node"}}"#);
        let (kind, body) = decode_post_envelope(&reordered).expect("a valid Node");
        assert_eq!(kind, ResourceType::Node);
        assert_eq!(body.text(), NODE);
    }

    #[test]
    fn extra_envelope_members_are_ignored() {
        // The envelope schema does not seal itself, and a Node sending an
        // extra member is not a reason to refuse its registration.
        let source = format!(r#"{{"type":"node","data":{NODE},"extra":1}}"#);
        assert!(decode_post_envelope(&source).is_ok());
    }
}
