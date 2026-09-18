// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Routing committed changes to the clients that asked for them.
//!
//! This is the divergence the whole crate is arranged around. Python routes
//! inline -- `SubscriptionManager.publish` (`subscriptions.py:404-420`) runs in
//! the same uninterrupted step as the mutation, because there was no lock to
//! hold and no other thread to block. Doing that here would put O(subscriptions)
//! filter evaluations **and a JSON parse** inside the exclusive write lock, in a
//! port whose entire purpose is multi-core write throughput.
//!
//! So a mutation does two things under the write lock -- apply, and append
//! `(seq, event)` -- and nothing else. What follows happens here, with no lock
//! on the store held:
//!
//! ```text
//! WRITE LOCK                        DRAIN AND ROUTE (no store lock held)
//!   store.insert_or_update()          registry.drain_commits()
//!   commits.extend(events)     O(1)   manager.routes()
//! DROP                                for each subscription:
//!                                       classify_batch()   <- parse happens here
//!                                       connection.enqueue_all()
//! ```
//!
//! # Why this cannot observe torn state
//!
//! A `ResourceEvent` carries `pre` and `post` as **owned body snapshots**, not
//! references into the store. Classification therefore needs nothing from the
//! store, and running it later cannot see a half-applied mutation. What the
//! sequence number preserves is the property that actually matters: grains are
//! queued in commit order and none is lost. What it gives up -- a window where
//! the store holds a change whose grain is not yet buffered -- was never
//! client-visible, because delivery was always asynchronous and rate-limited.
//!
//! # Why anchors are grouped rather than assumed equal
//!
//! Each connection anchors at the sequence its sync burst describes, and the
//! matcher owes it only what came after. Usually every connection on a
//! subscription is anchored below the whole batch and one classification serves
//! them all -- but a client that connects *while* a batch is being drained
//! anchors in the middle of it, and coalescing from the start of the batch would
//! then hand it a `pre` from before it connected. Grouping by anchor costs one
//! `HashMap` on a path that almost always has a single group, and removes the
//! race rather than making it unlikely.
//!
//! # What is deliberately not here yet
//!
//! **Sharding, and the driving task.** The plan keys matcher tasks by
//! subscription id so they scale out; that needs tokio and a socket to wake,
//! which is M5. [`route_once`] is the whole body of such a task and is called
//! directly by the standalone path in the meantime, so nothing here is waiting
//! on a caller that does not exist.

use std::collections::HashMap;
use std::sync::Arc;

use crate::commit::{Committed, Sequence};
use crate::connection::ConnectionBuffer;
use crate::manager::SubscriptionManager;
use crate::registry::{Registry, classify_batch};
use crate::subscription::Subscription;

/// What one routing pass did, for the metric the plan requires.
///
/// Queue depth is the overload signal for this design: the commit queue does
/// **not** coalesce -- merging an add and a remove into nothing would be an
/// observable divergence from Python -- so a matcher that cannot keep up grows
/// it. `Behaviour` is then still correct and memory is not, which is exactly the
/// failure that presents as unexplained growth without a counter to name it.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct RoutingReport {
    /// Commits taken off the queue.
    pub commits: usize,
    /// Subscriptions considered.
    pub subscriptions: usize,
    /// Connections that had at least one event queued.
    pub fed: usize,
    /// Events queued across every connection.
    pub queued: usize,
    /// The deepest the commit queue has been since the last reset.
    pub high_water: usize,
}

/// Drain the commit queue and queue what each connection is owed.
///
/// One pass. The caller decides the cadence: M5's matcher task calls it on a
/// wake, and a test calls it directly.
pub fn route_once(registry: &Registry, manager: &SubscriptionManager) -> RoutingReport {
    let batch = registry.drain_commits();
    let high_water = registry.commit_high_water();
    if batch.is_empty() {
        return RoutingReport {
            high_water,
            ..RoutingReport::default()
        };
    }

    let routes = manager.routes();
    let mut report = RoutingReport {
        commits: batch.len(),
        subscriptions: routes.len(),
        high_water,
        ..RoutingReport::default()
    };

    for (subscription, connections) in &routes {
        let (fed, queued) = route_subscription(subscription, connections, &batch);
        // Saturating, because the write path is lint-enforced panic-free: an
        // overflow here would be a counter wrapping, not a reason to poison a
        // registry mid-fan-out.
        report.fed = report.fed.saturating_add(fed);
        report.queued = report.queued.saturating_add(queued);
    }
    report
}

/// Feed one subscription's connections, classifying once per distinct anchor.
fn route_subscription(
    subscription: &Subscription,
    connections: &[Arc<ConnectionBuffer>],
    batch: &[Committed],
) -> (usize, usize) {
    if connections.is_empty() {
        // Nothing to feed. A persistent subscription with no client is a
        // legitimate steady state, and classifying for it would be pure waste --
        // the buffer that would hold the result does not exist yet, and the
        // client that eventually connects gets a sync burst instead.
        return (0, 0);
    }

    let mut by_anchor: HashMap<Sequence, Vec<&Arc<ConnectionBuffer>>> = HashMap::new();
    for connection in connections {
        by_anchor
            .entry(connection.anchor())
            .or_default()
            .push(connection);
    }

    let mut fed = 0_usize;
    let mut queued = 0_usize;
    for (anchor, group) in by_anchor {
        let events = classify_batch(subscription, batch, anchor);
        if events.is_empty() {
            continue;
        }
        for connection in group {
            if connection.is_closed() {
                // Reaped between `routes()` and here. Enqueueing is already a
                // no-op on a closed buffer; skipping keeps the report honest.
                continue;
            }
            connection.enqueue_all(events.iter().cloned());
            fed = fed.saturating_add(1);
            queued = queued.saturating_add(events.len());
        }
    }
    (fed, queued)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::manager::SubscriptionRequest;
    use nmos_registry_core::body::Body;
    use nmos_registry_core::resource_type::ResourceType;
    use nmos_registry_core::store::RegistryStore;

    fn request(resource_path: &str) -> SubscriptionRequest {
        SubscriptionRequest {
            resource_path: resource_path.to_owned(),
            params: Vec::new(),
            max_update_rate_ms: 0,
            persist: true,
            secure: false,
            authorization: false,
            host: "example.test".to_owned(),
            ws_scheme: "ws".to_owned(),
            ws_host: "example.test".to_owned(),
        }
    }

    fn filtered(resource_path: &str, key: &str, value: &str) -> SubscriptionRequest {
        let mut request = request(resource_path);
        request.params = vec![(key.to_owned(), value.to_owned())];
        request
    }

    fn node(id: &str, version: u32, label: &str) -> Body {
        Body::new(format!(
            r#"{{"id":"{id}","version":"{version}:0","label":"{label}"}}"#
        ))
    }

    fn put(registry: &Registry, id: &str, version: u32, label: &str) {
        registry
            .register(ResourceType::Node, node(id, version, label))
            .expect("the fixture body registers");
    }

    fn rig() -> (Registry, SubscriptionManager) {
        (
            Registry::new(RegistryStore::new()),
            SubscriptionManager::new(),
        )
    }

    fn connect(
        registry: &Registry,
        manager: &SubscriptionManager,
        request: &SubscriptionRequest,
    ) -> Arc<ConnectionBuffer> {
        let (subscription, _) = manager
            .create_or_match(request)
            .expect("a subscribable resource_path");
        let connection = manager
            .connect(registry, &subscription.id)
            .expect("the subscription exists");
        connection.drain(); // discard the sync burst; these tests are about grains
        connection
    }

    // -- the basic route ---------------------------------------------------

    #[test]
    fn a_change_reaches_the_connection_that_subscribed_to_its_type() {
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &request("/nodes"));

        put(&registry, "n1", 1, "one");
        let report = route_once(&registry, &manager);

        assert_eq!(report.commits, 1);
        assert_eq!(report.fed, 1);
        let queued = connection.drain();
        assert_eq!(queued.len(), 1);
        assert_eq!(queued[0].path, "n1");
        assert!(queued[0].pre.is_none(), "a first registration has no pre");
    }

    #[test]
    fn a_change_of_another_type_reaches_nobody() {
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &request("/senders"));

        put(&registry, "n1", 1, "one");
        let report = route_once(&registry, &manager);

        assert_eq!(report.commits, 1, "the commit was still drained");
        assert_eq!(report.fed, 0);
        assert!(connection.is_empty());
    }

    #[test]
    fn routing_an_empty_queue_does_nothing() {
        let (registry, manager) = rig();
        connect(&registry, &manager, &request("/nodes"));
        assert_eq!(route_once(&registry, &manager), RoutingReport::default());
    }

    #[test]
    fn draining_is_destructive_so_a_second_pass_re_sends_nothing() {
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &request("/nodes"));

        put(&registry, "n1", 1, "one");
        route_once(&registry, &manager);
        connection.drain();

        assert_eq!(route_once(&registry, &manager).commits, 0);
        assert!(connection.is_empty(), "the same change was routed twice");
    }

    // -- fan-out -----------------------------------------------------------

    #[test]
    fn one_change_fans_out_to_every_connection_on_every_matching_subscription() {
        let (registry, manager) = rig();
        let first = connect(&registry, &manager, &request("/nodes"));
        let second = connect(&registry, &manager, &request("/nodes"));
        // A second, differently-configured subscription on the same type.
        let mut other = request("/nodes");
        other.max_update_rate_ms = 500;
        let third = connect(&registry, &manager, &other);

        put(&registry, "n1", 1, "one");
        let report = route_once(&registry, &manager);

        assert_eq!(report.subscriptions, 2);
        assert_eq!(report.fed, 3);
        assert_eq!(report.queued, 3);
        for connection in [&first, &second, &third] {
            assert_eq!(connection.drain().len(), 1);
        }
    }

    #[test]
    fn each_connection_gets_its_own_copy_of_a_fanned_out_event() {
        // The Python defect this port does not inherit: `publish` hands one
        // mutable `_PendingEvent` to every connection, so one client's later
        // traffic could rewrite an event another had already drained. Here the
        // event is cloned per connection, and this pins that.
        let (registry, manager) = rig();
        let slow = connect(&registry, &manager, &request("/nodes"));
        let fast = connect(&registry, &manager, &request("/nodes"));

        put(&registry, "n1", 1, "one");
        route_once(&registry, &manager);

        let drained = slow.drain();
        assert_eq!(
            drained[0].post.as_ref().map(Body::text),
            Some(r#"{"id":"n1","version":"1:0","label":"one"}"#)
        );

        // A later change reaching only the connection that has not drained.
        put(&registry, "n1", 2, "two");
        route_once(&registry, &manager);

        assert_eq!(
            drained[0].post.as_ref().map(Body::text),
            Some(r#"{"id":"n1","version":"1:0","label":"one"}"#),
            "another connection's traffic rewrote an already-drained event",
        );
        assert_eq!(fast.drain().len(), 1);
    }

    #[test]
    fn a_subscription_with_no_connections_is_skipped_without_error() {
        let (registry, manager) = rig();
        manager
            .create_or_match(&request("/nodes"))
            .expect("subscribable");

        put(&registry, "n1", 1, "one");
        let report = route_once(&registry, &manager);
        assert_eq!(report.subscriptions, 1);
        assert_eq!(report.fed, 0);
    }

    #[test]
    fn a_closed_connection_is_not_fed() {
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &request("/nodes"));
        connection.close();

        put(&registry, "n1", 1, "one");
        assert_eq!(route_once(&registry, &manager).fed, 0);
        assert!(connection.is_empty());
    }

    // -- coalescing --------------------------------------------------------

    #[test]
    fn changes_within_one_batch_coalesce_to_the_net_change() {
        // What `max_update_rate_ms` means: the client sees the net change over
        // the window, not a replay of every intermediate state.
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &request("/nodes"));

        put(&registry, "n1", 1, "v1");
        put(&registry, "n1", 2, "v2");
        put(&registry, "n1", 3, "v3");
        route_once(&registry, &manager);

        let queued = connection.drain();
        assert_eq!(queued.len(), 1, "three updates must coalesce into one");
        assert!(
            queued[0]
                .post
                .as_ref()
                .is_some_and(|b| b.text().contains("v3")),
            "post must be the state after the LAST change",
        );
        assert!(
            queued[0].pre.is_none(),
            "pre must be the state before the FIRST change, which was absence",
        );
    }

    #[test]
    fn changes_across_two_batches_coalesce_in_the_buffer() {
        // Coalescing is the buffer's job as much as the batch's -- a client
        // that has not drained between passes still gets one entry.
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &request("/nodes"));

        put(&registry, "n1", 1, "v1");
        route_once(&registry, &manager);
        put(&registry, "n1", 2, "v2");
        route_once(&registry, &manager);

        let queued = connection.drain();
        assert_eq!(queued.len(), 1);
        assert!(
            queued[0]
                .post
                .as_ref()
                .is_some_and(|b| b.text().contains("v2"))
        );
    }

    // -- anchors -----------------------------------------------------------

    #[test]
    fn a_connection_is_not_re_sent_what_its_sync_burst_already_carried() {
        let (registry, manager) = rig();
        put(&registry, "n1", 1, "one");

        // Connect after the change: the burst carries it, so routing the batch
        // that contains it must queue nothing.
        let (subscription, _) = manager
            .create_or_match(&request("/nodes"))
            .expect("subscribable");
        let connection = manager
            .connect(&registry, &subscription.id)
            .expect("the subscription exists");
        assert_eq!(
            connection.drain().len(),
            1,
            "the burst carried the resource"
        );

        assert_eq!(route_once(&registry, &manager).fed, 0);
        assert!(
            connection.is_empty(),
            "the change was delivered twice -- once in the burst, once as a grain",
        );
    }

    #[test]
    fn a_connection_anchored_mid_batch_sees_only_what_came_after_it() {
        // The race the anchor grouping exists for: a client connects while a
        // batch is accumulating, so the batch spans its anchor. Coalescing from
        // the start of the batch would hand it a `pre` from before it existed.
        let (registry, manager) = rig();
        let early = connect(&registry, &manager, &request("/nodes"));

        put(&registry, "n1", 1, "v1");
        put(&registry, "n1", 2, "v2");

        // Connects here -- anchored above the two changes above, below the one
        // below. Its burst carries v2.
        let (subscription, _) = manager
            .create_or_match(&request("/nodes"))
            .expect("subscribable");
        let late = manager
            .connect(&registry, &subscription.id)
            .expect("exists");
        assert_eq!(late.drain().len(), 1);

        put(&registry, "n1", 3, "v3");
        route_once(&registry, &manager);

        let early_event = early.drain();
        let late_event = late.drain();
        assert_eq!(early_event.len(), 1);
        assert_eq!(late_event.len(), 1);
        assert!(
            early_event[0].pre.is_none(),
            "the early connection saw the resource appear",
        );
        assert!(
            late_event[0]
                .pre
                .as_ref()
                .is_some_and(|b| b.text().contains("v2")),
            "the late connection's pre must be the state it was last shown, \
             not the state before it connected: {:?}",
            late_event[0].pre.as_ref().map(Body::text),
        );
    }

    #[test]
    fn connections_sharing_an_anchor_are_classified_once() {
        // The grouping must not change what anyone receives -- only how often
        // the work is done.
        let (registry, manager) = rig();
        let first = connect(&registry, &manager, &request("/nodes"));
        let second = connect(&registry, &manager, &request("/nodes"));
        assert_eq!(first.anchor(), second.anchor());

        put(&registry, "n1", 1, "one");
        route_once(&registry, &manager);

        assert_eq!(first.drain(), second.drain());
    }

    // -- filters -----------------------------------------------------------

    #[test]
    fn a_filtered_subscription_sees_only_what_matches() {
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &filtered("/nodes", "label", "keep"));

        put(&registry, "n1", 1, "keep");
        put(&registry, "n2", 1, "drop");
        route_once(&registry, &manager);

        let queued = connection.drain();
        assert_eq!(queued.len(), 1);
        assert_eq!(queued[0].path, "n1");
    }

    #[test]
    fn a_resource_that_stops_matching_is_reported_as_removed() {
        // `:242-245` -- a filter transition is an add or a remove, not a
        // modification, and needs no extra state to detect.
        let (registry, manager) = rig();
        put(&registry, "n1", 1, "keep");
        let connection = connect(&registry, &manager, &filtered("/nodes", "label", "keep"));
        registry.drain_commits(); // the registration predates the subscription

        put(&registry, "n1", 2, "gone");
        route_once(&registry, &manager);

        let queued = connection.drain();
        assert_eq!(queued.len(), 1);
        assert!(
            queued[0].pre.is_some(),
            "pre is the last state that matched"
        );
        assert!(
            queued[0].post.is_none(),
            "a resource that stopped matching must be reported as removed",
        );
    }

    #[test]
    fn a_resource_that_starts_matching_is_reported_as_added() {
        let (registry, manager) = rig();
        put(&registry, "n1", 1, "other");
        let connection = connect(&registry, &manager, &filtered("/nodes", "label", "keep"));
        registry.drain_commits();

        put(&registry, "n1", 2, "keep");
        route_once(&registry, &manager);

        let queued = connection.drain();
        assert_eq!(queued.len(), 1);
        assert!(
            queued[0].pre.is_none(),
            "a resource that started matching must be reported as added",
        );
        assert!(queued[0].post.is_some());
    }

    #[test]
    fn a_resource_that_keeps_matching_is_reported_as_modified() {
        // The fourth row of the table, and the one that is easy to lose: a
        // change between two matching states must carry BOTH sides, not be
        // re-reported as an add.
        let (registry, manager) = rig();
        put(&registry, "n1", 1, "keep");
        let connection = connect(&registry, &manager, &filtered("/nodes", "label", "keep"));
        registry.drain_commits();

        // Same label, so it still matches; a different version, so it changed.
        registry
            .register(
                ResourceType::Node,
                Body::new(r#"{"id":"n1","version":"2:0","label":"keep","x":1}"#.to_owned()),
            )
            .expect("registers");
        route_once(&registry, &manager);

        let queued = connection.drain();
        assert_eq!(queued.len(), 1);
        assert!(
            queued[0].pre.is_some() && queued[0].post.is_some(),
            "a modification must carry both sides, not be reported as an add",
        );
    }

    #[test]
    fn two_subscriptions_on_different_types_do_not_see_each_other_s_changes() {
        let (registry, manager) = rig();
        let nodes = connect(&registry, &manager, &request("/nodes"));
        let senders = connect(&registry, &manager, &request("/senders"));

        put(&registry, "n1", 1, "one");
        route_once(&registry, &manager);

        assert_eq!(nodes.drain().len(), 1);
        assert!(
            senders.is_empty(),
            "a Node change reached a Sender subscriber"
        );
    }

    #[test]
    fn a_grain_carries_the_registered_bytes_rather_than_a_re_encoding() {
        // The byte-fidelity guarantee, end to end through the routing path:
        // register -> route -> drain -> build_grain. A re-encode would
        // normalise the escape and a Controller comparing the WebSocket view
        // with the HTTP one would see a spurious difference.
        let (registry, manager) = rig();
        let connection = connect(&registry, &manager, &request("/nodes"));

        // Spelled the way a JSON library that escapes non-ASCII emits it.
        let text = r#"{"id":"n1","version":"1:0","label":"café"}"#;
        registry
            .register(ResourceType::Node, Body::new(text.to_owned()))
            .expect("registers");
        route_once(&registry, &manager);

        let grain = crate::grain::build_grain(
            connection.subscription(),
            &connection.drain(),
            "query-1",
            nmos_registry_core::cursor::TaiCursor::new(1, 0),
        )
        .expect("the stored body is JSON");

        assert!(
            grain.contains(r#""label":"café""#),
            "the grain re-encoded the body instead of splicing the stored \
             text:\n{grain}",
        );
    }

    // -- deletion ----------------------------------------------------------

    #[test]
    fn a_deletion_cascade_reaches_the_subscriptions_for_each_type() {
        let (registry, manager) = rig();
        let nodes = connect(&registry, &manager, &request("/nodes"));

        put(&registry, "n1", 1, "one");
        route_once(&registry, &manager);
        nodes.drain();

        registry
            .delete(ResourceType::Node, "n1")
            .expect("the node was registered");
        route_once(&registry, &manager);

        let queued = nodes.drain();
        assert_eq!(queued.len(), 1);
        assert!(queued[0].pre.is_some());
        assert!(queued[0].post.is_none(), "a deletion has no post");
    }

    #[test]
    fn a_cascade_delete_reaches_the_subscription_for_each_child_type() {
        // `store.py`'s cascade: deleting a Node deletes everything beneath it,
        // and each subscriber must be told about its own type.
        let (registry, manager) = rig();
        let nodes = connect(&registry, &manager, &request("/nodes"));
        let devices = connect(&registry, &manager, &request("/devices"));
        let senders = connect(&registry, &manager, &request("/senders"));

        put(&registry, "n1", 1, "node");
        registry
            .register(
                ResourceType::Device,
                Body::new(r#"{"id":"d1","version":"1:0","node_id":"n1"}"#.to_owned()),
            )
            .expect("the parent Node exists");
        registry
            .register(
                ResourceType::Sender,
                Body::new(r#"{"id":"s1","version":"1:0","device_id":"d1"}"#.to_owned()),
            )
            .expect("the parent Device exists");
        route_once(&registry, &manager);
        for connection in [&nodes, &devices, &senders] {
            connection.drain();
        }

        registry
            .delete(ResourceType::Node, "n1")
            .expect("the Node was registered");
        route_once(&registry, &manager);

        for (name, connection, id) in [
            ("nodes", &nodes, "n1"),
            ("devices", &devices, "d1"),
            ("senders", &senders, "s1"),
        ] {
            let queued = connection.drain();
            assert_eq!(queued.len(), 1, "{name}: the cascade was not reported");
            assert_eq!(queued[0].path, id, "{name}");
            assert!(
                queued[0].post.is_none(),
                "{name}: a cascaded deletion must be reported as removed",
            );
        }
    }

    // -- the overload metric -----------------------------------------------

    #[test]
    fn the_report_carries_the_queue_depth_the_plan_requires_as_a_metric() {
        // The commit queue does not coalesce on purpose, so its depth is the
        // only signal that the matcher has fallen behind.
        let (registry, manager) = rig();
        connect(&registry, &manager, &request("/nodes"));

        for version in 1..=25 {
            put(&registry, &format!("n{version}"), version, "x");
        }
        let report = route_once(&registry, &manager);
        assert_eq!(report.commits, 25);
        assert!(
            report.high_water >= 25,
            "the backlog that was reached went unrecorded: {}",
            report.high_water,
        );
    }

    // -- concurrency -------------------------------------------------------

    #[test]
    fn a_burst_is_never_observable_as_a_half_filled_buffer() {
        // The deterministic half of obligation 2, and the one that catches the
        // likelier mistake: registering the connection and *then* building its
        // burst. The burst walk is O(extant), so with a large store that
        // version leaves the connection reachable-but-empty for milliseconds --
        // wide enough that an observer hits it every time rather than once in
        // a million.
        //
        // The invariant: no observer may ever see a connection whose buffer is
        // empty while the store it just anchored to was not. The real
        // implementation builds the burst before the connection exists at all,
        // so there is no such instant to find.
        use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
        use std::thread;

        const RESOURCES: usize = 20_000;

        let registry = Arc::new(Registry::new(RegistryStore::new()));
        for index in 0..RESOURCES {
            put(&registry, &format!("n{index}"), 1, "x");
        }
        registry.drain_commits();
        let manager = Arc::new(SubscriptionManager::new());
        let (subscription, _) = manager
            .create_or_match(&request("/nodes"))
            .expect("subscribable");

        let done = Arc::new(AtomicBool::new(false));
        let empty_sightings = Arc::new(AtomicUsize::new(0));
        let sightings = Arc::new(AtomicUsize::new(0));

        let observer = {
            let manager = Arc::clone(&manager);
            let done = Arc::clone(&done);
            let empty_sightings = Arc::clone(&empty_sightings);
            let sightings = Arc::clone(&sightings);
            let id = subscription.id.clone();
            thread::spawn(move || {
                while !done.load(Ordering::Acquire) {
                    // Exactly how the matcher reaches a buffer.
                    for connection in manager.connections(&id) {
                        if connection.is_closed() {
                            // A closed buffer is empty by design -- `close`
                            // discards what it held. Counting it would make
                            // the check fire on correct behaviour.
                            continue;
                        }
                        sightings.fetch_add(1, Ordering::Relaxed);
                        if connection.is_empty() {
                            empty_sightings.fetch_add(1, Ordering::Relaxed);
                        }
                    }
                    thread::yield_now();
                }
            })
        };

        // Held, not disconnected in the loop: a disconnected buffer is cleared,
        // and an observer that catches one in that state would report an empty
        // buffer for a reason that has nothing to do with the burst.
        let mut connections = Vec::new();
        for _ in 0..8 {
            let connection = manager
                .connect(&registry, &subscription.id)
                .expect("the subscription exists");
            assert_eq!(
                connection.depth(),
                RESOURCES,
                "connect returned before the burst was complete",
            );
            connections.push(connection);
        }
        done.store(true, Ordering::Release);
        observer.join().expect("the observer did not panic");
        for connection in &connections {
            manager.disconnect(connection);
        }

        assert!(
            sightings.load(Ordering::Relaxed) > 0,
            "the observer never saw a connection, so it proved nothing",
        );
        assert_eq!(
            empty_sightings.load(Ordering::Relaxed),
            0,
            "a connection was reachable before its sync burst was in it, out \
             of {} sightings",
            sightings.load(Ordering::Relaxed),
        );
    }

    #[test]
    fn no_change_is_lost_or_reordered_across_a_connect() {
        // Obligation 2 of the M4 gate, and the reason `connect` holds the
        // manager's write lock across the registry read.
        //
        // The failure it guards against needs a deletion to be visible at all.
        // If the burst lands *after* an event the matcher already queued, the
        // buffer coalesces them and keeps the burst's `post` -- so for an
        // update the next change repairs the view and the corruption is
        // invisible a moment later. For a **deletion** there is no next change:
        // the buffer is left saying the resource exists, permanently, and the
        // client's view never recovers. So this races connects against a
        // register/delete cycle and then checks, after everything has
        // quiesced, that nobody was left believing in a resource that is gone.
        //
        // # How much this actually proves
        //
        // It is a probabilistic detector, and it was measured rather than
        // assumed. Against a build that registers the connection before
        // priming its buffer, the window between the two is a few hundred
        // nanoseconds; instrumenting that build counted **zero** window
        // entries across 400 connects on a 12-core machine, and the test
        // passed. Widening the same window to 500us produced 5 entries and the
        // test failed on the first run, with exactly the assertion below.
        //
        // So: it detects the fault when the window is observable, and it does
        // not deterministically guard the ordering. What does guard it is the
        // write lock held across `Registry::connect`, plus
        // `a_burst_is_never_observable_as_a_half_filled_buffer` below, which
        // catches the wider and likelier version of the same mistake without
        // racing for it.
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::thread;

        const RESOURCES: usize = 6;
        const CONNECTS: usize = 400;

        let registry = Arc::new(Registry::new(RegistryStore::new()));
        let manager = Arc::new(SubscriptionManager::new());
        let (subscription, _) = manager
            .create_or_match(&request("/nodes"))
            .expect("subscribable");
        let done = Arc::new(AtomicBool::new(false));
        let connecting = Arc::new(AtomicBool::new(true));

        // Runs until the connects are done, rather than for a fixed count.
        // Measured: a fixed 40 rounds finishes in ~17 routing passes while the
        // connect loop is still starting, so the two phases barely overlap and
        // the race the test exists for is never attempted.
        let writer = {
            let registry = Arc::clone(&registry);
            let connecting = Arc::clone(&connecting);
            thread::spawn(move || {
                let mut round = 0_u32;
                while connecting.load(Ordering::Acquire) {
                    round += 1;
                    // A fresh id per round, so that a resource deleted here is
                    // never registered again. That is what makes a corrupted
                    // view permanent: with reused ids the next round's events
                    // overwrite the buffer and repair it, and the test goes
                    // blind to the very fault it exists for.
                    for index in 0..RESOURCES {
                        put(&registry, &format!("n{round}_{index}"), 1, "x");
                    }
                    for index in 0..RESOURCES {
                        registry.delete(ResourceType::Node, &format!("n{round}_{index}"));
                    }
                }
            })
        };

        let router = {
            let registry = Arc::clone(&registry);
            let manager = Arc::clone(&manager);
            let done = Arc::clone(&done);
            thread::spawn(move || {
                while !done.load(Ordering::Acquire) {
                    route_once(&registry, &manager);
                    thread::yield_now();
                }
            })
        };

        // Connect repeatedly, straight into the churn.
        let mut connections = Vec::new();
        for _ in 0..CONNECTS {
            connections.push(
                manager
                    .connect(&registry, &subscription.id)
                    .expect("the subscription is persistent"),
            );
            thread::yield_now();
        }

        connecting.store(false, Ordering::Release);
        writer.join().expect("the writer did not panic");
        done.store(true, Ordering::Release);
        router.join().expect("the router did not panic");
        // Everything committed is now routed, and every connection has been
        // attached for the whole of it.
        route_once(&registry, &manager);

        // The store ends empty -- the writer's last act is a delete.
        assert_eq!(registry.count_extant(ResourceType::Node), 0);

        for (index, connection) in connections.iter().enumerate() {
            // The net of the burst plus every grain, which is what this client
            // now believes.
            let mut view: HashMap<String, bool> = HashMap::new();
            for event in connection.drain() {
                view.insert(event.path, event.post.is_some());
            }
            for (path, present) in &view {
                assert!(
                    !present,
                    "connection {index} was left believing {path} exists, but \
                     every resource was deleted -- a sync burst landed behind \
                     a grain the matcher had already queued",
                );
            }
        }
    }

    #[test]
    fn routing_beside_writers_loses_nothing_and_reorders_nothing() {
        // Obligation 1 of the M4 gate: per resource, the `post` sequence a
        // connection observes is a subsequence of the actual value sequence,
        // with no reordering. That is what the commit sequence number now
        // carries instead of the lock.
        use std::sync::atomic::{AtomicBool, Ordering};
        use std::thread;

        let registry = Arc::new(Registry::new(RegistryStore::new()));
        let manager = Arc::new(SubscriptionManager::new());
        let connection = connect(&registry, &manager, &request("/nodes"));
        let done = Arc::new(AtomicBool::new(false));

        let writers: Vec<_> = (0..4)
            .map(|writer| {
                let registry = Arc::clone(&registry);
                thread::spawn(move || {
                    for version in 1..=200_u32 {
                        put(&registry, &format!("n{writer}"), version, "x");
                    }
                })
            })
            .collect();

        let router = {
            let registry = Arc::clone(&registry);
            let manager = Arc::clone(&manager);
            let done = Arc::clone(&done);
            thread::spawn(move || {
                while !done.load(Ordering::Acquire) {
                    route_once(&registry, &manager);
                    std::thread::yield_now();
                }
                route_once(&registry, &manager);
            })
        };

        // Observe from the buffer while it is being fed.
        let mut seen: HashMap<String, Vec<u32>> = HashMap::new();
        let observer = {
            let connection = Arc::clone(&connection);
            let done = Arc::clone(&done);
            thread::spawn(move || {
                let mut seen: HashMap<String, Vec<u32>> = HashMap::new();
                loop {
                    let finished = done.load(Ordering::Acquire);
                    for event in connection.drain() {
                        let text = event.post.as_ref().map(Body::text).unwrap_or_default();
                        let version: u32 = text
                            .split(r#""version":""#)
                            .nth(1)
                            .and_then(|rest| rest.split(':').next())
                            .and_then(|digits| digits.parse().ok())
                            .expect("every post carries a version");
                        seen.entry(event.path).or_default().push(version);
                    }
                    if finished {
                        return seen;
                    }
                    std::thread::yield_now();
                }
            })
        };

        for writer in writers {
            writer.join().expect("no writer panicked");
        }
        done.store(true, Ordering::Release);
        router.join().expect("the router did not panic");
        for (path, versions) in observer.join().expect("the observer did not panic") {
            seen.entry(path).or_default().extend(versions);
        }
        // Whatever the last pass left buffered.
        for event in connection.drain() {
            let text = event.post.as_ref().map(Body::text).unwrap_or_default();
            let version: u32 = text
                .split(r#""version":""#)
                .nth(1)
                .and_then(|rest| rest.split(':').next())
                .and_then(|digits| digits.parse().ok())
                .expect("every post carries a version");
            seen.entry(event.path).or_default().push(version);
        }

        assert_eq!(seen.len(), 4, "a whole resource went unobserved");
        for (path, versions) in &seen {
            let mut sorted = versions.clone();
            sorted.sort_unstable();
            assert_eq!(
                versions, &sorted,
                "{path}: a connection observed states out of order: {versions:?}",
            );
            assert_eq!(
                versions.last(),
                Some(&200),
                "{path}: the final state never arrived",
            );
        }
    }
}
