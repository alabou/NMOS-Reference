// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Building one WebSocket grain.
//!
//! This is the registry's **only** typed encode. Everything else on the wire is
//! either a stored body served verbatim or a synthesised value going through
//! `dump_any`, so this is the one place a generated type's bytes become
//! something a subscriber parses.
//!
//! # The splice is the point
//!
//! A grain entry's `pre` and `post` are the resource bodies, and they are
//! **spliced**, not re-encoded. The envelope around them is type-checked -- the
//! grain type, the topic, the three timestamps, the rate -- while the bodies
//! pass through untouched, which is what keeps the WebSocket view and the HTTP
//! view describing the same resource with the same bytes.
//!
//! Re-encoding them would not merely reformat. Measured through
//! `serde_json::Value`:
//!
//! ```text
//! in:   {"id": "x",  "n": 1e3, "e": "café", "r": "🎬"}
//! out:  {"e":"café","id":"x","n":1000.0,"r":"🎬"}
//! ```
//!
//! Keys reordered, `1e3` became `1000.0`, whitespace gone. A Controller
//! comparing what it received over the socket against what it fetched over HTTP
//! would see two different documents for one resource.
//!
//! `RawJson` is what prevents that, and it is why `NGeneric` maps to it rather
//! than to `Value`.
//!
//! # The three timestamps
//!
//! `Behaviour - Querying.md:39-45` says they MAY be identical. They are: a
//! registry event has no capture time distinct from its creation time, so
//! inventing a difference would be fiction.

use nmos_json::RawJson;
use nmos_json::value::Tai;
use nmos_registry_core::cursor::TaiCursor;
use nmos_types::generated::narray_of_query_web_socket_grain_data_generic::NArrayOfQueryWebSocketGrainDataGeneric;
use nmos_types::generated::nquery_payload_generic::NQueryPayloadGeneric;
use nmos_types::generated::nquery_web_socket_grain_data_generic::NQueryWebSocketGrainDataGeneric;
use nmos_types::generated::nquery_web_socket_grain_generic::NQueryWebSocketGrainGeneric;
use nmos_types::generated::nrational::NRational;

use crate::subscription::{PendingEvent, Subscription};

/// The `grain_type` every Query API grain carries.
const GRAIN_TYPE: &str = "event";
/// The `data` grain's own type.
const DATA_GRAIN_TYPE: &str = "urn:x-nmos:format:data.event";

/// The nominal grain rate. `Behaviour - Querying.md` fixes it at 0/1 -- grains
/// are event-driven, not clocked.
const GRAIN_RATE: (i64, i64) = (0, 1);

/// Why a grain could not be built.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GrainError {
    /// What went wrong.
    pub detail: String,
}

impl std::fmt::Display for GrainError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for GrainError {}

/// Encode pending events as one grain message.
///
/// `query_id` identifies the Query API instance and becomes `source_id`; the
/// subscription's id becomes `flow_id` (`Behaviour - Querying.md:37`).
///
/// # Errors
///
/// A stored body that is not valid JSON, which cannot happen for a body that
/// reached the store -- decoding is what let it in -- but is reported rather
/// than papered over, because a grain carrying a malformed body is worse than
/// no grain.
pub fn build_grain(
    subscription: &Subscription,
    events: &[PendingEvent],
    query_id: &str,
    now: TaiCursor,
) -> Result<String, GrainError> {
    let mut entries = Vec::with_capacity(events.len());
    for event in events {
        entries.push(NQueryWebSocketGrainDataGeneric {
            path: event.path.clone(),
            // Spliced, not re-encoded. Only the members that apply are set: an
            // absent one is omitted entirely, which is how the presence or
            // absence of pre/post carries the event type.
            pre: splice(event.pre.as_ref())?,
            post: splice(event.post.as_ref())?,
        });
    }

    let payload = NQueryPayloadGeneric {
        grain_type: GRAIN_TYPE.to_owned(),
        source_id: query_id.to_owned(),
        flow_id: subscription.id.clone(),
        // `:39-45` -- the three MAY be identical, and here they are.
        origin_timestamp: tai(now),
        sync_timestamp: tai(now),
        creation_timestamp: tai(now),
        rate: rational(GRAIN_RATE),
        duration: rational(GRAIN_RATE),
        grain: NQueryWebSocketGrainGeneric {
            r#type: DATA_GRAIN_TYPE.to_owned(),
            topic: subscription.resource_type.topic().to_owned(),
            data: NArrayOfQueryWebSocketGrainDataGeneric(entries),
        },
    };

    // The compact encoder with Python's float spelling -- the same one
    // `JsonEngine.encode` is, and not the `dump_any` separators.
    nmos_json::engine::encode_compact(&payload).map_err(|error| GrainError {
        detail: format!("could not encode the grain: {error}"),
    })
}

/// A stored body as a spliceable value.
fn splice(body: Option<&nmos_registry_core::body::Body>) -> Result<Option<RawJson>, GrainError> {
    match body {
        None => Ok(None),
        Some(body) => RawJson::from_text(body.text())
            .map(Some)
            .map_err(|error| GrainError {
                detail: format!("a stored body is not valid JSON: {error}"),
            }),
    }
}

/// A registry cursor as the timestamp a grain carries.
///
/// `Tai` stores UTC-based seconds and adds the offset back when it encodes, so
/// the TAI value has to come off here -- otherwise the grain would be 37
/// seconds ahead of the cursor it describes.
fn tai(cursor: TaiCursor) -> Tai {
    let seconds = i64::try_from(cursor.seconds).unwrap_or(i64::MAX);
    Tai {
        sec: seconds.saturating_sub(Tai::UTC_OFFSET),
        nsec: cursor.nanoseconds,
    }
}

fn rational((numerator, denominator): (i64, i64)) -> NRational {
    NRational {
        numerator,
        denominator: Some(denominator),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use nmos_registry_core::body::Body;
    use nmos_registry_core::resource_type::ResourceType;

    fn subscription() -> Subscription {
        Subscription {
            id: "8a4d1c0e-6f3b-4a1e-9a2c-1f5b7d3e9c02".to_owned(),
            ws_href: "ws://example.test/ws/?uid=s".to_owned(),
            resource_path: "/senders".to_owned(),
            resource_type: ResourceType::Sender,
            params: Vec::new(),
            max_update_rate_ms: 100,
            persist: true,
            secure: false,
            authorization: false,
            created: TaiCursor::new(100, 0),
            host: "example.test".to_owned(),
        }
    }

    const QUERY_ID: &str = "8c4d1e70-6b3a-4f52-9d81-2e7c5a0b3f14";

    #[test]
    fn a_stored_body_reaches_the_wire_byte_for_byte() {
        // The guarantee this module exists for. Every part of this body is
        // something a re-encode would change.
        let escaped = "caf\\u00e9";
        let original = format!(r#"{{"id": "s1",  "n": 1e3, "e": "{escaped}", "r": "🎬", "z": 1}}"#);
        assert!(
            original.contains(r"\u00e9"),
            "the source lost its escape, so this proves nothing about escapes",
        );
        let events = vec![PendingEvent {
            path: "s1".to_owned(),
            pre: None,
            post: Some(Body::new(original.clone())),
        }];

        let grain = build_grain(&subscription(), &events, QUERY_ID, TaiCursor::new(100, 0))
            .expect("the grain builds");

        assert!(
            grain.contains(&original),
            "the body was re-encoded rather than spliced.\n  wanted: {original}\n  in:     {grain}",
        );
        // Each loss a re-encode would cause, named separately so a failure says
        // which one happened.
        assert!(grain.contains(r"\u00e9"), "an escape was expanded: {grain}");
        assert!(grain.contains("1e3"), "a number was renormalised: {grain}");
        assert!(grain.contains("🎬"), "raw UTF-8 was escaped: {grain}");
    }

    #[test]
    fn the_event_shape_is_carried_by_which_sides_are_present() {
        let sub = subscription();
        let body = Body::new(r#"{"id":"s1"}"#);

        let added = build_grain(
            &sub,
            &[PendingEvent {
                path: "s1".to_owned(),
                pre: None,
                post: Some(body.clone()),
            }],
            QUERY_ID,
            TaiCursor::new(100, 0),
        )
        .unwrap();
        assert!(!added.contains("\"pre\""), "an add carried a pre: {added}");
        assert!(added.contains("\"post\""));

        let removed = build_grain(
            &sub,
            &[PendingEvent {
                path: "s1".to_owned(),
                pre: Some(body.clone()),
                post: None,
            }],
            QUERY_ID,
            TaiCursor::new(100, 0),
        )
        .unwrap();
        assert!(removed.contains("\"pre\""));
        assert!(
            !removed.contains("\"post\""),
            "a removal carried a post: {removed}",
        );
    }

    #[test]
    fn the_envelope_carries_the_documented_fields() {
        let sub = subscription();
        let grain = build_grain(
            &sub,
            &[PendingEvent {
                path: "s1".to_owned(),
                pre: None,
                post: Some(Body::new(r#"{"id":"s1"}"#)),
            }],
            QUERY_ID,
            TaiCursor::new(1_700_000_000, 5),
        )
        .unwrap();

        // `:37` -- source_id is the Query API instance, flow_id the subscription.
        assert!(
            grain.contains(&format!(r#""source_id":"{QUERY_ID}""#)),
            "{grain}"
        );
        assert!(
            grain.contains(&format!(r#""flow_id":"{}""#, sub.id)),
            "{grain}"
        );
        assert!(grain.contains(r#""grain_type":"event""#));
        // `:49` -- the topic is the collection path with BOTH slashes.
        assert!(grain.contains(r#""topic":"/senders/""#), "{grain}");
        // The three timestamps are identical and round-trip the cursor.
        assert_eq!(
            grain.matches("1700000000:5").count(),
            3,
            "the three timestamps should agree and equal the cursor: {grain}",
        );
    }

    #[test]
    fn several_events_ride_in_one_grain_in_order() {
        // Coalesced windows produce several entries, and their order is the
        // order they were queued -- which for a cascade is children first.
        let sub = subscription();
        let events: Vec<PendingEvent> = ["a", "b", "c"]
            .iter()
            .map(|id| PendingEvent {
                path: (*id).to_owned(),
                pre: None,
                post: Some(Body::new(format!(r#"{{"id":"{id}"}}"#))),
            })
            .collect();

        let grain = build_grain(&sub, &events, QUERY_ID, TaiCursor::new(100, 0)).expect("builds");

        let a = grain.find(r#""path":"a""#).expect("a is present");
        let b = grain.find(r#""path":"b""#).expect("b is present");
        let c = grain.find(r#""path":"c""#).expect("c is present");
        assert!(a < b && b < c, "the entries were reordered: {grain}");
    }

    #[test]
    fn an_empty_grain_is_still_well_formed() {
        // A rate-limited window that coalesced to nothing still has to encode.
        let grain =
            build_grain(&subscription(), &[], QUERY_ID, TaiCursor::new(100, 0)).expect("builds");
        let parsed: serde_json::Value =
            serde_json::from_str(&grain).expect("the grain is valid JSON");
        assert_eq!(
            parsed
                .get("grain")
                .and_then(|g| g.get("data"))
                .and_then(serde_json::Value::as_array)
                .map(Vec::len),
            Some(0),
        );
    }

    #[test]
    fn the_grain_is_valid_json_despite_the_splice() {
        // Splicing raw text into a structure is exactly how one produces
        // malformed output, so this is asserted rather than assumed.
        let grain = build_grain(
            &subscription(),
            &[PendingEvent {
                path: "s1".to_owned(),
                pre: Some(Body::new(r#"{"a":[1,2,{"b":null}]}"#)),
                post: Some(Body::new(r#"{"a": [1, 2, {"b": "}"}]}"#)),
            }],
            QUERY_ID,
            TaiCursor::new(100, 0),
        )
        .expect("builds");

        let parsed: serde_json::Value =
            serde_json::from_str(&grain).expect("the grain is valid JSON");
        assert!(parsed.get("grain").is_some());
    }

    #[test]
    fn a_body_that_is_not_json_is_reported_rather_than_spliced() {
        // Unreachable through the store, but splicing it would produce a grain
        // no subscriber could parse -- a worse failure than none.
        let result = build_grain(
            &subscription(),
            &[PendingEvent {
                path: "s1".to_owned(),
                pre: None,
                post: Some(Body::new("{not json")),
            }],
            QUERY_ID,
            TaiCursor::new(100, 0),
        );
        assert!(result.is_err(), "a malformed body was spliced");
    }
}
