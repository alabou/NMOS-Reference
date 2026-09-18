// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The six registerable IS-04 resource types, and how they relate.
//!
//! # Why this is an enum and not a string
//!
//! IS-04 uses the singular form on the Registration API wire
//! (`{"type": "sender", ...}`) and the plural form in URLs
//! (`/resource/senders/{id}`, `/senders`). Converting between them by string
//! surgery is how the AMWA mock registry ends up doing
//! `resource_type.rstrip("s")` -- which strips *every* trailing `s`, so a
//! hypothetical "status" type becomes "statu". Both forms are held explicitly
//! here, and only the exact spellings the RAML enumerates parse.

use std::fmt;

/// A registerable IS-04 resource type.
///
/// The declaration order is the registration dependency order required by
/// `Behaviour - Registration.md:57-64` -- Node, then Devices, then Sources,
/// Flows, Senders, Receivers. Several places iterate these and rely on that
/// order, so it is load-bearing and must not be sorted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ResourceType {
    /// A Node.
    Node,
    /// A Device, whose parent is a Node.
    Device,
    /// A Source, whose parent is a Device.
    Source,
    /// A Flow, whose parent is a Device.
    Flow,
    /// A Sender, whose parent is a Device.
    Sender,
    /// A Receiver, whose parent is a Device.
    Receiver,
}

impl ResourceType {
    /// Every type, in registration dependency order.
    pub const ALL: [Self; 6] = [
        Self::Node,
        Self::Device,
        Self::Source,
        Self::Flow,
        Self::Sender,
        Self::Receiver,
    ];

    /// The singular name, as used in the Registration API POST envelope's
    /// `type` field (`registrationapi-resource-post-request.json`).
    #[must_use]
    pub const fn singular(self) -> &'static str {
        match self {
            Self::Node => "node",
            Self::Device => "device",
            Self::Source => "source",
            Self::Flow => "flow",
            Self::Sender => "sender",
            Self::Receiver => "receiver",
        }
    }

    /// The URL segment, as fixed by the `resourceType` enum in
    /// `RegistrationAPI.raml:75-82` and the collection names in
    /// `queryapi-base.json`.
    #[must_use]
    pub const fn plural(self) -> &'static str {
        match self {
            Self::Node => "nodes",
            Self::Device => "devices",
            Self::Source => "sources",
            Self::Flow => "flows",
            Self::Sender => "senders",
            Self::Receiver => "receivers",
        }
    }

    /// The Query API WebSocket grain `topic` for this type.
    ///
    /// `Behaviour - Querying.md:49`: the grain `topic` and the event `path`
    /// together form the Query API resource path, so the topic is the
    /// collection path with **both** slashes -- `/senders/`.
    #[must_use]
    pub const fn topic(self) -> &'static str {
        match self {
            Self::Node => "/nodes/",
            Self::Device => "/devices/",
            Self::Source => "/sources/",
            Self::Flow => "/flows/",
            Self::Sender => "/senders/",
            Self::Receiver => "/receivers/",
        }
    }

    /// Parse the Registration API POST envelope's `type` value.
    ///
    /// Exact match only, and `None` rather than an error, because the caller
    /// turns an unparseable type into an HTTP 400 with a useful message.
    #[must_use]
    pub fn from_singular(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|t| t.singular() == name)
    }

    /// Parse a URL collection segment (`senders` -> `Sender`).
    ///
    /// Exact match only. This is what keeps a bad path segment out of the
    /// store instead of silently aliasing onto a real type.
    #[must_use]
    pub fn from_plural(name: &str) -> Option<Self> {
        Self::ALL.into_iter().find(|t| t.plural() == name)
    }

    /// The body attribute naming this resource's parent, per
    /// `Behaviour - Registration.md:57-64`. `None` for a Node, which has none.
    ///
    /// v1.0 Flows had no `device_id` and were collected via `source_id`
    /// (`:66`); this registry serves v1.3 only, where `device_id` is required
    /// on a Flow, so that fallback does not apply.
    #[must_use]
    pub const fn parent_key(self) -> Option<&'static str> {
        match self {
            Self::Node => None,
            Self::Device => Some("node_id"),
            Self::Source | Self::Flow | Self::Sender | Self::Receiver => Some("device_id"),
        }
    }

    /// The type this type's parent reference must point at.
    ///
    /// Enforces `Behaviour - Registration.md:104`: "the parent resource
    /// referred to either doesn't exist in the registry or the ID matches the
    /// wrong type of resource" is a 400.
    #[must_use]
    pub const fn parent_type(self) -> Option<Self> {
        match self {
            Self::Node => None,
            Self::Device => Some(Self::Node),
            Self::Source | Self::Flow | Self::Sender | Self::Receiver => Some(Self::Device),
        }
    }
}

impl fmt::Display for ResourceType {
    /// The singular form, which is what error messages and the POST envelope
    /// both use.
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.singular())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn declaration_order_is_registration_dependency_order() {
        // Load-bearing: cascades, GC and the discovery ladders all iterate
        // this and assume a parent precedes its children.
        assert_eq!(
            ResourceType::ALL,
            [
                ResourceType::Node,
                ResourceType::Device,
                ResourceType::Source,
                ResourceType::Flow,
                ResourceType::Sender,
                ResourceType::Receiver,
            ],
        );
        for (position, kind) in ResourceType::ALL.iter().enumerate() {
            if let Some(parent) = kind.parent_type() {
                let parent_position = ResourceType::ALL
                    .iter()
                    .position(|t| *t == parent)
                    .expect("the parent is a resource type");
                assert!(
                    parent_position < position,
                    "{kind} precedes its own parent {parent}",
                );
            }
        }
    }

    #[test]
    fn the_plural_is_not_the_singular_plus_s_by_surgery() {
        // It happens to be, for all six. The point is that it is declared, so
        // a future type whose plural is irregular cannot be mangled by a
        // `rstrip("s")` that a reader assumed was general.
        for kind in ResourceType::ALL {
            assert_eq!(ResourceType::from_plural(kind.plural()), Some(kind));
            assert_eq!(ResourceType::from_singular(kind.singular()), Some(kind));
        }
    }

    #[test]
    fn the_topic_carries_both_slashes() {
        // `Behaviour - Querying.md:49`. A topic of `/senders` rather than
        // `/senders/` concatenates with the event path into `/senders<id>`.
        for kind in ResourceType::ALL {
            let topic = kind.topic();
            assert!(topic.starts_with('/'), "{kind}: {topic}");
            assert!(topic.ends_with('/'), "{kind}: {topic}");
            assert_eq!(topic, format!("/{}/", kind.plural()));
        }
    }

    #[test]
    fn parsing_is_exact() {
        assert_eq!(
            ResourceType::from_singular("sender"),
            Some(ResourceType::Sender)
        );
        // Not the plural, not a prefix, not a different case, not padded.
        for bad in [
            "senders", "send", "Sender", "SENDER", " sender", "sender ", "",
        ] {
            assert_eq!(ResourceType::from_singular(bad), None, "singular {bad:?}");
        }
        for bad in ["sender", "senderss", "Senders", " senders", ""] {
            assert_eq!(ResourceType::from_plural(bad), None, "plural {bad:?}");
        }
    }

    #[test]
    fn only_a_node_has_no_parent() {
        for kind in ResourceType::ALL {
            assert_eq!(
                kind.parent_key().is_none(),
                kind == ResourceType::Node,
                "{kind}",
            );
            assert_eq!(kind.parent_key().is_none(), kind.parent_type().is_none());
        }
        assert_eq!(ResourceType::Device.parent_key(), Some("node_id"));
        assert_eq!(ResourceType::Flow.parent_key(), Some("device_id"));
    }
}
