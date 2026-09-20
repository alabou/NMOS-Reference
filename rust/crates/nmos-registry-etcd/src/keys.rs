// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The etcd key layout and the value envelope.
//!
//! # Layout
//!
//! ```text
//! <ns>/meta/config
//! <ns>/ids/<resource-id>
//! <ns>/nodes/<node-id>/self
//! <ns>/nodes/<node-id>/devices/<device-id>/self
//! <ns>/nodes/<node-id>/devices/<device-id>/<plural>/<resource-id>
//! ```
//!
//! The shape is chosen so that **a Node's entire subtree is one prefix**. That
//! single property does most of the work in this design:
//!
//! * deleting a Node is one ranged delete, not a walk;
//! * every key belonging to a Node hangs off that Node's lease, so expiry
//!   collects the subtree atomically on every member at once;
//! * two Nodes registering concurrently touch disjoint prefixes, so unrelated
//!   registrations never contend -- which is why there is no global generation
//!   key and no hot key anywhere in the mutation path.
//!
//! `<ns>/ids/<resource-id>` is the one deliberate exception to the tree. It is
//! a flat claim used to detect the cross-type id collision of
//! `Behaviour - Registration.md:101`, which cannot be answered from the tree
//! because the tree is keyed by *where a resource is*, and the question is
//! whether an id exists *anywhere*. Keeping it flat is what avoids a global
//! index.
//!
//! # Envelope
//!
//! Values are JSON carrying the resource exactly as the Node sent it, plus the
//! registry-assigned paging cursors and a schema version. The raw form is
//! stored verbatim for the same reason [`Body::text`] exists: the Query API
//! serves what was registered, byte for byte, including attributes the
//! generated types do not model.
//!
//! The cursors are in the envelope because they must be *authoritative*. If
//! each member allocated its own, the same resource would page differently on
//! different members. Storing them makes the value the single source of truth
//! for ordering.
//!
//! # Parity
//!
//! Every key string and every refusal message here must match
//! `nmos/registry/keys.py` exactly. Keys, because a mixed cluster writes and
//! reads one namespace and a single mismatched separator produces a resource
//! that is stored but never materialised -- silent, and extremely hard to see.
//! Messages, because they are what an operator reads out of a log when a key
//! turns out to be unreadable. `tests/key_parity.rs` asserts both against a
//! corpus recorded from the Python.

use std::fmt;

use nmos_json::repr::{py_repr, py_repr_str};
use nmos_json::spans::{is_json_document, member_spans};
use nmos_registry_core::{Body, ResourceType, TaiCursor};
use serde::Serialize;
use serde_json::Value;

/// The envelope schema version this implementation writes.
///
/// Bumped only for a change that a previous version could not read. The
/// preload refuses an envelope from the future rather than guessing, because a
/// registry silently ignoring fields it does not understand is how two members
/// end up serving different content for the same resource.
pub const ENVELOPE_VERSION: i64 = 1;

const META: &str = "meta";
const CONFIG: &str = "config";
const IDS: &str = "ids";
const NODES: &str = "nodes";
const DEVICES: &str = "devices";
const SELF: &str = "self";

/// A key or envelope did not have the expected shape.
///
/// Named `KeyFault` rather than `KeyError`: the Python calls it `KeyError_`
/// only to dodge the builtin of that name, a collision Rust does not have, and
/// `keys::KeyError` stutters. The type is the same thing and carries the same
/// messages.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KeyFault(String);

impl KeyFault {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    /// The message, which is what an operator sees in a log.
    #[must_use]
    pub fn message(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for KeyFault {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for KeyFault {}

/// Shorthand for the one error type in this module.
pub type Result<T> = std::result::Result<T, KeyFault>;

// ---------------------------------------------------------------------------
// Namespace
// ---------------------------------------------------------------------------

/// The configured key prefix, and every key derived from it.
///
/// All key construction goes through here rather than being formatted at call
/// sites: a single mismatched separator between the writer and the watcher
/// would produce a resource that is stored but never materialised, which is
/// both silent and extremely hard to see.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct Namespace {
    prefix: String,
}

impl Namespace {
    /// Validate and adopt a configured prefix.
    ///
    /// # Errors
    ///
    /// `KeyFault` when the prefix does not start with `/`, or ends with one.
    /// Both would make every derived key ambiguous about where the namespace
    /// stops.
    pub fn new(prefix: impl Into<String>) -> Result<Self> {
        let prefix = prefix.into();
        if !prefix.starts_with('/') {
            return Err(KeyFault::new(format!(
                "namespace must start with '/': {}",
                py_repr_str(&prefix),
            )));
        }
        if prefix.ends_with('/') {
            return Err(KeyFault::new(format!(
                "namespace must not end with '/': {}",
                py_repr_str(&prefix),
            )));
        }
        Ok(Self { prefix })
    }

    /// The prefix as configured.
    #[must_use]
    pub fn prefix(&self) -> &str {
        &self.prefix
    }

    // -- roots --------------------------------------------------------------

    /// Everything the watch covers.
    #[must_use]
    pub fn root(&self) -> Vec<u8> {
        format!("{}/", self.prefix).into_bytes()
    }

    /// The cluster configuration document.
    #[must_use]
    pub fn meta_config(&self) -> Vec<u8> {
        format!("{}/{META}/{CONFIG}", self.prefix).into_bytes()
    }

    /// The prefix every flat id claim shares.
    #[must_use]
    pub fn ids_root(&self) -> Vec<u8> {
        format!("{}/{IDS}/", self.prefix).into_bytes()
    }

    /// The prefix every Node subtree hangs off.
    #[must_use]
    pub fn nodes_root(&self) -> Vec<u8> {
        format!("{}/{NODES}/", self.prefix).into_bytes()
    }

    // -- resource keys ------------------------------------------------------

    /// The flat claim proving this id is not in use by another type.
    #[must_use]
    pub fn id_claim(&self, resource_id: &str) -> Vec<u8> {
        format!("{}/{IDS}/{resource_id}", self.prefix).into_bytes()
    }

    /// Prefix covering a Node and everything under it.
    ///
    /// The prefix a Node delete ranges over, and the prefix every key on that
    /// Node's lease shares.
    #[must_use]
    pub fn node_subtree(&self, node_id: &str) -> Vec<u8> {
        format!("{}/{NODES}/{node_id}/", self.prefix).into_bytes()
    }

    /// The Node resource itself.
    #[must_use]
    pub fn node(&self, node_id: &str) -> Vec<u8> {
        format!("{}/{NODES}/{node_id}/{SELF}", self.prefix).into_bytes()
    }

    /// Prefix covering a Device and everything under it.
    #[must_use]
    pub fn device_subtree(&self, node_id: &str, device_id: &str) -> Vec<u8> {
        format!("{}/{NODES}/{node_id}/{DEVICES}/{device_id}/", self.prefix).into_bytes()
    }

    /// The Device resource itself.
    #[must_use]
    pub fn device(&self, node_id: &str, device_id: &str) -> Vec<u8> {
        format!(
            "{}/{NODES}/{node_id}/{DEVICES}/{device_id}/{SELF}",
            self.prefix,
        )
        .into_bytes()
    }

    /// Key for a Source, Flow, Sender or Receiver.
    ///
    /// # Errors
    ///
    /// `KeyFault` for a Node or a Device, which have their own key functions
    /// because they live at `self` rather than under a plural collection.
    pub fn child(
        &self,
        resource_type: ResourceType,
        node_id: &str,
        device_id: &str,
        resource_id: &str,
    ) -> Result<Vec<u8>> {
        if matches!(resource_type, ResourceType::Node | ResourceType::Device) {
            return Err(KeyFault::new(format!(
                "{} has its own key function",
                resource_type.singular(),
            )));
        }
        let plural = resource_type.plural();
        Ok(format!(
            "{}/{NODES}/{node_id}/{DEVICES}/{device_id}/{plural}/{resource_id}",
            self.prefix,
        )
        .into_bytes())
    }

    // -- parsing ------------------------------------------------------------

    /// Decode a key back into what it identifies.
    ///
    /// Returns `Ok(None)` for keys the Query view does not materialise -- the
    /// meta config and the id claims. They are not errors: the watch sees
    /// every key under the namespace, and these two are bookkeeping the local
    /// store has no representation for. Returning `None` rather than failing
    /// keeps the watch loop's ordinary path free of error handling.
    ///
    /// # Errors
    ///
    /// `KeyFault` when the key is outside the namespace, names a section that
    /// does not exist, or is a resource key of the wrong shape.
    pub fn parse(&self, key: &[u8]) -> Result<Option<ParsedKey>> {
        // Lossy, matching Python's `decode("utf-8", errors="replace")`. A key
        // that is not UTF-8 is one some other tool wrote, and it should be
        // *reported* rather than turned into a different failure -- the
        // operator needs to see as much of it as can be shown.
        let text = String::from_utf8_lossy(key);
        let inside = text
            .strip_prefix(&self.prefix)
            .and_then(|rest| rest.strip_prefix('/'))
            .ok_or_else(|| {
                KeyFault::new(format!(
                    "key outside namespace {}: {}",
                    py_repr_str(&self.prefix),
                    py_repr_str(&text),
                ))
            })?;

        let parts: Vec<&str> = inside.split('/').collect();
        // `split` on a non-empty separator always yields at least one element,
        // so indexing position 0 is total. Spelled as a `match` on the slice
        // so that stays true by construction rather than by argument.
        let [section, rest @ ..] = parts.as_slice() else {
            unreachable!("split always yields at least one element")
        };

        if *section == META || *section == IDS {
            return Ok(None);
        }
        if *section != NODES {
            return Err(KeyFault::new(format!(
                "unrecognised key section {}: {}",
                py_repr_str(section),
                py_repr_str(&text),
            )));
        }

        match rest {
            // nodes/<id>/self
            [node_id, tail] if *tail == SELF => Ok(Some(ParsedKey {
                resource_type: ResourceType::Node,
                resource_id: (*node_id).to_owned(),
                node_id: (*node_id).to_owned(),
                device_id: None,
            })),
            // nodes/<id>/devices/<id>/self
            [node_id, devices, device_id, tail] if *devices == DEVICES && *tail == SELF => {
                Ok(Some(ParsedKey {
                    resource_type: ResourceType::Device,
                    resource_id: (*device_id).to_owned(),
                    node_id: (*node_id).to_owned(),
                    device_id: Some((*device_id).to_owned()),
                }))
            }
            // nodes/<id>/devices/<id>/<plural>/<id>
            [node_id, devices, device_id, plural, resource_id] if *devices == DEVICES => {
                let resource_type = ResourceType::from_plural(plural).ok_or_else(|| {
                    KeyFault::new(format!(
                        "unknown collection {} in key {}",
                        py_repr_str(plural),
                        py_repr_str(&text),
                    ))
                })?;
                Ok(Some(ParsedKey {
                    resource_type,
                    resource_id: (*resource_id).to_owned(),
                    node_id: (*node_id).to_owned(),
                    device_id: Some((*device_id).to_owned()),
                }))
            }
            _ => Err(KeyFault::new(format!(
                "malformed resource key: {}",
                py_repr_str(&text),
            ))),
        }
    }
}

// ---------------------------------------------------------------------------
// ParsedKey
// ---------------------------------------------------------------------------

/// What a resource key identifies.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ParsedKey {
    /// The type the key's position says this resource is.
    pub resource_type: ResourceType,
    /// The resource's own id.
    pub resource_id: String,
    /// The Node whose subtree it lives in.
    pub node_id: String,
    /// The Device it hangs off, or `None` for a Node.
    pub device_id: Option<String>,
}

impl ParsedKey {
    /// Whether this key names a Node.
    #[must_use]
    pub const fn is_node(&self) -> bool {
        matches!(self.resource_type, ResourceType::Node)
    }

    /// Tree depth, used to apply parents before children within a revision.
    ///
    /// Node 0, Device 1, everything else 2. Registration order is normative
    /// (`Behaviour - Registration.md:57-64`), and a revision that creates a
    /// Device and its Senders together has to be applied in that order or the
    /// store's referential-integrity check rejects the children.
    #[must_use]
    pub const fn depth(&self) -> u8 {
        match self.resource_type {
            ResourceType::Node => 0,
            ResourceType::Device => 1,
            _ => 2,
        }
    }
}

// ---------------------------------------------------------------------------
// Envelope
// ---------------------------------------------------------------------------

/// The metadata half of a stored value, in the order it is written.
///
/// A struct rather than a map because serde serialises fields in declaration
/// order, and the order is part of the bytes: Python builds the same head as a
/// `dict` literal, whose insertion order is `v, type, created, updated,
/// health`. A map would sort them and every stored value would differ.
#[derive(Serialize)]
struct EnvelopeHead<'a> {
    v: i64,
    #[serde(rename = "type")]
    resource_type: &'a str,
    created: String,
    updated: String,
    health: i64,
}

/// The stored value for one resource.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Envelope {
    /// The schema version the writer stamped.
    pub version: i64,
    /// What the writer says this resource is.
    pub resource_type: ResourceType,
    /// The resource exactly as the Node sent it.
    pub body: Body,
    /// The registry-assigned creation cursor.
    pub created: TaiCursor,
    /// The registry-assigned update cursor.
    pub updated: TaiCursor,
    /// Seconds since the TAI epoch at the last heartbeat.
    pub health: i64,
}

impl Envelope {
    /// The body's parsed form, for the checks that need field access.
    #[must_use]
    pub fn raw(&self) -> &Value {
        self.body.data()
    }

    /// Serialise for etcd.
    ///
    /// The metadata is encoded normally; the body is **spliced in as text**.
    /// Re-encoding it here would normalise the Node's spelling -- `1e3` to
    /// `1000.0`, `é` to `é` -- and then the member that accepted the
    /// registration would serve different bytes from every member that
    /// materialised it from storage. Splicing keeps all members byte-identical
    /// and costs nothing, since the text is what we already hold.
    ///
    /// Safe because [`Body::text`] is always a value some JSON parser has
    /// already accepted, so the result is well-formed by construction.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let head = EnvelopeHead {
            v: self.version,
            resource_type: self.resource_type.singular(),
            created: self.created.to_string(),
            updated: self.updated.to_string(),
            health: self.health,
        };
        // `dump_any` fails only if the value's own `Serialize` fails, and this
        // one is five scalars. The fallback spells the same bytes rather than
        // panicking, because this runs on the mutation path.
        let prefix = nmos_json::engine::dump_any(&head).unwrap_or_else(|_| {
            format!(
                "{{\"v\": {}, \"type\": \"{}\", \"created\": \"{}\", \"updated\": \"{}\", \
                 \"health\": {}}}",
                self.version,
                self.resource_type.singular(),
                self.created,
                self.updated,
                self.health,
            )
        });
        // Python asserts this; here the shape is fixed by the struct above, so
        // trimming the brace is a slice rather than a claim.
        let head_body = prefix.strip_suffix('}').unwrap_or(prefix.as_str());
        let mut out = String::with_capacity(
            head_body
                .len()
                .saturating_add(self.body.text().len())
                .saturating_add(12),
        );
        out.push_str(head_body);
        out.push_str(", \"data\": ");
        out.push_str(self.body.text());
        out.push('}');
        out.into_bytes()
    }

    /// Parse a stored value, or fail.
    ///
    /// Every failure here is fatal to the preload rather than survivable: a
    /// value that cannot be read is a resource whose existence this member
    /// cannot agree on, and continuing would mean serving a view that silently
    /// differs from its peers'.
    ///
    /// # Errors
    ///
    /// `KeyFault` for anything that is not a readable envelope of a version
    /// this registry understands.
    pub fn decode(value: &[u8]) -> Result<Self> {
        // One pass yields both the metadata and the body's exact span;
        // parsing and then locating the span separately would parse every
        // resource body twice on the watch path.
        let text = std::str::from_utf8(value).map_err(|exc| {
            // Python interpolates `UnicodeDecodeError`, which is CPython's
            // words for CPython's exception. The prefix is shared and the
            // detail is not -- the same boundary `persist_refusals` draws, and
            // recorded in the corpus as such rather than left to look like an
            // accident.
            KeyFault::new(format!("envelope is not valid JSON: {exc}"))
        })?;

        let members = match member_spans(text) {
            Ok(members) => members,
            Err(exc) => {
                // Distinguish "not JSON at all" from "valid JSON, wrong
                // shape". Only reachable on the failure path, so the extra
                // scan is free in every case that matters.
                //
                // The second scan uses the span scanner's grammar rather than
                // `serde_json`, because Python's second attempt is
                // `json.loads`, which accepts `NaN` and `Infinity`. Asking
                // `serde_json` instead called `[NaN]` "not valid JSON" where
                // Python calls it "not a JSON object" -- two different things
                // to tell an operator, and found by running the case rather
                // than by reading the code.
                return Err(if is_json_document(text) {
                    KeyFault::new("envelope is not a JSON object")
                } else {
                    KeyFault::new(format!("envelope is not valid JSON: {exc}"))
                });
            }
        };

        // Spans, parsed on demand. Python's `member_spans` hands back the
        // parsed value alongside each span; this one returns spans only, and
        // the five metadata members are scalars, so parsing them here is the
        // same work in a different place.
        let scalar = |name: &str| -> Option<Value> {
            members
                .get(name)
                .and_then(|span| serde_json::from_str::<Value>(span).ok())
        };

        let (version, version_literal) = match integer_member(members.get("v").copied()) {
            IntegerMember::Absent => return Err(KeyFault::new("envelope has no integer 'v'")),
            IntegerMember::TooLarge { literal, value } => (value, literal),
            IntegerMember::Value(version) => (version, version.to_string()),
        };
        if version > ENVELOPE_VERSION {
            // The *literal*, not the saturated value, so the refusal names
            // what was actually read. Python's int is unbounded and prints
            // the digits it holds.
            return Err(version_too_new(&version_literal));
        }

        let type_name = scalar("type");
        let resource_type = type_name
            .as_ref()
            .and_then(Value::as_str)
            .and_then(ResourceType::from_singular)
            .ok_or_else(|| {
                KeyFault::new(format!(
                    "envelope has unknown type {}",
                    py_repr(type_name.as_ref()),
                ))
            })?;

        let data = members.get("data").copied().filter(|span| {
            // The span has no leading whitespace -- the scanner skips it
            // before recording -- so a leading `{` is exactly "this is a JSON
            // object". Checking the text rather than re-parsing is not only
            // cheaper: `member_spans` accepts `NaN` and `Infinity` as Python's
            // decoder does, and `serde_json::from_str` does not, so a body
            // containing one would be refused here and accepted there.
            span.starts_with('{')
        });
        let Some(data) = data else {
            return Err(KeyFault::new("envelope has no 'data' object"));
        };
        // The TEXT the writer stored, not a re-encoding, so this member serves
        // the same bytes as the one that accepted the registration.
        let body = Body::new(data);

        let created = cursor_member(&scalar("created"), "created")?;
        let updated = cursor_member(&scalar("updated"), "updated")?;

        let health = match integer_member(members.get("health").copied()) {
            IntegerMember::Absent => {
                return Err(KeyFault::new("envelope has no integer 'health'"));
            }
            IntegerMember::TooLarge { value, .. } | IntegerMember::Value(value) => value,
        };

        Ok(Self {
            version,
            resource_type,
            body,
            created,
            updated,
            health,
        })
    }
}

fn version_too_new(version: &str) -> KeyFault {
    KeyFault::new(format!(
        "envelope schema version {version} is newer than this registry understands \
         ({ENVELOPE_VERSION}). Upgrade this member rather than letting it serve a partial view.",
    ))
}

/// What reading a member that must be an integer found.
enum IntegerMember {
    /// Missing, or present and not an integer.
    Absent,
    /// An integer literal outside `i64`, saturated.
    ///
    /// The port's settled rule, not a decision taken here: Python's integers
    /// are arbitrary precision and this port's clamp at `i64`, which is what
    /// `NInt` and TAI seconds already do. Accept and reject still match
    /// exactly, which is the property that has to hold.
    ///
    /// The literal is kept because the version refusal prints it, and printing
    /// the saturated value would name a number the envelope does not contain.
    TooLarge { literal: String, value: i64 },
    /// An integer.
    Value(i64),
}

/// Read a member that must be an integer.
///
/// **`true` and `false` are refused**, which is worth saying because in Python
/// they would not be: `isinstance(True, int)` is true there, so the obvious
/// spelling of this check accepts `{"health": true}` and stores the health as
/// `1` -- one second after the TAI epoch, which makes the resource expire on
/// the next collection pass. It appears and then vanishes.
///
/// That was the Python's behaviour until this port reached it, and it was an
/// inconsistency rather than a decision: every other integer the registry
/// reads off the wire is already protected, because `NInt`'s setter refuses a
/// `bool` (`nmos/json/types.py:193`), as do the nullable and array forms and
/// `max_update_rate_ms`. The envelope decoder is the one integer boundary that
/// bypasses the base-type layer -- it reads raw JSON straight out of etcd --
/// so the guard is spelled out on both sides. Fixed in the Python in the same
/// change, so the two still agree on exactly what they reject.
///
/// A float is refused too, including `7.0`, which was always Python's
/// behaviour.
fn integer_member(span: Option<&str>) -> IntegerMember {
    let Some(span) = span else {
        return IntegerMember::Absent;
    };
    let literal = span.trim();
    // An integer literal, by JSON's grammar: an optional minus and digits,
    // with no `.` and no exponent. Classifying on the text rather than on a
    // parsed `Value` is what keeps a literal beyond `i64` distinguishable
    // from a float -- `serde_json` turns both into `f64`.
    let digits = literal.strip_prefix('-').unwrap_or(literal);
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return IntegerMember::Absent;
    }
    literal.parse::<i64>().map_or_else(
        |_| IntegerMember::TooLarge {
            literal: literal.to_owned(),
            value: if literal.starts_with('-') {
                i64::MIN
            } else {
                i64::MAX
            },
        },
        IntegerMember::Value,
    )
}

fn cursor_member(value: &Option<Value>, field: &str) -> Result<TaiCursor> {
    let Some(text) = value.as_ref().and_then(Value::as_str) else {
        return Err(KeyFault::new(format!(
            "envelope has no string {}",
            py_repr_str(field),
        )));
    };
    TaiCursor::parse(text).ok_or_else(|| {
        KeyFault::new(format!(
            "envelope {} is not '<sec>:<nsec>': {}",
            py_repr_str(field),
            py_repr_str(text),
        ))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ns() -> Namespace {
        Namespace::new("/nmos").unwrap()
    }

    #[test]
    fn a_namespace_must_be_absolute_and_unterminated() {
        assert!(Namespace::new("/nmos").is_ok());
        assert!(Namespace::new("/a/b").is_ok());

        assert_eq!(
            Namespace::new("nmos").unwrap_err().message(),
            "namespace must start with '/': 'nmos'",
        );
        assert_eq!(
            Namespace::new("/nmos/").unwrap_err().message(),
            "namespace must not end with '/': '/nmos/'",
        );
        // "/" starts with a slash and ends with one, so the second rule wins,
        // exactly as it does in Python where the checks run in this order.
        assert_eq!(
            Namespace::new("/").unwrap_err().message(),
            "namespace must not end with '/': '/'",
        );
        assert!(Namespace::new("").is_err());
    }

    #[test]
    fn a_nodes_whole_subtree_is_one_prefix() {
        // The property the layout exists for: every key belonging to a Node
        // starts with that Node's subtree prefix, so one ranged delete and one
        // lease cover all of them.
        let ns = ns();
        let subtree = ns.node_subtree("n1");
        for key in [
            ns.node("n1"),
            ns.device("n1", "d1"),
            ns.device_subtree("n1", "d1"),
            ns.child(ResourceType::Sender, "n1", "d1", "s1").unwrap(),
            ns.child(ResourceType::Flow, "n1", "d1", "f1").unwrap(),
        ] {
            assert!(
                key.starts_with(&subtree),
                "{} escapes the subtree",
                String::from_utf8_lossy(&key),
            );
        }
        // And the flat claim deliberately does not.
        assert!(!ns.id_claim("n1").starts_with(&subtree));
    }

    #[test]
    fn a_key_round_trips_through_parse() {
        let ns = ns();
        let node = ns.parse(&ns.node("n1")).unwrap().unwrap();
        assert_eq!(node.resource_type, ResourceType::Node);
        assert_eq!(node.resource_id, "n1");
        assert_eq!(node.node_id, "n1");
        assert_eq!(node.device_id, None);
        assert!(node.is_node());
        assert_eq!(node.depth(), 0);

        let device = ns.parse(&ns.device("n1", "d1")).unwrap().unwrap();
        assert_eq!(device.resource_type, ResourceType::Device);
        assert_eq!(device.resource_id, "d1");
        assert_eq!(device.node_id, "n1");
        assert_eq!(device.device_id.as_deref(), Some("d1"));
        assert_eq!(device.depth(), 1);

        for kind in [
            ResourceType::Source,
            ResourceType::Flow,
            ResourceType::Sender,
            ResourceType::Receiver,
        ] {
            let key = ns.child(kind, "n1", "d1", "r1").unwrap();
            let parsed = ns.parse(&key).unwrap().unwrap();
            assert_eq!(parsed.resource_type, kind);
            assert_eq!(parsed.resource_id, "r1");
            assert_eq!(parsed.node_id, "n1");
            assert_eq!(parsed.device_id.as_deref(), Some("d1"));
            assert_eq!(parsed.depth(), 2);
        }
    }

    #[test]
    fn bookkeeping_keys_parse_to_nothing_rather_than_failing() {
        // The watch sees every key under the namespace. These two have no
        // representation in the local store, and making them errors would put
        // an error path on the ordinary case.
        let ns = ns();
        assert_eq!(ns.parse(&ns.meta_config()).unwrap(), None);
        assert_eq!(ns.parse(&ns.id_claim("n1")).unwrap(), None);
    }

    #[test]
    fn a_node_and_a_device_are_refused_their_own_child_key() {
        let ns = ns();
        assert_eq!(
            ns.child(ResourceType::Node, "n1", "d1", "x")
                .unwrap_err()
                .message(),
            "node has its own key function",
        );
        assert_eq!(
            ns.child(ResourceType::Device, "n1", "d1", "x")
                .unwrap_err()
                .message(),
            "device has its own key function",
        );
    }

    #[test]
    fn a_malformed_key_says_which_way_it_is_malformed() {
        let ns = ns();
        assert_eq!(
            ns.parse(b"/other/nodes/n1/self").unwrap_err().message(),
            "key outside namespace '/nmos': '/other/nodes/n1/self'",
        );
        // A prefix that is a prefix of the namespace but not the namespace.
        assert!(ns.parse(b"/nmosX/nodes/n1/self").is_err());
        assert_eq!(
            ns.parse(b"/nmos/things/x").unwrap_err().message(),
            "unrecognised key section 'things': '/nmos/things/x'",
        );
        assert_eq!(
            ns.parse(b"/nmos/nodes/n1/devices/d1/widgets/w1")
                .unwrap_err()
                .message(),
            "unknown collection 'widgets' in key \
             '/nmos/nodes/n1/devices/d1/widgets/w1'",
        );
        assert_eq!(
            ns.parse(b"/nmos/nodes/n1").unwrap_err().message(),
            "malformed resource key: '/nmos/nodes/n1'",
        );
        // A subtree prefix is not itself a resource key.
        assert_eq!(
            ns.parse(&ns.node_subtree("n1")).unwrap_err().message(),
            "malformed resource key: '/nmos/nodes/n1/'",
        );
        // The namespace root alone.
        assert_eq!(
            ns.parse(&ns.root()).unwrap_err().message(),
            "unrecognised key section '': '/nmos/'",
        );
    }

    #[test]
    fn an_envelope_round_trips_and_keeps_the_bodys_bytes() {
        // The spelling a re-encode would destroy, which is the whole reason
        // the body is spliced as text.
        let original = r#"{"id": "n1", "rate": 1e3, "label": "café"}"#;
        let envelope = Envelope {
            version: ENVELOPE_VERSION,
            resource_type: ResourceType::Node,
            body: Body::new(original),
            created: TaiCursor::new(10, 20),
            updated: TaiCursor::new(30, 40),
            health: 1_700_000_000,
        };
        let encoded = envelope.encode();
        assert_eq!(
            String::from_utf8(encoded.clone()).unwrap(),
            format!(
                "{{\"v\": 1, \"type\": \"node\", \"created\": \"10:20\", \
                 \"updated\": \"30:40\", \"health\": 1700000000, \"data\": {original}}}"
            ),
        );

        let decoded = Envelope::decode(&encoded).unwrap();
        assert_eq!(decoded, envelope);
        assert_eq!(decoded.body.text(), original, "the body was re-encoded");
    }

    #[test]
    fn a_body_containing_a_brace_or_a_string_slash_survives() {
        // The splice is textual, so a body whose own content resembles the
        // envelope's punctuation is the case that would break it.
        let original = r#"{"label": "}, \"data\": {\"id\": \"spoof\"}", "id": "n1"}"#;
        let envelope = Envelope {
            version: ENVELOPE_VERSION,
            resource_type: ResourceType::Sender,
            body: Body::new(original),
            created: TaiCursor::new(1, 2),
            updated: TaiCursor::new(1, 2),
            health: 5,
        };
        let decoded = Envelope::decode(&envelope.encode()).unwrap();
        assert_eq!(decoded.body.text(), original);
        assert_eq!(decoded.resource_type, ResourceType::Sender);
    }

    fn envelope_of(members: &str) -> String {
        format!("{{{members}}}")
    }

    #[test]
    fn an_unreadable_envelope_is_refused_with_the_reason() {
        let cases: &[(&str, &str)] = &[
            (
                r#""type": "node", "created": "1:2", "updated": "1:2", "health": 3, "data": {}"#,
                "envelope has no integer 'v'",
            ),
            (
                r#""v": 1, "created": "1:2", "updated": "1:2", "health": 3, "data": {}"#,
                "envelope has unknown type None",
            ),
            (
                r#""v": 1, "type": "nod", "created": "1:2", "updated": "1:2", "health": 3, "data": {}"#,
                "envelope has unknown type 'nod'",
            ),
            (
                r#""v": 1, "type": 7, "created": "1:2", "updated": "1:2", "health": 3, "data": {}"#,
                "envelope has unknown type 7",
            ),
            (
                r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "health": 3"#,
                "envelope has no 'data' object",
            ),
            (
                r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "health": 3, "data": []"#,
                "envelope has no 'data' object",
            ),
            (
                r#""v": 1, "type": "node", "updated": "1:2", "health": 3, "data": {}"#,
                "envelope has no string 'created'",
            ),
            (
                r#""v": 1, "type": "node", "created": "x", "updated": "1:2", "health": 3, "data": {}"#,
                "envelope 'created' is not '<sec>:<nsec>': 'x'",
            ),
            (
                r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "data": {}"#,
                "envelope has no integer 'health'",
            ),
            (
                r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "health": 3.5, "data": {}"#,
                "envelope has no integer 'health'",
            ),
        ];
        for (members, expected) in cases {
            let raw = envelope_of(members);
            let fault =
                Envelope::decode(raw.as_bytes()).expect_err(&format!("{raw} should not decode"));
            assert_eq!(fault.message(), *expected, "for {raw}");
        }
    }

    #[test]
    fn an_envelope_from_the_future_says_to_upgrade_rather_than_guessing() {
        let raw = envelope_of(
            r#""v": 2, "type": "node", "created": "1:2", "updated": "1:2", "health": 3, "data": {}"#,
        );
        assert_eq!(
            Envelope::decode(raw.as_bytes()).unwrap_err().message(),
            "envelope schema version 2 is newer than this registry understands (1). \
             Upgrade this member rather than letting it serve a partial view.",
        );
        // A literal beyond i64 is unambiguously from the future too, and says
        // so with the digits that were read.
        let huge = envelope_of(
            r#""v": 99999999999999999999, "type": "node", "created": "1:2", "updated": "1:2", "health": 3, "data": {}"#,
        );
        assert!(
            Envelope::decode(huge.as_bytes())
                .unwrap_err()
                .message()
                .starts_with("envelope schema version 99999999999999999999 is newer"),
        );
    }

    #[test]
    fn a_boolean_is_not_an_integer() {
        // In Python it would be: `isinstance(True, int)` is true, so the
        // obvious spelling of this check stores a health of `1` -- one second
        // after the TAI epoch -- and the resource expires on the next
        // collection pass, appearing and then vanishing. Refused on both
        // sides.
        for members in [
            r#""v": true, "type": "node", "created": "1:2", "updated": "1:2", "health": 5, "data": {}"#,
            r#""v": false, "type": "node", "created": "1:2", "updated": "1:2", "health": 5, "data": {}"#,
        ] {
            let raw = envelope_of(members);
            assert_eq!(
                Envelope::decode(raw.as_bytes()).unwrap_err().message(),
                "envelope has no integer 'v'",
                "for {raw}",
            );
        }
        for members in [
            r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "health": true, "data": {}"#,
            r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "health": false, "data": {}"#,
        ] {
            let raw = envelope_of(members);
            assert_eq!(
                Envelope::decode(raw.as_bytes()).unwrap_err().message(),
                "envelope has no integer 'health'",
                "for {raw}",
            );
        }
    }

    #[test]
    fn the_integers_a_boolean_decodes_to_are_still_accepted() {
        // The check a careless fix breaks: `False` is `0` and `True` is `1`,
        // and both are legitimate values. A health of 0 is the TAI epoch and a
        // version of 1 is what this registry writes.
        for (members, health) in [
            (
                r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "health": 0, "data": {}"#,
                0,
            ),
            (
                r#""v": 1, "type": "node", "created": "1:2", "updated": "1:2", "health": 1, "data": {}"#,
                1,
            ),
            (
                r#""v": 0, "type": "node", "created": "1:2", "updated": "1:2", "health": -1, "data": {}"#,
                -1,
            ),
        ] {
            let raw = envelope_of(members);
            let envelope = Envelope::decode(raw.as_bytes())
                .unwrap_or_else(|exc| panic!("{raw} should decode: {exc}"));
            assert_eq!(envelope.health, health, "for {raw}");
        }
    }

    #[test]
    fn not_json_and_not_an_object_are_told_apart() {
        // Two different operator actions: one means the value is corrupt, the
        // other means something wrote a scalar where a resource belongs.
        assert!(
            Envelope::decode(b"[1, 2]").unwrap_err().message() == "envelope is not a JSON object",
        );
        assert!(Envelope::decode(b"7").unwrap_err().message() == "envelope is not a JSON object");
        assert!(
            Envelope::decode(b"\"text\"").unwrap_err().message() == "envelope is not a JSON object",
        );
        // `NaN` is valid to Python's decoder and not to serde_json's, so the
        // verdict has to come from the same rules the scanner uses.
        assert_eq!(
            Envelope::decode(b"NaN").unwrap_err().message(),
            "envelope is not a JSON object",
        );
        let fault = Envelope::decode(b"{not json").unwrap_err();
        assert!(
            fault.message().starts_with("envelope is not valid JSON: "),
            "{}",
            fault.message(),
        );
    }

    #[test]
    fn a_value_that_is_not_utf8_is_refused_rather_than_mangled() {
        let fault = Envelope::decode(&[0xff, 0xfe]).unwrap_err();
        assert!(
            fault.message().starts_with("envelope is not valid JSON: "),
            "{}",
            fault.message(),
        );
    }
}
