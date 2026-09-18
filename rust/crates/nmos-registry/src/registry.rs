// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The registry: the store and the commit queue, behind one lock.
//!
//! Every mutation here has the same shape, and it is the shape the whole
//! concurrency model rests on:
//!
//! ```text
//! with_write(|core| {
//!     core.store.mutate(...);          // the change
//!     core.commits.extend(events);     // its events, in order
//!     something_owned                  // never a borrow
//! })
//! ```
//!
//! Nothing else happens in there. No classification, no filter evaluation, no
//! JSON parsing, no grain construction -- those run on the drained queue with
//! no lock held. See [`crate::commit`] for why, and what it gives up.
//!
//! # Reads copy out, then encode
//!
//! `with_read` returns owned values, so a response is built *after* the guard
//! drops. That is not a limitation to work around: encoding a page of fifty
//! bodies under the lock would block every writer for the duration, and the
//! whole point of the port is write throughput. The type system makes the
//! right structure the only available one.

use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::event::ResourceEvent;
use nmos_registry_core::paging::{Page, PageWindow, PagingRequest, apply_paging};
use nmos_registry_core::query_filter::matches;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::{Applied, RegistrationFailure, RegistryStatistics, RegistryStore};

use crate::commit::{CommitQueue, Committed, Sequence};
use crate::lock::Locked;
use crate::subscription::{PendingEvent, Subscription, classify};

/// Everything the lock protects.
///
/// The store and the queue are one unit because a mutation must append its
/// events in the same critical section that applies the change -- otherwise a
/// second writer could interleave and the queue order would stop matching the
/// commit order.
#[derive(Debug)]
pub struct RegistryCore {
    /// The resources.
    pub store: RegistryStore,
    /// The changes awaiting classification.
    pub commits: CommitQueue,
}

impl RegistryCore {
    /// Wrap a store.
    #[must_use]
    pub fn new(store: RegistryStore) -> Self {
        Self {
            store,
            commits: CommitQueue::new(),
        }
    }
}

/// A resource's identity and body, copied out of the store.
///
/// What a read returns instead of a reference. Cloning a [`Body`] is a pointer
/// copy, so this is cheap -- and it is what lets the response be encoded after
/// the guard has dropped.
#[derive(Debug, Clone)]
pub struct ResourceSnapshot {
    /// Which type it is.
    pub resource_type: ResourceType,
    /// Its id.
    pub id: String,
    /// Its body.
    pub body: Body,
    /// Its creation cursor.
    pub created: TaiCursor,
    /// Its update cursor.
    pub updated: TaiCursor,
}

/// A page, copied out of the store.
#[derive(Debug, Clone)]
pub struct PageSnapshot {
    /// The bodies, most recent first.
    pub resources: Vec<ResourceSnapshot>,
    /// What to report in `X-Paging-Since`.
    pub since: TaiCursor,
    /// What to report in `X-Paging-Until`.
    pub until: TaiCursor,
    /// What to report in `X-Paging-Limit`.
    pub limit: usize,
}

impl PageSnapshot {
    /// The window the `X-Paging-*` headers and `Link` URLs are built from.
    #[must_use]
    pub const fn window(&self) -> PageWindow {
        PageWindow {
            since: self.since,
            until: self.until,
            limit: self.limit,
        }
    }
}

/// The registry.
#[derive(Debug)]
pub struct Registry {
    core: Locked<RegistryCore>,
    /// Woken whenever the commit queue grows.
    ///
    /// The matcher's wake-up, and the reason it can sleep rather than poll. It
    /// is fired **after** the write guard drops, because the matcher's first
    /// act on waking is to take that same lock and drain.
    committed: tokio::sync::Notify,
}

impl Registry {
    /// Wrap a store.
    #[must_use]
    pub fn new(store: RegistryStore) -> Self {
        Self {
            core: Locked::new(RegistryCore::new(store)),
            committed: tokio::sync::Notify::new(),
        }
    }

    // -- mutations -------------------------------------------------------

    /// Register a resource.
    ///
    /// # Errors
    ///
    /// One of the five documented 400 conditions.
    pub fn register(
        &self,
        resource_type: ResourceType,
        body: Body,
    ) -> Result<Applied, RegistrationFailure> {
        let applied = self.core.with_write(|core| {
            let applied = core.store.insert_or_update(resource_type, body)?;
            core.commits.extend(applied.events.iter().cloned());
            Ok(applied)
        });
        // After the guard drops, never under it: the matcher's first act is to
        // take the write lock and drain, so waking it inside the critical
        // section would hand it a lock it cannot have.
        if applied.is_ok() {
            self.announce_commits();
        }
        applied
    }

    /// Delete a resource and everything beneath it.
    ///
    /// `None` when it was not registered, on which the caller answers 404.
    pub fn delete(&self, resource_type: ResourceType, id: &str) -> Option<Vec<ResourceEvent>> {
        let events = self.core.with_write(|core| {
            let events = core.store.delete(resource_type, id)?;
            // In order: children before their parent, so a subscriber replaying
            // the grains never sees a parent go while its children remain.
            core.commits.extend(events.iter().cloned());
            Some(events)
        });
        if events.is_some() {
            self.announce_commits();
        }
        events
    }

    /// Record a heartbeat for a Node.
    ///
    /// **Under the read lock**, which is the whole of divergence D3. Heartbeat
    /// is the highest-rate writer in the system -- `Behaviour -
    /// Registration.md:51` has only Nodes beating, but a beat refreshes the
    /// Node *and every descendant*, so at AMWA scale (2,500 Nodes of six
    /// resources on a 5 s interval) it is roughly 3,500 writes a second to
    /// store a clock value.
    ///
    /// It runs here concurrently with every other reader because `health` is an
    /// `AtomicI64` rather than part of the locked record. Changing this to
    /// `with_write` would compile and would silently undo the divergence.
    ///
    /// No events: health is not part of any resource's representation, so a
    /// heartbeat produces no grain -- which is also why a beat can skip the
    /// commit queue entirely.
    pub fn heartbeat(&self, node_id: &str) -> Option<i64> {
        self.core.with_read(|core| core.store.heartbeat(node_id))
    }

    /// A Node's current health, or `None` if it is not registered.
    ///
    /// What `GET /health/nodes/{id}` answers.
    pub fn node_health(&self, node_id: &str) -> Option<i64> {
        self.core.with_read(|core| core.store.node_health(node_id))
    }

    /// One resource's current health.
    ///
    /// A targeted read, and the distinction from [`Self::health_snapshot`]
    /// matters for more than tidiness: sampling the whole store allocates a
    /// vector of every resource, which is far too slow to observe anything
    /// happening inside a single heartbeat. A test that wants to catch the
    /// order of writes has to read two values, not twenty thousand.
    pub fn resource_health(&self, resource_type: ResourceType, id: &str) -> Option<i64> {
        self.core.with_read(|core| {
            core.store
                .get(resource_type, id)
                .map(nmos_registry_core::resource::RegisteredResource::health)
        })
    }

    /// Every resource's health, copied out.
    ///
    /// A diagnostic read, and the only way to observe the property D3 depends
    /// on: that a heartbeat refreshes children **before** the Node.
    pub fn health_snapshot(&self) -> Vec<(ResourceType, String, i64)> {
        self.core.with_read(|core| {
            ResourceType::ALL
                .into_iter()
                .flat_map(|kind| {
                    core.store
                        .iter_extant(kind)
                        .map(move |resource| (kind, resource.id.clone(), resource.health()))
                        .collect::<Vec<_>>()
                })
                .collect()
        })
    }

    /// Expire silent Nodes and forget elapsed tombstones.
    pub fn collect_garbage(&self) -> Vec<ResourceEvent> {
        let events = self.core.with_write(|core| {
            let events = core.store.collect_garbage();
            core.commits.extend(events.iter().cloned());
            events
        });
        if !events.is_empty() {
            self.announce_commits();
        }
        events
    }

    /// Replace the whole store, for a snapshot install.
    ///
    /// The replacement is built by the caller **outside** the lock -- that is
    /// the point -- and swapped in with one exclusive acquisition. Readers keep
    /// serving the previous view right up to the swap and the next one
    /// afterwards, with no window in which the registry looks empty.
    ///
    /// The commit queue is deliberately **not** cleared. Its entries describe
    /// changes that were already published, and dropping them would strand any
    /// matcher that had not yet drained them.
    pub fn swap_store(&self, store: RegistryStore) -> RegistryStore {
        self.core
            .with_write(|core| std::mem::replace(&mut core.store, store))
    }

    // -- the matcher's side ----------------------------------------------

    /// Take the committed changes awaiting classification.
    ///
    /// One short exclusive acquisition for the whole backlog, after which the
    /// matcher works with no lock held.
    pub fn drain_commits(&self) -> Vec<Committed> {
        self.core.with_write(|core| core.commits.drain())
    }

    /// The position of the most recent commit.
    pub fn latest_sequence(&self) -> Sequence {
        self.core.with_read(|core| core.commits.latest())
    }

    /// The deepest backlog reached since the last reset.
    pub fn commit_high_water(&self) -> usize {
        self.core.with_read(|core| core.commits.high_water())
    }

    /// How many commits are waiting to be routed.
    ///
    /// The overload metric of divergence D2: the queue deliberately does not
    /// coalesce, so sustained growth means the matcher is the bottleneck.
    pub fn pending_commits(&self) -> usize {
        self.core.with_read(|core| core.commits.depth())
    }

    /// Wait until there is something to route.
    ///
    /// The matcher's wake-up. Without it the only way to drive [`route_once`]
    /// would be a polling loop, which on an idle registry is a timer firing
    /// forever to discover nothing has happened.
    ///
    /// [`route_once`]: crate::matcher::route_once
    ///
    /// The future is created before the depth is read, and `notify_one` stores
    /// a permit, so a commit landing in that window is delivered rather than
    /// lost -- a lost wake here is a grain that never arrives.
    pub async fn wait_for_commits(&self) {
        loop {
            let notified = self.committed.notified();
            if self.pending_commits() > 0 {
                return;
            }
            notified.await;
        }
    }

    /// Announce that the commit queue has grown.
    fn announce_commits(&self) {
        self.committed.notify_one();
    }

    // -- reads -----------------------------------------------------------

    /// One resource, copied out.
    pub fn get(&self, resource_type: ResourceType, id: &str) -> Option<ResourceSnapshot> {
        self.core.with_read(|core| {
            core.store
                .get(resource_type, id)
                .map(|resource| ResourceSnapshot {
                    resource_type: resource.resource_type,
                    id: resource.id.clone(),
                    body: resource.body.clone(),
                    created: resource.created,
                    updated: resource.updated,
                })
        })
    }

    /// A page of one collection, filtered and paged, copied out.
    ///
    /// Everything happens under the read lock except the encoding, which is the
    /// whole point: the guard drops with a `Vec` of pointer-cloned bodies in
    /// hand, and the response is built from them afterwards.
    pub fn page(
        &self,
        resource_type: ResourceType,
        request: &PagingRequest,
        filters: &[(String, String)],
    ) -> PageSnapshot {
        self.core.with_read(|core| {
            let ordered: Vec<&nmos_registry_core::resource::RegisteredResource> = core
                .store
                .iter_ordered(resource_type, request.order)
                .collect();
            let collection_max = ordered.last().map(|r| request.cursor_of(r));

            let matched: Vec<&nmos_registry_core::resource::RegisteredResource> = ordered
                .iter()
                .copied()
                .filter(|resource| matches(resource.body.data(), filters))
                .collect();

            let page: Page<'_> = apply_paging(&matched, collection_max, request);
            PageSnapshot {
                resources: page
                    .resources
                    .iter()
                    .map(|resource| ResourceSnapshot {
                        resource_type: resource.resource_type,
                        id: resource.id.clone(),
                        body: resource.body.clone(),
                        created: resource.created,
                        updated: resource.updated,
                    })
                    .collect(),
                since: page.since,
                until: page.until,
                limit: page.limit,
            }
        })
    }

    /// The status-line counters.
    pub fn statistics(&self, subscriptions: usize, grains: usize) -> RegistryStatistics {
        self.core
            .with_read(|core| core.store.statistics(subscriptions, grains))
    }

    /// The periodic status line, in nmos-cpp's exact format.
    ///
    /// `"At <now>, the registry contains <statistics>"`. nmos-cpp emits this
    /// from both its expiry thread and its `POST /resource` handler, and so
    /// does the Python registry -- matching it is what lets the two logs be
    /// read side by side when diagnosing a registration problem.
    ///
    /// Takes the subscription and grain counts rather than reaching for them,
    /// because they live in the `SubscriptionManager` and this crate's one rule
    /// is that the store's lock never reaches outside itself.
    #[must_use]
    pub fn status_line(&self, subscriptions: usize, grains: usize) -> String {
        format!(
            "At {}, the registry contains {}",
            TaiCursor::now(),
            self.statistics(subscriptions, grains).render(),
        )
    }

    /// How many live resources of one type there are.
    pub fn count_extant(&self, resource_type: ResourceType) -> usize {
        self.core
            .with_read(|core| core.store.count_extant(resource_type))
    }

    // -- connecting ------------------------------------------------------

    /// The sync burst for a newly connected subscription, and its anchor.
    ///
    /// # Why this closes the gap rather than holding the lock across it
    ///
    /// `subscriptions.py:345-366` registers the connection and queues the sync
    /// burst in one critical section, so no change can fall in between. It can
    /// afford that because there is no lock to hold.
    ///
    /// Here the burst is built from `Arc`-cloned bodies under a **read** lock,
    /// together with the sequence the store was at. The matcher then delivers
    /// only events **above** that sequence. Same no-gap guarantee: a change
    /// committed before the anchor is in the burst, one after it arrives as a
    /// grain, and there is no instant that is neither.
    ///
    /// It is strictly better than holding the lock, because the filtering and
    /// the encoding happen after the guard drops.
    pub fn connect(&self, subscription: &Subscription) -> (Vec<PendingEvent>, Sequence) {
        self.core.with_read(|core| {
            let anchor = core.commits.latest();
            let burst: Vec<PendingEvent> = core
                .store
                .iter_extant(subscription.resource_type)
                .filter(|resource| {
                    subscription.is_unfiltered()
                        || matches(resource.body.data(), &subscription.params)
                })
                .map(|resource| PendingEvent {
                    path: resource.id.clone(),
                    // `Behaviour - Querying.md:166` -- a sync event carries the
                    // same body on both sides.
                    pre: Some(resource.body.clone()),
                    post: Some(resource.body.clone()),
                })
                .collect();
            (burst, anchor)
        })
    }
}

/// Classify a drained batch for one subscription, coalescing per resource.
///
/// Runs with **no lock held**. Everything it needs is in the events, because
/// they carry owned body snapshots rather than references into the store.
///
/// Coalescing is per resource and in arrival order, so the result is the net
/// change over the batch: the first `pre` and the last `post`.
#[must_use]
pub fn classify_batch(
    subscription: &Subscription,
    batch: &[Committed],
    after: Sequence,
) -> Vec<PendingEvent> {
    let mut order: Vec<String> = Vec::new();
    let mut pending: std::collections::HashMap<String, PendingEvent> =
        std::collections::HashMap::new();

    for committed in batch {
        if committed.sequence <= after {
            // Already covered by this connection's sync burst.
            continue;
        }
        if committed.event.resource_type != subscription.resource_type {
            continue;
        }
        let Some(event) = classify(subscription, &committed.event) else {
            continue;
        };
        match pending.get_mut(&event.path) {
            Some(existing) => existing.merge(event),
            None => {
                order.push(event.path.clone());
                pending.insert(event.path.clone(), event);
            }
        }
    }

    // Emitted in first-appearance order rather than hash order, so two members
    // handed the same batch publish the same grain -- the same reason the
    // store sorts a cascade.
    order
        .into_iter()
        .filter_map(|path| pending.remove(&path))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use nmos_registry_core::store::RegistryStore;

    /// Captured from the Python registry, which reproduces nmos-cpp's
    ///
    /// ```text
    /// "At " << make_version(tai_now()) << ", the registry contains "
    ///       << put_resources_statistics(resources)
    /// ```
    ///
    /// The two registries' logs are meant to be readable side by side when
    /// diagnosing a registration problem, so the wording, the order of the
    /// eight counters and the separators are contract rather than taste.
    const EMPTY_BODY: &str = "0 resources (0 nodes, 0 devices, 0 sources, \
                              0 flows, 0 senders, 0 receivers, \
                              0 subscriptions, 0 grains), \
                              most recent update: 0:0, least health: ";

    #[test]
    fn the_status_line_matches_nmos_cpps_format() {
        let registry = Registry::new(RegistryStore::new());
        let line = registry.status_line(0, 0);

        assert!(line.starts_with("At "), "{line}");
        assert!(line.contains(", the registry contains "), "{line}");
        assert!(line.ends_with(" non-extant resources"), "{line}");
        assert!(line.contains(EMPTY_BODY), "{line}");
    }

    #[test]
    fn the_status_line_counts_subscriptions_and_grains_in_the_total() {
        // nmos-cpp's `by_type.count(true)` includes them, so they appear both
        // in the per-kind list and in the leading total. Counting resources
        // only would make the total disagree with the list beside it.
        let registry = Registry::new(RegistryStore::new());
        let line = registry.status_line(1, 2);

        assert!(line.contains("1 subscriptions"), "{line}");
        assert!(line.contains("2 grains"), "{line}");
        assert!(
            line.contains("3 resources ("),
            "the total omitted them: {line}",
        );
    }

    #[test]
    fn the_eight_counters_keep_nmos_cpps_order() {
        // Registration dependency order for the six, then subscriptions, then
        // grains. A different order is a different log line.
        let registry = Registry::new(RegistryStore::new());
        let line = registry.status_line(0, 0);
        let mut at = 0;
        for label in [
            "nodes",
            "devices",
            "sources",
            "flows",
            "senders",
            "receivers",
            "subscriptions",
            "grains",
        ] {
            let found = line[at..]
                .find(label)
                .unwrap_or_else(|| panic!("{label} missing or out of order: {line}"));
            at += found + label.len();
        }
    }
}
