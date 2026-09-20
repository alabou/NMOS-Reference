// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Where a resource lives, on whose lease, and what a write must compare.
//!
//! Split out of the backend because these are the decisions that are pure
//! given the local store: they can be tested without a cluster, and getting
//! one of them wrong is the difference between a transaction that enforces
//! correctness and one that merely looks like it does.

use nmos_registry_core::{RegistrationError, RegistrationFailure, ResourceType};
use serde_json::Value;

use crate::keys::Namespace;

/// Where one resource is written, and what its write depends on.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Placement {
    /// The resource's own id.
    pub resource_id: String,
    /// The Node whose subtree it belongs to, and whose lease it hangs off.
    pub node_id: String,
    /// The key it is stored under.
    pub key: Vec<u8>,
    /// The flat id claim proving no other type holds this id.
    pub claim: Vec<u8>,
    /// The key whose existence this write depends on, or `None` for a Node.
    pub parent: Option<Vec<u8>>,
    /// The lease every key in this subtree hangs off. Zero until granted.
    pub lease: i64,
}

impl Placement {
    /// The same placement, with a lease attached.
    #[must_use]
    pub fn with_lease(mut self, lease: i64) -> Self {
        self.lease = lease;
        self
    }
}

/// What the local store must be able to answer for a placement to be decided.
///
/// A trait rather than the store itself, so the decision is testable without
/// building one: the only question ever asked is "which Node does this Device
/// belong to?".
pub trait ParentLookup {
    /// The Node id of a registered Device, or `None` when it is not here.
    fn node_of_device(&self, device_id: &str) -> Option<String>;
}

/// Work out where a resource lives, and on whose lease.
///
/// Every resource belongs to exactly one Node subtree, so the Node id has to
/// be resolvable before anything can be written. For a Device it is in the
/// body; for a Source/Flow/Sender/Receiver it is the Device's Node, which is
/// looked up locally -- and if the Device is not here yet, that is a genuine
/// `PARENT_MISSING`, decided by the same store rule that governs it in
/// standalone mode.
///
/// # Errors
///
/// A `RegistrationFailure` the caller may return as a 400 -- but only after
/// fencing, when it came from the optimistic path. See the backend's
/// `register`.
pub fn placement_for(
    namespace: &Namespace,
    resource_type: ResourceType,
    raw: &Value,
    lease_of: impl Fn(&str) -> i64,
    parents: &impl ParentLookup,
) -> Result<Placement, RegistrationFailure> {
    let resource_id = raw
        .get("id")
        .and_then(Value::as_str)
        .filter(|id| !id.is_empty())
        .ok_or_else(|| {
            RegistrationFailure::new(RegistrationError::Schema, "resource has no 'id' attribute")
        })?
        .to_owned();

    let claim = namespace.id_claim(&resource_id);

    if resource_type == ResourceType::Node {
        let lease = lease_of(&resource_id);
        return Ok(Placement {
            key: namespace.node(&resource_id),
            node_id: resource_id.clone(),
            resource_id,
            claim,
            parent: None,
            lease,
        });
    }

    if resource_type == ResourceType::Device {
        let node_id = raw
            .get("node_id")
            .and_then(Value::as_str)
            .ok_or_else(|| {
                RegistrationFailure::new(
                    RegistrationError::Schema,
                    "device is missing its 'node_id' attribute",
                )
            })?
            .to_owned();
        let lease = lease_of(&node_id);
        return Ok(Placement {
            key: namespace.device(&node_id, &resource_id),
            parent: Some(namespace.node(&node_id)),
            resource_id,
            node_id,
            claim,
            lease,
        });
    }

    let device_id = raw
        .get("device_id")
        .and_then(Value::as_str)
        .ok_or_else(|| {
            RegistrationFailure::new(
                RegistrationError::Schema,
                format!("{resource_type} is missing its 'device_id' attribute"),
            )
        })?;

    let node_id = parents.node_of_device(device_id).ok_or_else(|| {
        RegistrationFailure::new(
            RegistrationError::ParentMissing,
            format!("parent device {device_id} is not registered"),
        )
    })?;

    let key = namespace
        .child(resource_type, &node_id, device_id, &resource_id)
        .map_err(|fault| {
            // Unreachable: `child` refuses only a Node or a Device, and both
            // returned above. Turned into a failure rather than unwrapped
            // because this crate's mutation path is held to being panic-free.
            RegistrationFailure::new(RegistrationError::Schema, fault.message())
        })?;
    let lease = lease_of(&node_id);
    Ok(Placement {
        key,
        parent: Some(namespace.device(&node_id, device_id)),
        resource_id,
        node_id,
        claim,
        lease,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    struct Devices(Vec<(&'static str, &'static str)>);

    impl ParentLookup for Devices {
        fn node_of_device(&self, device_id: &str) -> Option<String> {
            self.0
                .iter()
                .find(|(id, _)| *id == device_id)
                .map(|(_, node)| (*node).to_owned())
        }
    }

    fn ns() -> Namespace {
        Namespace::new("/nmos").unwrap()
    }

    #[test]
    fn a_node_is_its_own_subtree_and_has_no_parent() {
        let placement = placement_for(
            &ns(),
            ResourceType::Node,
            &json!({"id": "n1"}),
            |_| 0,
            &Devices(vec![]),
        )
        .unwrap();
        assert_eq!(placement.node_id, "n1");
        assert_eq!(placement.key, b"/nmos/nodes/n1/self".to_vec());
        assert_eq!(placement.claim, b"/nmos/ids/n1".to_vec());
        assert_eq!(placement.parent, None);
    }

    #[test]
    fn a_device_takes_its_node_from_the_body() {
        let placement = placement_for(
            &ns(),
            ResourceType::Device,
            &json!({"id": "d1", "node_id": "n1"}),
            |node| i64::from(node == "n1") * 77,
            &Devices(vec![]),
        )
        .unwrap();
        assert_eq!(placement.node_id, "n1");
        assert_eq!(placement.key, b"/nmos/nodes/n1/devices/d1/self".to_vec());
        assert_eq!(placement.parent, Some(b"/nmos/nodes/n1/self".to_vec()));
        // A Device hangs off its NODE's lease, not one of its own.
        assert_eq!(placement.lease, 77);
    }

    #[test]
    fn a_child_takes_its_node_from_the_local_store() {
        // The Device's Node is not in the child's body, so it has to be looked
        // up -- and a Device that is not here yet is a genuine PARENT_MISSING.
        let placement = placement_for(
            &ns(),
            ResourceType::Sender,
            &json!({"id": "s1", "device_id": "d1"}),
            |_| 0,
            &Devices(vec![("d1", "n1")]),
        )
        .unwrap();
        assert_eq!(placement.node_id, "n1");
        assert_eq!(
            placement.key,
            b"/nmos/nodes/n1/devices/d1/senders/s1".to_vec(),
        );
        assert_eq!(
            placement.parent,
            Some(b"/nmos/nodes/n1/devices/d1/self".to_vec()),
        );
    }

    #[test]
    fn a_child_of_an_unknown_device_is_parent_missing() {
        let failure = placement_for(
            &ns(),
            ResourceType::Flow,
            &json!({"id": "f1", "device_id": "nope"}),
            |_| 0,
            &Devices(vec![]),
        )
        .unwrap_err();
        assert_eq!(failure.error, RegistrationError::ParentMissing);
        assert_eq!(failure.detail, "parent device nope is not registered");
    }

    #[test]
    fn a_missing_id_is_a_schema_failure_for_every_type() {
        for kind in ResourceType::ALL {
            let failure =
                placement_for(&ns(), kind, &json!({}), |_| 0, &Devices(vec![])).unwrap_err();
            assert_eq!(failure.error, RegistrationError::Schema, "{kind}");
            assert_eq!(failure.detail, "resource has no 'id' attribute");
        }
        // An empty id is no id.
        let failure = placement_for(
            &ns(),
            ResourceType::Node,
            &json!({"id": ""}),
            |_| 0,
            &Devices(vec![]),
        )
        .unwrap_err();
        assert_eq!(failure.error, RegistrationError::Schema);
    }

    #[test]
    fn the_missing_parent_attribute_names_the_type() {
        let failure = placement_for(
            &ns(),
            ResourceType::Device,
            &json!({"id": "d1"}),
            |_| 0,
            &Devices(vec![]),
        )
        .unwrap_err();
        assert_eq!(failure.detail, "device is missing its 'node_id' attribute");

        let failure = placement_for(
            &ns(),
            ResourceType::Receiver,
            &json!({"id": "r1"}),
            |_| 0,
            &Devices(vec![]),
        )
        .unwrap_err();
        assert_eq!(
            failure.detail,
            "receiver is missing its 'device_id' attribute",
        );
    }

    #[test]
    fn attaching_a_lease_leaves_everything_else_alone() {
        let placement = placement_for(
            &ns(),
            ResourceType::Node,
            &json!({"id": "n1"}),
            |_| 0,
            &Devices(vec![]),
        )
        .unwrap();
        let leased = placement.clone().with_lease(42);
        assert_eq!(leased.lease, 42);
        assert_eq!(leased.key, placement.key);
        assert_eq!(leased.claim, placement.claim);
    }
}
