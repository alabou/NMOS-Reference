// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! One slot per resource type, and one per paging order.
//!
//! # Why these exist rather than `[T; 6]`
//!
//! An array subscripted by an enum-to-`usize` helper is total in fact and
//! partial in type: the compiler sees `container[i]` and has to admit it might
//! panic, so `clippy::indexing_slicing` fires -- and that lint is not noise
//! here, it is what keeps the store's write path panic-free. `parking_lot`
//! locks do not poison, so a panic mid-mutation leaves a half-applied store
//! with nothing to indicate it.
//!
//! Silencing the lint across the store would disable it exactly where it earns
//! its place. Instead the lookup is a `match` over the enum, which the compiler
//! checks for exhaustiveness and which has no failure case to lint. Adding a
//! seventh resource type then fails to compile here rather than panicking at
//! run time on a subscript nobody updated.
//!
//! The types are also self-documenting in a way the array was not: "there is
//! exactly one bucket per resource type" stops being a convention maintained by
//! the `slot` function and becomes what the type says.

use crate::resource::Order;
use crate::resource_type::ResourceType;

/// One `T` per [`ResourceType`].
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct PerType<T> {
    node: T,
    device: T,
    source: T,
    flow: T,
    sender: T,
    receiver: T,
}

impl<T> PerType<T> {
    /// The slot for one type.
    pub const fn get(&self, resource_type: ResourceType) -> &T {
        match resource_type {
            ResourceType::Node => &self.node,
            ResourceType::Device => &self.device,
            ResourceType::Source => &self.source,
            ResourceType::Flow => &self.flow,
            ResourceType::Sender => &self.sender,
            ResourceType::Receiver => &self.receiver,
        }
    }

    /// The slot for one type, mutably.
    pub const fn get_mut(&mut self, resource_type: ResourceType) -> &mut T {
        match resource_type {
            ResourceType::Node => &mut self.node,
            ResourceType::Device => &mut self.device,
            ResourceType::Source => &mut self.source,
            ResourceType::Flow => &mut self.flow,
            ResourceType::Sender => &mut self.sender,
            ResourceType::Receiver => &mut self.receiver,
        }
    }

    /// Every slot, paired with its type, in registration dependency order.
    pub fn iter(&self) -> impl Iterator<Item = (ResourceType, &T)> {
        ResourceType::ALL
            .into_iter()
            .map(move |resource_type| (resource_type, self.get(resource_type)))
    }

    /// Every slot's value, in registration dependency order.
    pub fn values(&self) -> impl Iterator<Item = &T> {
        self.iter().map(|(_, value)| value)
    }

    /// Apply a function to each slot, producing a new container.
    pub fn map<U>(&self, mut f: impl FnMut(ResourceType, &T) -> U) -> PerType<U> {
        PerType {
            node: f(ResourceType::Node, &self.node),
            device: f(ResourceType::Device, &self.device),
            source: f(ResourceType::Source, &self.source),
            flow: f(ResourceType::Flow, &self.flow),
            sender: f(ResourceType::Sender, &self.sender),
            receiver: f(ResourceType::Receiver, &self.receiver),
        }
    }
}

/// One `T` per [`Order`].
#[derive(Debug, Default, Clone, PartialEq, Eq)]
pub struct PerOrder<T> {
    created: T,
    updated: T,
}

impl<T> PerOrder<T> {
    /// The slot for one order.
    pub const fn get(&self, order: Order) -> &T {
        match order {
            Order::Created => &self.created,
            Order::Updated => &self.updated,
        }
    }

    /// The slot for one order, mutably.
    pub const fn get_mut(&mut self, order: Order) -> &mut T {
        match order {
            Order::Created => &mut self.created,
            Order::Updated => &mut self.updated,
        }
    }

    /// Every slot, paired with its order.
    pub fn iter(&self) -> impl Iterator<Item = (Order, &T)> {
        Order::ALL
            .into_iter()
            .map(move |order| (order, self.get(order)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_type_has_its_own_slot() {
        let mut container: PerType<usize> = PerType::default();
        for (n, resource_type) in ResourceType::ALL.into_iter().enumerate() {
            *container.get_mut(resource_type) = n;
        }
        for (n, resource_type) in ResourceType::ALL.into_iter().enumerate() {
            assert_eq!(
                *container.get(resource_type),
                n,
                "{resource_type} shares a slot with another type",
            );
        }
    }

    #[test]
    fn iteration_follows_registration_dependency_order() {
        // The same order `ResourceType::ALL` declares, because callers that
        // iterate buckets rely on a parent's bucket preceding its children's.
        let container: PerType<usize> = PerType::default();
        let seen: Vec<ResourceType> = container.iter().map(|(t, _)| t).collect();
        assert_eq!(seen, ResourceType::ALL.to_vec());
    }

    #[test]
    fn every_order_has_its_own_slot() {
        let mut container: PerOrder<&str> = PerOrder::default();
        *container.get_mut(Order::Created) = "c";
        *container.get_mut(Order::Updated) = "u";
        assert_eq!(*container.get(Order::Created), "c");
        assert_eq!(*container.get(Order::Updated), "u");
    }

    #[test]
    fn map_visits_every_slot_exactly_once() {
        let container: PerType<usize> = PerType::default();
        let mut visits = 0;
        let mapped = container.map(|_, value| {
            visits += 1;
            value + 1
        });
        assert_eq!(visits, 6);
        assert!(mapped.values().all(|v| *v == 1));
    }
}
