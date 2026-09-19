// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! One quorum round per burst, and no proposal answered into the void.
//!
//! Port of `nmos/raft/tests/test_batcher.py`, adapted to a channel rather than
//! `call_soon` -- see the module docs for why that substitution is the one
//! place this crate could not port the mechanism as-is.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use nmos_registry_raft::batcher::{MAX_BATCH, proposal_channel};

#[tokio::test]
async fn proposals_submitted_together_land_in_one_batch() {
    // The throughput claim. Ten proposals queued before the drain runs are one
    // quorum round, not ten.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(MAX_BATCH);
    let mut waiters = Vec::new();
    for value in 0..10 {
        waiters.push(batcher.submit(value).expect("accepted"));
    }

    let batch = drain.next_batch().await.expect("a batch");
    assert_eq!(
        batch.len(),
        10,
        "the burst was split across rounds, so the cluster pays ten quorum \
         round trips where it should pay one",
    );

    for pending in batch {
        pending.reply.send(true).expect("the waiter is still there");
    }
    for waiter in waiters {
        assert_eq!(waiter.await, Ok(true));
    }
}

#[tokio::test]
async fn the_batch_closes_without_waiting_for_more() {
    // No timer, so a single proposal on an idle registry is not delayed by a
    // batching window that exists to help a load this registry is not under.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(MAX_BATCH);
    let waiter = batcher.submit(1).expect("accepted");

    let batch = drain.next_batch().await.expect("a batch");
    assert_eq!(batch.len(), 1);
    batch.into_iter().next().expect("one").reply.send(true).ok();
    assert_eq!(waiter.await, Ok(true));
}

#[tokio::test]
async fn a_batch_is_capped() {
    // So a single AppendEntries cannot grow past the frame cap. The excess
    // simply forms the next batch.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(4);
    for value in 0..10 {
        batcher.submit(value).expect("accepted");
    }

    let first = drain.next_batch().await.expect("a batch");
    assert_eq!(first.len(), 4);
    let second = drain.next_batch().await.expect("a batch");
    assert_eq!(second.len(), 4);
    let third = drain.next_batch().await.expect("a batch");
    assert_eq!(third.len(), 2);
}

#[tokio::test]
async fn the_batch_preserves_submission_order() {
    // A batcher that reordered would break the per-Node serialisation the
    // ownership design relies on: two updates to one resource must reach the
    // log in the order the client sent them.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(MAX_BATCH);
    for value in 0..50 {
        batcher.submit(value).expect("accepted");
    }

    let batch = drain.next_batch().await.expect("a batch");
    let order: Vec<u32> = batch.iter().map(|p| p.operation).collect();
    assert_eq!(order, (0..50).collect::<Vec<u32>>());
}

#[tokio::test]
async fn the_receiver_exists_before_anything_can_await() {
    // The window this is synchronous to avoid: if `submit` awaited, the entry
    // could commit and apply between "the operation was accepted" and "the
    // waiter exists", and the outcome would be delivered to nobody while the
    // caller waited forever for something that had already happened.
    //
    // Expressed here as: the outcome can be delivered the instant the batch is
    // taken, with the caller not yet awaiting, and it is still received.
    let (batcher, mut drain) = proposal_channel::<u32, &'static str>(MAX_BATCH);
    let waiter = batcher.submit(1).expect("accepted");

    let batch = drain.next_batch().await.expect("a batch");
    batch
        .into_iter()
        .next()
        .expect("one")
        .reply
        .send("committed")
        .expect("the waiter was registered before the drain could run");

    assert_eq!(waiter.await, Ok("committed"));
}

#[tokio::test]
async fn pending_reports_the_queue_depth() {
    // The overload metric: sustained growth means the cluster cannot commit as
    // fast as it is being asked to.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(MAX_BATCH);
    assert_eq!(batcher.pending(), 0);
    for value in 0..7 {
        batcher.submit(value).expect("accepted");
    }
    assert_eq!(batcher.pending(), 7);

    let batch = drain.next_batch().await.expect("a batch");
    assert_eq!(batch.len(), 7);
    assert_eq!(batcher.pending(), 0);
}

#[tokio::test]
async fn draining_now_takes_everything_without_waiting() {
    // Shutdown, and losing leadership. The caller fails each waiter rather than
    // proposing it.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(MAX_BATCH);
    for value in 0..5 {
        batcher.submit(value).expect("accepted");
    }

    let batch = drain.drain_now();
    assert_eq!(batch.len(), 5);
    assert_eq!(batcher.pending(), 0);
    assert!(drain.drain_now().is_empty());
}

#[tokio::test]
async fn the_batcher_is_usable_after_a_failure() {
    // A member that loses leadership and regains it keeps the same batcher.
    // One that refused to accept anything after a single failure would stop
    // serving until it restarted.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(MAX_BATCH);
    let orphaned = batcher.submit(1).expect("accepted");
    drop(drain.drain_now()); // every waiter dropped, as a failure would

    assert!(orphaned.await.is_err(), "a dropped reply is an error");

    let waiter = batcher.submit(2).expect("still accepting");
    let batch = drain.next_batch().await.expect("a batch");
    assert_eq!(batch[0].operation, 2);
    batch.into_iter().next().expect("one").reply.send(true).ok();
    assert_eq!(waiter.await, Ok(true));
}

#[tokio::test]
async fn submitting_after_the_drain_is_gone_is_refused() {
    // Not silently dropped: a caller whose proposal went nowhere must be told,
    // or it waits forever on a round that will never happen.
    let (batcher, drain) = proposal_channel::<u32, bool>(MAX_BATCH);
    drop(drain);
    assert!(batcher.submit(1).is_err());
}

#[tokio::test]
async fn every_handler_holds_its_own_clone() {
    // The shape the HTTP layer needs: one batcher per handler, one drain.
    let (batcher, mut drain) = proposal_channel::<u32, bool>(MAX_BATCH);

    // Each task submits *and awaits its own outcome*. Returning the receiver
    // from the task instead would make the `JoinHandle` yield something that is
    // itself awaitable -- which compiles, and which the next reader has to
    // double-await correctly. Clippy's `async_yields_async` says so, and it is
    // right: the shape that cannot be got wrong is the one where the waiter
    // never leaves the task that created it.
    let handles: Vec<_> = (0..4u32)
        .map(|value| {
            let mine = batcher.clone();
            tokio::spawn(async move {
                let waiter = mine.submit(value).expect("accepted");
                waiter.await.expect("answered")
            })
        })
        .collect();

    let mut taken = 0;
    while taken < 4 {
        let batch = drain.next_batch().await.expect("a batch");
        taken += batch.len();
        for pending in batch {
            pending.reply.send(true).ok();
        }
    }

    for handle in handles {
        assert!(handle.await.expect("the task finished"));
    }
}
