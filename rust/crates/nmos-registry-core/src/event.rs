// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A single change to a single resource, ready to become grain data.
//!
//! `Behaviour - Querying.md:85-210` defines the four event shapes by **which of
//! `pre` and `post` are present**, not by a discriminator on the wire. Naming
//! them keeps the intent readable at the call sites; the grain builder turns
//! the name back into the pre/post shape.
//!
//! # Why an event carries bodies, not references into the store
//!
//! This is what lets subscription matching leave the write lock. `pre` and
//! `post` are owned snapshots -- `Arc`-shared, so carrying them is a pointer
//! copy -- which means classifying an event later can never observe torn state,
//! because it is not reading the store at all. A matcher running after the lock
//! has dropped sees exactly what the mutation saw.
//!
//! It is also what lets a grain emit the same bytes the Query API serves: the
//! grain splices `Body::text` verbatim rather than re-encoding a parsed value.

use crate::body::Body;
use crate::resource::{RegisteredResource, ResourceId};
use crate::resource_type::ResourceType;

/// Which of the four Query API WebSocket event shapes this is.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventKind {
    /// `post` only: the resource did not exist before.
    Added,
    /// `pre` only: the resource has gone.
    Removed,
    /// Both, and they differ.
    Modified,
    /// Both, identical. `Behaviour - Querying.md:166` -- the initial burst
    /// that tells a newly connected client the current state of the topic.
    Sync,
}

impl EventKind {
    /// The name used in this project's logs and tests.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Added => "added",
            Self::Removed => "removed",
            Self::Modified => "modified",
            Self::Sync => "sync",
        }
    }
}

/// One change to one resource.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResourceEvent {
    /// Which shape this is.
    pub kind: EventKind,
    /// The type of the changed resource.
    pub resource_type: ResourceType,
    /// The id of the changed resource.
    pub resource_id: ResourceId,
    /// The body before the change, if there was one.
    pub pre: Option<Body>,
    /// The body after the change, if there is one.
    pub post: Option<Body>,
}

impl ResourceEvent {
    /// A resource that did not exist before.
    #[must_use]
    pub fn added(resource: &RegisteredResource) -> Self {
        Self {
            kind: EventKind::Added,
            resource_type: resource.resource_type,
            resource_id: resource.id.clone(),
            pre: None,
            post: Some(resource.body.clone()),
        }
    }

    /// A resource that has gone.
    #[must_use]
    pub fn removed(resource: &RegisteredResource) -> Self {
        Self {
            kind: EventKind::Removed,
            resource_type: resource.resource_type,
            resource_id: resource.id.clone(),
            pre: Some(resource.body.clone()),
            post: None,
        }
    }

    /// A resource whose body changed.
    ///
    /// `pre` is passed in rather than read from the record, because by the time
    /// an event is built the record already holds the new body.
    #[must_use]
    pub fn modified(pre: Body, resource: &RegisteredResource) -> Self {
        Self {
            kind: EventKind::Modified,
            resource_type: resource.resource_type,
            resource_id: resource.id.clone(),
            pre: Some(pre),
            post: Some(resource.body.clone()),
        }
    }

    /// The current state of a resource, for a newly connected client.
    #[must_use]
    pub fn sync(resource: &RegisteredResource) -> Self {
        Self {
            kind: EventKind::Sync,
            resource_type: resource.resource_type,
            resource_id: resource.id.clone(),
            pre: Some(resource.body.clone()),
            post: Some(resource.body.clone()),
        }
    }
}

/// Why a registration was refused.
///
/// The 400-yielding conditions of `Behaviour - Registration.md:98-104`, as an
/// enum rather than free text: the handler maps each to a fixed status, and a
/// test can assert on the condition rather than on message wording.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum RegistrationError {
    /// The body does not meet the JSON schema for its type (`:100`).
    Schema,
    /// The id is already used by another resource type (`:101`).
    IdTypeConflict,
    /// The version is earlier than the stored one (`:102`).
    VersionRegression,
    /// A parent resource id was modified during an update (`:103`).
    ParentChanged,
    /// The parent is absent, or the id names the wrong type (`:104`).
    ParentMissing,
}

impl RegistrationError {
    /// The stable identifier for this condition.
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Schema => "schema",
            Self::IdTypeConflict => "id_type_conflict",
            Self::VersionRegression => "version_regression",
            Self::ParentChanged => "parent_changed",
            Self::ParentMissing => "parent_missing",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cursor::TaiCursor;

    fn a_resource(text: &str) -> RegisteredResource {
        RegisteredResource::new(
            ResourceType::Sender,
            "3b8be755-08ff-452b-b217-c9151eb21193",
            Body::new(text),
            "1700000000:0",
            TaiCursor::new(10, 0),
            TaiCursor::new(20, 0),
            None,
        )
    }

    #[test]
    fn the_shape_is_which_sides_are_present() {
        // `Behaviour - Querying.md:85-210` defines the four this way, so this
        // is the protocol and not an internal convention.
        let resource = a_resource(r#"{"a":1}"#);

        let added = ResourceEvent::added(&resource);
        assert!(added.pre.is_none() && added.post.is_some());

        let removed = ResourceEvent::removed(&resource);
        assert!(removed.pre.is_some() && removed.post.is_none());

        let modified = ResourceEvent::modified(Body::new(r#"{"a":0}"#), &resource);
        assert!(modified.pre.is_some() && modified.post.is_some());
        assert_ne!(modified.pre, modified.post);

        let sync = ResourceEvent::sync(&resource);
        assert!(sync.pre.is_some() && sync.post.is_some());
        assert_eq!(
            sync.pre, sync.post,
            "a sync event's two sides are identical"
        );
    }

    #[test]
    fn an_event_carries_the_bytes_not_a_reference_to_the_store() {
        // The property that lets matching run outside the write lock: the
        // event is complete on its own, so a later classification cannot
        // observe a record that has since changed.
        let resource = a_resource(r#"{"a":1}"#);
        let event = ResourceEvent::added(&resource);

        drop(resource);
        assert_eq!(
            event.post.as_ref().map(Body::text),
            Some(r#"{"a":1}"#),
            "the body did not survive the record it came from",
        );
    }

    #[test]
    fn carrying_a_body_shares_it() {
        // Fanning one change to fifty subscriptions must copy fifty pointers,
        // not fifty bodies.
        let resource = a_resource(r#"{"a":1}"#);
        let event = ResourceEvent::added(&resource);
        let _ = resource.body.data();
        assert!(
            event.post.as_ref().is_some_and(Body::is_parsed),
            "the event holds a copy rather than a share",
        );
    }

    #[test]
    fn the_registration_errors_are_the_documented_conditions() {
        for (error, name) in [
            (RegistrationError::Schema, "schema"),
            (RegistrationError::IdTypeConflict, "id_type_conflict"),
            (RegistrationError::VersionRegression, "version_regression"),
            (RegistrationError::ParentChanged, "parent_changed"),
            (RegistrationError::ParentMissing, "parent_missing"),
        ] {
            assert_eq!(error.as_str(), name);
        }
    }
}
