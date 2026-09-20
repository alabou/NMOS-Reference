// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The namespace watch: the only thing that changes a registry's local store.
//!
//! Every registry materialises its view from this stream and from nothing else
//! -- including for its own writes. A mutation commits through a transaction
//! and is then applied *here*, when the watch delivers it. That is what makes
//! locally originated writes need no special casing and no duplicate
//! suppression, which is the failure the legacy dRDS spent an origin-index byte
//! on and still got wrong for deletes (an etcd DELETE event carries no value,
//! so its origin byte was always read as zero).
//!
//! # Three etcd behaviours this module depends on
//!
//! **Revisions are never split.** etcd's API guarantees say "a list of events
//! is guaranteed to encompass complete revisions. Updates in the same revision
//! over multiple keys will not be split over several lists of events." So
//! grouping a response's events by `mod_revision` yields *complete* groups, and
//! the consumer can apply a whole revision -- a Node delete and all of its
//! descendants -- as one uninterrupted step. One response may carry several
//! revisions; none carries part of one.
//!
//! **Progress replies prove delivery, and are withheld in two cases.** etcd's
//! `progressIfSync` declines to answer a `WatchProgressRequest` when the
//! watcher is lagging, *and when the store revision has not yet reached the
//! watch's start revision* -- that is, when nothing has happened since the
//! watch was created.
//!
//! That second case is load-bearing and is easy to design a deadlock into.
//! After a preload at revision R the watch opens at `R + 1`; if no write has
//! happened since, a progress request is silently ignored, and a fence waiting
//! on R would block until its deadline. The resolution is initialisation, not a
//! retry: `last_applied_revision` must be seeded to `start_revision - 1` (the
//! preload revision), so any fence target at or below R is already satisfied
//! and no progress reply is needed.
//!
//! **Compaction is a distinct, non-resumable failure.** It arrives as a
//! cancelled watch carrying `compact_revision`. It gets its own error variant
//! so the resume loop cannot mistake it for a dropped connection and silently
//! skip a range of revisions -- the one bug that would let two registries
//! disagree about state forever.

use tokio::sync::mpsc;
use tonic::Streaming;

use crate::channel::{Endpoint, SharedPool, StreamMethod};
use crate::errors::{EtcdError, Result, classify};
use crate::generated::etcdserverpb as pb;
use crate::generated::mvccpb::Event;
use crate::kv::prefix_range_end;

const WATCH: StreamMethod = StreamMethod::new("/etcdserverpb.Watch/Watch");

/// Every event belonging to one store revision, or a progress marker.
///
/// Events are the raw `mvccpb::Event` messages rather than copies: they are
/// fully typed already, every `bytes` field shares the decoded buffer, and a
/// subtree delete can carry hundreds of events each holding a complete resource
/// body in `prev_kv`. Copying those into project types would double the
/// allocation on the one path that is already the largest.
#[derive(Debug, Clone, PartialEq)]
pub struct RevisionBatch {
    /// The store revision these events belong to.
    pub revision: i64,
    /// The events, complete for that revision.
    pub events: Vec<Event>,
}

impl RevisionBatch {
    /// True for a progress notification: no events, revision meaningful.
    ///
    /// The consumer may advance its fence to `revision` on one of these -- that
    /// is their whole purpose -- but only after everything already received has
    /// been applied.
    #[must_use]
    pub fn progress_only(&self) -> bool {
        self.events.is_empty()
    }
}

/// One watch connection, positioned at a revision.
///
/// Deliberately a *single* connection rather than a self-healing one. The
/// caller owns `last_applied_revision`, so only the caller knows where a
/// replacement stream must resume from; a stream that silently reconnected
/// itself would resume from wherever it happened to be and could skip
/// revisions. Failure is reported, and the backend re-opens at
/// `last_applied_revision + 1`.
pub struct WatchStream {
    requests: mpsc::Sender<pb::WatchRequest>,
    responses: Streaming<pb::WatchResponse>,
    watch_id: i64,
    /// Fragments of a revision etcd split across responses, awaiting the piece
    /// that completes it.
    pending: Vec<Event>,
    /// Batches already decoded from one response but not yet handed out.
    ///
    /// One response can carry several complete revisions; the consumer takes
    /// one batch at a time.
    ready: std::collections::VecDeque<RevisionBatch>,
    closed: bool,
}

impl std::fmt::Debug for WatchStream {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("WatchStream")
            .field("watch_id", &self.watch_id)
            .field("pending_fragments", &self.pending.len())
            .field("ready_batches", &self.ready.len())
            .field("closed", &self.closed)
            .finish()
    }
}

impl WatchStream {
    /// etcd's id for this watch, once it has confirmed creation.
    #[must_use]
    pub const fn watch_id(&self) -> i64 {
        self.watch_id
    }

    /// Ask etcd to emit a progress notification on this stream.
    ///
    /// Answered only when the watcher is synced, which is precisely what makes
    /// the reply meaningful: receiving progress at revision R proves everything
    /// through R has been delivered. A lagging watcher gets no reply at all, so
    /// a caller must never block on this alone.
    ///
    /// # Errors
    ///
    /// `EtcdError::Unavailable` when the stream is closed.
    pub async fn request_progress(&self) -> Result<()> {
        if self.closed {
            return Err(EtcdError::Unavailable("watch stream is closed".to_owned()));
        }
        self.requests
            .send(pb::WatchRequest {
                request_union: Some(pb::watch_request::RequestUnion::ProgressRequest(
                    pb::WatchProgressRequest {},
                )),
            })
            .await
            .map_err(|_| EtcdError::Unavailable("watch stream is closed".to_owned()))
    }

    /// The next batch: one revision's complete events, or a progress marker.
    ///
    /// `Ok(None)` when the stream ended cleanly.
    ///
    /// # Errors
    ///
    /// `EtcdError::Compacted` when the history this watch needs is gone --
    /// which the caller must treat as "resnapshot", never as "reconnect".
    /// `EtcdError::Unavailable` for every other failure.
    pub async fn next_batch(&mut self) -> Result<Option<RevisionBatch>> {
        loop {
            if let Some(batch) = self.ready.pop_front() {
                return Ok(Some(batch));
            }

            let response = match self.responses.message().await {
                Ok(Some(response)) => response,
                Ok(None) => {
                    // A stream that ends mid-fragment delivered part of a
                    // revision, which is the one thing the completeness
                    // guarantee otherwise rules out. Saying so is better than
                    // returning a short batch the consumer would apply.
                    if self.pending.is_empty() {
                        return Ok(None);
                    }
                    return Err(EtcdError::Unavailable(
                        "watch stream ended mid-fragment; a revision was \
                         delivered incompletely"
                            .to_owned(),
                    ));
                }
                Err(status) => return Err(classify(&status)),
            };

            raise_if_cancelled(&response)?;
            if response.created {
                continue;
            }

            // Fragmented responses are reassembled here: etcd sets
            // `fragment` on every piece but the last, and all pieces share one
            // revision. Yielding a fragment on its own would hand the consumer
            // a partial revision.
            self.pending.extend(response.events);
            if response.fragment {
                continue;
            }

            let events = std::mem::take(&mut self.pending);
            if events.is_empty() {
                // Progress notification: no events, but the revision is
                // authoritative and lets a fence advance during quiet periods.
                return Ok(Some(RevisionBatch {
                    revision: response.header.map_or(0, |header| header.revision),
                    events: Vec::new(),
                }));
            }
            self.ready.extend(group_by_revision(events));
        }
    }

    /// Cancel the stream. Safe to call more than once.
    pub fn close(&mut self) {
        self.closed = true;
        // Dropping the request half ends the call; the response half is
        // dropped with `self`.
        self.ready.clear();
    }
}

/// Turn a cancellation into the right error.
///
/// Compaction is separated from every other cancellation because it is the
/// only one that cannot be fixed by reconnecting at the same revision -- the
/// history is gone, and the only recovery is a fresh snapshot.
fn raise_if_cancelled(response: &pb::WatchResponse) -> Result<()> {
    if !response.canceled {
        return Ok(());
    }
    if response.compact_revision > 0 {
        return Err(EtcdError::Compacted {
            message: format!(
                "watch cancelled: history compacted to revision {}",
                response.compact_revision,
            ),
            compact_revision: response.compact_revision,
        });
    }
    let reason = if response.cancel_reason.is_empty() {
        "no reason given"
    } else {
        &response.cancel_reason
    };
    Err(EtcdError::Unavailable(
        format!("watch cancelled: {reason}",),
    ))
}

/// Split a response's events into one batch per revision, in order.
///
/// Safe because etcd never splits a revision across responses, so every group
/// formed here is complete. Events arrive ordered by revision, so a single pass
/// suffices and the grouping preserves etcd's ordering -- which for a
/// transaction is the order of its operations, the property the registry relies
/// on to apply parents before children.
fn group_by_revision(events: Vec<Event>) -> Vec<RevisionBatch> {
    let mut batches: Vec<RevisionBatch> = Vec::new();
    let mut current: Vec<Event> = Vec::new();
    let mut revision = 0_i64;

    for event in events {
        // Both PUT and DELETE record the revision that produced them in
        // `kv.mod_revision`; for a delete the kv carries no value and the old
        // content is in `prev_kv`.
        let event_revision = event.kv.as_ref().map_or(0, |kv| kv.mod_revision);
        if !current.is_empty() && event_revision != revision {
            batches.push(RevisionBatch {
                revision,
                events: std::mem::take(&mut current),
            });
        }
        revision = event_revision;
        current.push(event);
    }
    if !current.is_empty() {
        batches.push(RevisionBatch {
            revision,
            events: current,
        });
    }
    batches
}

/// Opens watch streams against a chosen member.
#[derive(Debug, Clone)]
pub struct EtcdWatch {
    pool: SharedPool,
}

impl EtcdWatch {
    /// Wrap a pool.
    #[must_use]
    pub const fn new(pool: SharedPool) -> Self {
        Self { pool }
    }

    /// Create a watch over `prefix` starting at `start_revision`.
    ///
    /// The create request is **confirmed** before this returns, so a watch that
    /// cannot be established -- most importantly one whose start revision has
    /// already been compacted -- fails here, before the backend believes it has
    /// a live stream and starts trusting an empty event flow as "nothing has
    /// changed".
    ///
    /// `endpoint` defaults to the first member -- the local one when configured
    /// -- because a watch is a long-lived stream and keeping it on the
    /// co-located member avoids a network hop for every change in the cluster.
    ///
    /// `prev_kv` asks etcd to attach each key's previous value to its event.
    /// **Off by default, and the registry leaves it off.** It looks as though
    /// removal grains need it -- a DELETE event carries no value, and
    /// `Behaviour - Querying.md` requires the removal event to carry the
    /// resource's final content. They do not: the registry builds that grain
    /// from its OWN copy, which it must have, because a resource it never
    /// materialised has nothing to remove and emits no grain either way.
    /// Requesting it anyway makes etcd fetch and transmit the previous value of
    /// every key on every event, for a field no production code path reads.
    ///
    /// # Errors
    ///
    /// `EtcdError::Compacted` when `start_revision` is already gone;
    /// `EtcdError::Unavailable` when the member cannot be reached or does not
    /// confirm the watch.
    pub async fn open(
        &self,
        prefix: &[u8],
        start_revision: i64,
        endpoint: Option<&Endpoint>,
        prev_kv: bool,
    ) -> Result<WatchStream> {
        let default;
        let target = match endpoint {
            Some(chosen) => chosen,
            None => {
                default = self
                    .pool
                    .endpoints()
                    .first()
                    .ok_or_else(|| {
                        EtcdError::Other("the channel pool has no endpoints".to_owned())
                    })?
                    .clone();
                &default
            }
        };

        let create = pb::WatchCreateRequest {
            key: bytes::Bytes::copy_from_slice(prefix),
            range_end: bytes::Bytes::from(prefix_range_end(prefix)),
            start_revision,
            prev_kv,
            // etcd splits oversized responses instead of failing them. A Node
            // delete cascades to every descendant in ONE revision, so the
            // response can be large; without this it would arrive as
            // RESOURCE_EXHAUSTED.
            fragment: true,
            // Periodic progress even when idle, so a fence waiter is not the
            // only thing that can advance the applied revision and a dead
            // connection is distinguishable from a quiet one.
            progress_notify: true,
            ..Default::default()
        };
        // The create request opens the stream rather than following it: etcd
        // sends no response headers until its handler writes, and the Watch
        // handler writes nothing until it has a create request. Opening first
        // and sending second deadlocks -- see `open_stream`.
        let (requests, mut responses) = self
            .pool
            .open_stream::<pb::WatchRequest, pb::WatchResponse>(
                WATCH,
                target,
                pb::WatchRequest {
                    request_union: Some(pb::watch_request::RequestUnion::CreateRequest(create)),
                },
            )
            .await?;

        let confirmation = responses
            .message()
            .await
            .map_err(|status| classify(&status))?
            .ok_or_else(|| {
                EtcdError::Unavailable("watch stream closed before confirmation".to_owned())
            })?;
        raise_if_cancelled(&confirmation)?;
        if !confirmation.created {
            return Err(EtcdError::Unavailable(
                "watch stream did not confirm creation".to_owned(),
            ));
        }

        Ok(WatchStream {
            requests,
            responses,
            watch_id: confirmation.watch_id,
            pending: Vec::new(),
            ready: std::collections::VecDeque::new(),
            closed: false,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::generated::mvccpb::KeyValue;

    fn event(revision: i64, key: &'static str) -> Event {
        Event {
            r#type: 0,
            kv: Some(KeyValue {
                key: bytes::Bytes::from_static(key.as_bytes()),
                mod_revision: revision,
                ..Default::default()
            }),
            prev_kv: None,
        }
    }

    #[test]
    fn one_response_can_carry_several_complete_revisions() {
        // etcd never splits a revision across responses, so every group formed
        // here is complete -- but one response may hold more than one.
        let batches = group_by_revision(vec![
            event(5, "a"),
            event(5, "b"),
            event(6, "c"),
            event(7, "d"),
            event(7, "e"),
        ]);
        assert_eq!(batches.len(), 3);
        assert_eq!(batches[0].revision, 5);
        assert_eq!(batches[0].events.len(), 2);
        assert_eq!(batches[1].revision, 6);
        assert_eq!(batches[2].revision, 7);
        assert_eq!(batches[2].events.len(), 2);
    }

    #[test]
    fn grouping_preserves_the_order_within_a_revision() {
        // For a transaction that is the order of its operations, which is what
        // lets the registry apply parents before children.
        let batches = group_by_revision(vec![
            event(9, "node"),
            event(9, "device"),
            event(9, "sender"),
        ]);
        assert_eq!(batches.len(), 1);
        let keys: Vec<&[u8]> = batches[0]
            .events
            .iter()
            .map(|e| e.kv.as_ref().unwrap().key.as_ref())
            .collect();
        assert_eq!(keys, [b"node".as_slice(), b"device", b"sender"]);
    }

    #[test]
    fn a_single_revision_is_one_batch() {
        let batches = group_by_revision(vec![event(3, "a")]);
        assert_eq!(batches.len(), 1);
        assert_eq!(batches[0].revision, 3);
    }

    #[test]
    fn no_events_is_no_batches() {
        assert!(group_by_revision(Vec::new()).is_empty());
    }

    #[test]
    fn a_compaction_is_its_own_error_and_carries_the_revision() {
        // The distinction the whole module exists to preserve: a resume loop
        // that treated this as a reconnect would silently skip every revision
        // that was compacted away.
        let response = pb::WatchResponse {
            canceled: true,
            compact_revision: 4000,
            ..Default::default()
        };
        let error = raise_if_cancelled(&response).unwrap_err();
        match error {
            EtcdError::Compacted {
                compact_revision, ..
            } => assert_eq!(compact_revision, 4000),
            other => panic!("expected a compaction, got {other:?}"),
        }
    }

    #[test]
    fn a_plain_cancellation_is_retryable_and_says_why() {
        let response = pb::WatchResponse {
            canceled: true,
            cancel_reason: "etcdserver: permission denied".to_owned(),
            ..Default::default()
        };
        let error = raise_if_cancelled(&response).unwrap_err();
        assert!(error.is_retryable());
        assert_eq!(
            error.message(),
            "watch cancelled: etcdserver: permission denied",
        );
    }

    #[test]
    fn a_cancellation_with_no_reason_still_says_something() {
        let response = pb::WatchResponse {
            canceled: true,
            ..Default::default()
        };
        assert_eq!(
            raise_if_cancelled(&response).unwrap_err().message(),
            "watch cancelled: no reason given",
        );
    }

    #[test]
    fn an_uncancelled_response_passes_through() {
        assert!(raise_if_cancelled(&pb::WatchResponse::default()).is_ok());
    }

    #[test]
    fn a_progress_marker_carries_a_revision_and_no_events() {
        let batch = RevisionBatch {
            revision: 4242,
            events: Vec::new(),
        };
        assert!(batch.progress_only());
        assert!(
            !RevisionBatch {
                revision: 4242,
                events: vec![event(4242, "a")],
            }
            .progress_only()
        );
    }
}
