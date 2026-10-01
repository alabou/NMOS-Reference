// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Two members over real sockets, and the gates that stop the wrong ones.
//!
//! Port of the socket-level half of `nmos/raft/tests/`. Plaintext, which the
//! configuration layer permits only on the loopback -- the TLS half needs the
//! certificate fixtures and belongs with the binary's tests, where they live.
//!
//! The handshake decisions are also tested directly, without a socket. They
//! are the difference between "these two members belong to the same cluster"
//! and "any device holding a Product-CA certificate may propose log entries",
//! and each branch has a message an operator has to be able to act on.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};

use async_trait::async_trait;
use nmos_registry_raft::messages::{
    AppendEntries, AppendEntriesReply, Forward, ForwardReply, Hello, HelloAck, InstallSnapshot,
    InstallSnapshotReply, Message, Pong, Promote, Propose, ProposeReply, ReadIndex, ReadIndexReply,
    RequestVote, RequestVoteReply, decode_message,
};
use nmos_registry_raft::persist::PersistentStateError;
use nmos_registry_raft::transport::{
    CONN_READ_TIMEOUT_MS, PeerHandler, RaftTransport, Transport, TransportSettings, frame_for,
    read_frame, refuse_certificate, refuse_hello,
};
use nmos_registry_raft::wire::{
    FLAG_REPLY, Frame, MessageType, PROTOCOL_MAJOR, PROTOCOL_MINOR, Stream, encode_frame,
};
use tokio::io::AsyncWriteExt as _;
use tokio::sync::Mutex;

/// Records what arrived, and answers the way a member would.
#[derive(Default)]
struct Recorder {
    votes: AtomicU64,
    appends: AtomicU64,
    promotes: AtomicU64,
    peer_up: AtomicU64,
    peer_down: AtomicU64,
    last_incarnation: AtomicU64,
    append_replies: AtomicU64,
    /// Answers every append, and every append reply, as a member whose term
    /// file can no longer be written: with the save's failure.
    saves_fail: AtomicBool,
    /// Holds `on_forward` open until the test releases it.
    ///
    /// `None` by default, so every other test sees an immediate answer. Only
    /// usable at all because the forward handler is served off the link reader
    /// -- held open on the reader, as it was until today, this would stall the
    /// link and the colliding append would never arrive.
    forward_gate: Mutex<Option<tokio::sync::oneshot::Receiver<()>>>,
    seen: Mutex<Vec<String>>,
    /// The most any snapshot-chunk answer sent to this member said was
    /// received.
    chunk_answered: AtomicU64,
}

impl Recorder {
    /// What a member's save of its term would do: fail, once told to.
    fn save(&self) -> Result<(), PersistentStateError> {
        if self.saves_fail.load(Ordering::SeqCst) {
            return Err(PersistentStateError(
                "could not write raft-state.json: No space left on device".to_owned(),
            ));
        }
        Ok(())
    }
}

#[async_trait]
impl PeerHandler for Recorder {
    fn on_request_vote(
        &self,
        _peer: u64,
        message: &RequestVote,
    ) -> Result<RequestVoteReply, PersistentStateError> {
        self.votes.fetch_add(1, Ordering::SeqCst);
        Ok(RequestVoteReply {
            term: message.term,
            granted: true,
            voting: true,
            pre_vote: message.pre_vote,
        })
    }

    fn on_append_entries(
        &self,
        _peer: u64,
        message: &AppendEntries,
    ) -> Result<AppendEntriesReply, PersistentStateError> {
        self.appends.fetch_add(1, Ordering::SeqCst);
        self.save()?;
        Ok(AppendEntriesReply {
            term: message.term,
            success: true,
            match_index: message.prev_log_index + message.entries.len() as u64,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: message.request_id,
        })
    }

    fn on_install_snapshot(
        &self,
        _peer: u64,
        message: &InstallSnapshot,
    ) -> Result<InstallSnapshotReply, PersistentStateError> {
        Ok(InstallSnapshotReply {
            term: message.term,
            bytes_received: message.data.len() as u64,
            done: message.done,
            commit_index: 0,
            request_id: message.request_id,
        })
    }

    fn on_promote(&self, _peer: u64, _message: &Promote) {
        self.promotes.fetch_add(1, Ordering::SeqCst);
    }

    fn on_request_vote_reply(
        &self,
        _peer: u64,
        _message: &RequestVoteReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    fn on_append_entries_reply(
        &self,
        _peer: u64,
        _message: &AppendEntriesReply,
    ) -> Result<(), PersistentStateError> {
        self.append_replies.fetch_add(1, Ordering::SeqCst);
        self.save()?;
        Ok(())
    }

    fn on_install_snapshot_reply(
        &self,
        _peer: u64,
        message: &InstallSnapshotReply,
    ) -> Result<(), PersistentStateError> {
        self.chunk_answered
            .fetch_max(message.bytes_received, Ordering::SeqCst);
        Ok(())
    }

    async fn on_propose(&self, _peer: u64, message: &Propose) -> ProposeReply {
        self.seen
            .lock()
            .await
            .push(format!("propose x{}", message.proposals.len()));
        ProposeReply {
            accepted: true,
            reason: String::new(),
            term: 1,
            first_index: 1,
            request_id: message.request_id,
            leader: Some(0),
        }
    }

    async fn on_forward(&self, _peer: u64, message: &Forward) -> ForwardReply {
        // Taken in its own scope so the lock is released before the wait.
        let gate = { self.forward_gate.lock().await.take() };
        if let Some(gate) = gate {
            drop(gate.await);
        }
        self.seen
            .lock()
            .await
            .push(format!("forward {}", message.resource_id));
        ForwardReply {
            ok: true,
            created: true,
            error: String::new(),
            detail: String::new(),
            applied_index: 7,
            not_owner: false,
            request_id: message.request_id,
            owner: Some(1),
        }
    }

    async fn on_read_index(&self, _peer: u64, message: &ReadIndex) -> ReadIndexReply {
        self.seen.lock().await.push("read index".to_owned());
        ReadIndexReply {
            ok: true,
            index: 7,
            reason: String::new(),
            request_id: message.request_id,
        }
    }

    fn on_peer_state(&self, _peer: u64, up: bool, incarnation: u64) {
        if up {
            self.peer_up.fetch_add(1, Ordering::SeqCst);
            self.last_incarnation.store(incarnation, Ordering::SeqCst);
        } else {
            self.peer_down.fetch_add(1, Ordering::SeqCst);
        }
    }
}

fn loopback(port: u16) -> std::net::SocketAddr {
    std::net::SocketAddr::from(([127, 0, 0, 1], port))
}

/// Wait for a condition, or give up. Polling rather than sleeping a fixed
/// time: a fixed sleep is either flaky on a loaded machine or slow on an idle
/// one, and this suite runs on both.
async fn until(mut ready: impl FnMut() -> bool) -> bool {
    for _ in 0..600 {
        if ready() {
            return true;
        }
        tokio::time::sleep(std::time::Duration::from_millis(10)).await;
    }
    false
}

/// Two members that know about each other, already started.
async fn pair(
    cluster_id: &str,
) -> (
    Arc<RaftTransport>,
    Arc<Recorder>,
    Arc<RaftTransport>,
    Arc<Recorder>,
) {
    // Bind first with no peers to learn the ports, then rebuild with them:
    // each member has to be told where the other listens, and port 0 is what
    // keeps this suite from colliding with anything else on the machine.
    let probe_a = std::net::TcpListener::bind(loopback(0)).expect("a port");
    let probe_b = std::net::TcpListener::bind(loopback(0)).expect("a port");
    let addr_a = probe_a.local_addr().expect("an address");
    let addr_b = probe_b.local_addr().expect("an address");
    drop(probe_a);
    drop(probe_b);

    let make = |local: u64, bind, peer_index: u64, peer_addr: std::net::SocketAddr| {
        let mut peers = HashMap::new();
        peers.insert(peer_index, ("127.0.0.1".to_owned(), peer_addr.port()));
        Arc::new(RaftTransport::new(TransportSettings {
            local,
            peers,
            bind,
            cluster_id: cluster_id.to_owned(),
            member_name: format!("member-{local}"),
            incarnation: local + 10,
            tls: None,
            rpc_timeout_ms: 2_000,
            conn_read_timeout_ms: CONN_READ_TIMEOUT_MS,
        }))
    };

    let a = make(0, addr_a, 1, addr_b);
    let b = make(1, addr_b, 0, addr_a);
    let recorder_a = Arc::new(Recorder::default());
    let recorder_b = Arc::new(Recorder::default());

    a.start(Arc::clone(&recorder_a) as Arc<dyn PeerHandler>)
        .await
        .expect("a listens");
    b.start(Arc::clone(&recorder_b) as Arc<dyn PeerHandler>)
        .await
        .expect("b listens");

    assert!(
        until(|| !a.live().is_empty() && !b.live().is_empty()).await,
        "the two members never linked up",
    );
    (a, recorder_a, b, recorder_b)
}

// -- the handshake decisions, without a socket ------------------------------

#[test]
fn a_matching_hello_is_accepted() {
    let hello = Hello {
        major: u64::from(PROTOCOL_MAJOR),
        minor: 0,
        cluster_id: "nmos-registry-abc".to_owned(),
        member_name: "member-1".to_owned(),
        member_index: 1,
        incarnation: 3,
        stream: Stream::Control,
    };
    assert_eq!(refuse_hello(&hello, 0, "nmos-registry-abc", &[1, 2]), None);
}

#[test]
fn a_different_cluster_is_refused() {
    // Precisely the split the cluster token exists to detect.
    let hello = Hello {
        major: u64::from(PROTOCOL_MAJOR),
        minor: 0,
        cluster_id: "nmos-registry-other".to_owned(),
        member_name: "member-1".to_owned(),
        member_index: 1,
        incarnation: 3,
        stream: Stream::Control,
    };
    let refusal = refuse_hello(&hello, 0, "nmos-registry-abc", &[1]).expect("refused");
    assert!(refusal.contains("nmos-registry-other"), "{refusal}");
    assert!(refusal.contains("nmos-registry-abc"), "{refusal}");
}

#[test]
fn a_different_protocol_major_is_refused_and_a_minor_is_not() {
    let base = Hello {
        major: u64::from(PROTOCOL_MAJOR),
        minor: 0,
        cluster_id: "c".to_owned(),
        member_name: "member-1".to_owned(),
        member_index: 1,
        incarnation: 3,
        stream: Stream::Control,
    };

    let newer_minor = Hello {
        minor: 99,
        ..base.clone()
    };
    assert_eq!(
        refuse_hello(&newer_minor, 0, "c", &[1]),
        None,
        "a higher minor is skippable by design and must not close the link",
    );

    let newer_major = Hello {
        major: u64::from(PROTOCOL_MAJOR) + 1,
        ..base
    };
    let refusal = refuse_hello(&newer_major, 0, "c", &[1]).expect("refused");
    assert!(refusal.contains("protocol major"), "{refusal}");
}

#[test]
fn a_hello_claiming_our_own_index_is_refused() {
    // Two members that both believe they are member 0 would each count the
    // other's vote as their own.
    let hello = Hello {
        major: u64::from(PROTOCOL_MAJOR),
        minor: 0,
        cluster_id: "c".to_owned(),
        member_name: "impostor".to_owned(),
        member_index: 0,
        incarnation: 1,
        stream: Stream::Control,
    };
    assert_eq!(
        refuse_hello(&hello, 0, "c", &[1, 2]).as_deref(),
        Some("that is this member's own index"),
    );
}

#[test]
fn a_hello_from_outside_the_member_set_is_refused() {
    let hello = Hello {
        major: u64::from(PROTOCOL_MAJOR),
        minor: 0,
        cluster_id: "c".to_owned(),
        member_name: "stranger".to_owned(),
        member_index: 7,
        incarnation: 1,
        stream: Stream::Control,
    };
    let refusal = refuse_hello(&hello, 0, "c", &[1, 2]).expect("refused");
    assert!(refusal.contains("not in the member set"), "{refusal}");
}

#[test]
fn only_the_shared_cluster_san_is_admitted() {
    // The gate that matters. Chain validation proves the peer holds *a*
    // Product-CA certificate, and in an IPMX deployment that is every device in
    // the building -- so without this a camera could propose log entries.
    assert_eq!(
        refuse_certificate("cluster.example.com", &["cluster.example.com".to_owned()]),
        None,
    );

    let refusal = refuse_certificate("cluster.example.com", &["camera-17.example.com".to_owned()])
        .expect("refused");
    assert!(refusal.contains("camera-17.example.com"), "{refusal}");
    assert!(refusal.contains("cluster.example.com"), "{refusal}");
}

#[test]
fn a_wildcard_san_does_not_admit_the_cluster_name() {
    // No wildcards, deliberately: `*.example.com` would readmit exactly the set
    // this check exists to exclude.
    assert!(
        refuse_certificate("cluster.example.com", &["*.example.com".to_owned()]).is_some(),
        "a wildcard SAN was accepted, which widens the gate to every device \
         the Product CA has signed",
    );
}

#[test]
fn a_connection_with_no_certificate_is_refused() {
    // Belt and braces against a handshake that should already have failed --
    // but "no names" is also what a plaintext connection produces, and
    // accepting it here would turn a misconfiguration into an open door.
    let refusal = refuse_certificate("cluster.example.com", &[]).expect("refused");
    assert!(refusal.contains("no peer certificate"), "{refusal}");
}

// -- over real sockets ------------------------------------------------------

#[tokio::test]
async fn two_members_link_up_and_exchange_incarnations() {
    let (a, recorder_a, b, recorder_b) = pair("nmos-registry-test").await;

    assert_eq!(a.live(), vec![1]);
    assert_eq!(b.live(), vec![0]);
    assert!(recorder_a.peer_up.load(Ordering::SeqCst) >= 1);
    assert_eq!(
        recorder_a.last_incarnation.load(Ordering::SeqCst),
        11,
        "the peer's incarnation did not reach the handler, so a leader cannot \
         tell a restarted member from one it has always been talking to",
    );
    assert_eq!(recorder_b.last_incarnation.load(Ordering::SeqCst), 10);

    a.close().await;
    b.close().await;
}

#[tokio::test]
async fn a_fire_and_forget_message_reaches_the_peer() {
    let (a, _ra, b, recorder_b) = pair("nmos-registry-test").await;

    a.send(
        1,
        &Message::Promote(Promote {
            term: 4,
            leader: 0,
            through_index: 9,
        }),
        Stream::Control,
    );

    assert!(
        until(|| recorder_b.promotes.load(Ordering::SeqCst) == 1).await,
        "the promotion never arrived",
    );

    a.close().await;
    b.close().await;
}

#[tokio::test]
async fn a_correlated_request_gets_its_own_reply() {
    let (a, _ra, b, recorder_b) = pair("nmos-registry-test").await;

    let reply = a
        .request(
            1,
            &Message::AppendEntries(AppendEntries {
                term: 3,
                leader: 0,
                prev_log_index: 5,
                prev_log_term: 2,
                leader_commit: 4,
                request_id: 0,
                entries: Vec::new(),
            }),
            Stream::Control,
            Some(2_000),
        )
        .await
        .expect("answered");

    match reply {
        Message::AppendEntriesReply(ref m) => {
            assert!(m.success);
            assert_eq!(m.match_index, 5);
            assert_ne!(
                m.request_id, 0,
                "the reply carries no correlation id, so a second concurrent \
                 request would be answered with the first one's result",
            );
        }
        other => panic!("answered with {other:?}"),
    }
    assert_eq!(recorder_b.appends.load(Ordering::SeqCst), 1);

    a.close().await;
    b.close().await;
}

/// A snapshot chunk can be awaited like any request that carries an id.
///
/// The node sends chunks fire-and-forget and handles their answers itself, but
/// a chunk carries a correlation id (S9), and `request` refused one -- "carries
/// no request_id and cannot be awaited" -- where the Python's awaits it: the
/// stamping and reading of ids had not been extended to the chunk and its
/// answer. Found when a test tried to await a chunk (part 17 of the fix
/// record).
#[tokio::test]
async fn a_snapshot_chunk_can_be_awaited_as_a_correlated_request() {
    let (a, _ra, b, _rb) = pair("nmos-registry-test").await;

    // BULK connects in its own time; until it has, there is no link to ask on.
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let reply = loop {
        match a
            .request(1, &chunk(vec![b'x'; 16]), Stream::Bulk, Some(2_000))
            .await
        {
            Err(ref error)
                if error.0.starts_with("no link") && std::time::Instant::now() < deadline =>
            {
                tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            }
            other => break other,
        }
    };

    match reply {
        Ok(Message::InstallSnapshotReply(ref m)) => {
            assert_eq!(m.bytes_received, 16);
            assert_ne!(m.request_id, 0, "the answer carries no correlation id");
        }
        other => panic!("a snapshot chunk could not be awaited as a request: {other:?}"),
    }
    a.close().await;
    b.close().await;
}

#[tokio::test]
async fn concurrent_requests_do_not_cross() {
    // The property correlation ids exist for. Without them the second reply
    // resolves the first caller, and a leader acts on an answer to a question
    // it did not ask.
    let (a, _ra, b, _rb) = pair("nmos-registry-test").await;

    // The messages outlive the futures that borrow them, deliberately: each
    // request borrows its own, so they have to be built first rather than
    // inline.
    let messages: Vec<Message> = (0..20u64)
        .map(|index| {
            Message::AppendEntries(AppendEntries {
                term: 3,
                leader: 0,
                prev_log_index: index,
                prev_log_term: 2,
                leader_commit: 0,
                request_id: 0,
                entries: Vec::new(),
            })
        })
        .collect();
    let waiters = messages
        .iter()
        .map(|message| a.request(1, message, Stream::Control, Some(2_000)));

    let replies = futures_util::future::join_all(waiters).await;
    for (index, reply) in replies.into_iter().enumerate() {
        match reply.expect("answered") {
            Message::AppendEntriesReply(ref m) => assert_eq!(
                m.match_index, index as u64,
                "reply {index} carries another request's answer",
            ),
            other => panic!("answered with {other:?}"),
        }
    }

    a.close().await;
    b.close().await;
}

#[tokio::test]
async fn an_async_handler_answers_a_forward() {
    let (a, _ra, b, recorder_b) = pair("nmos-registry-test").await;

    let reply = a
        .request(
            1,
            &Message::Forward(Forward {
                verb: "register".to_owned(),
                resource_type: "sender".to_owned(),
                resource_id: "s-1".to_owned(),
                body_text: "{\"id\":\"s-1\"}".to_owned(),
                request_id: 0,
            }),
            Stream::Control,
            Some(2_000),
        )
        .await
        .expect("answered");

    match reply {
        Message::ForwardReply(ref m) => {
            assert!(m.ok);
            assert_eq!(m.applied_index, 7);
        }
        other => panic!("answered with {other:?}"),
    }
    assert_eq!(recorder_b.seen.lock().await.as_slice(), ["forward s-1"]);

    a.close().await;
    b.close().await;
}

#[tokio::test]
async fn the_bulk_stream_is_separate_from_control() {
    // A snapshot must not head-of-line-block the heartbeat timer, which means
    // it must not share the link with it.
    let (a, _ra, b, _rb) = pair("nmos-registry-test").await;
    let chunk = Message::InstallSnapshot(InstallSnapshot {
        term: 2,
        leader: 0,
        last_index: 100,
        last_term: 1,
        offset: 0,
        data: vec![1, 2, 3, 4],
        done: true,
        ownership: Vec::new(),
        request_id: 7,
    });

    // BULK connects in its own time; until it has, there is no link to ask on.
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(5);
    let reply = loop {
        match a.request(1, &chunk, Stream::Bulk, Some(2_000)).await {
            Err(ref error)
                if error.0.starts_with("no link") && std::time::Instant::now() < deadline =>
            {
                tokio::time::sleep(std::time::Duration::from_millis(20)).await;
            }
            other => break other,
        }
    };

    // Answered, and so carried both ways by the BULK link: an answer is matched
    // to the waiters of the link it arrived on, and a request on BULK waits on
    // BULK's. Until part 18 of the fix record this asserted that the request
    // failed, on the premise that a snapshot's answer carries no correlation id
    // -- false since S9 -- and passed because this transport refused to stamp a
    // chunk at all, so the frame was never sent.
    match reply {
        Ok(Message::InstallSnapshotReply(ref m)) => assert!(
            m.done && m.bytes_received == 4,
            "the chunk was answered as another: {m:?}",
        ),
        other => panic!("the BULK link did not carry a chunk and its answer: {other:?}"),
    }

    a.close().await;
    b.close().await;
}

#[tokio::test]
async fn a_request_to_an_unknown_member_fails_at_once() {
    // Rather than waiting out a deadline for a link that was never going to
    // exist.
    let (a, _ra, b, _rb) = pair("nmos-registry-test").await;

    let started = std::time::Instant::now();
    let error = a
        .request(
            7,
            &Message::AppendEntries(AppendEntries {
                term: 1,
                leader: 0,
                prev_log_index: 0,
                prev_log_term: 0,
                leader_commit: 0,
                request_id: 0,
                entries: Vec::new(),
            }),
            Stream::Control,
            Some(2_000),
        )
        .await
        .expect_err("no such member");

    assert!(error.0.contains("no link to member 7"), "{}", error.0);
    assert!(
        started.elapsed() < std::time::Duration::from_millis(500),
        "it waited out the deadline for a link that does not exist",
    );

    a.close().await;
    b.close().await;
}

#[tokio::test]
async fn a_member_in_another_cluster_is_refused_and_does_not_link() {
    // The end-to-end form of the token check: not a connection that works
    // badly, a connection that does not open.
    let probe = std::net::TcpListener::bind(loopback(0)).expect("a port");
    let addr = probe.local_addr().expect("an address");
    drop(probe);

    let mut peers = HashMap::new();
    peers.insert(1, ("127.0.0.1".to_owned(), addr.port()));
    let outsider = Arc::new(RaftTransport::new(TransportSettings {
        local: 0,
        peers,
        bind: loopback(0),
        cluster_id: "nmos-registry-somewhere-else".to_owned(),
        member_name: "outsider".to_owned(),
        incarnation: 1,
        tls: None,
        rpc_timeout_ms: 500,
        conn_read_timeout_ms: CONN_READ_TIMEOUT_MS,
    }));

    let mut ours = HashMap::new();
    ours.insert(0, ("127.0.0.1".to_owned(), 1));
    let member = Arc::new(RaftTransport::new(TransportSettings {
        local: 1,
        peers: ours,
        bind: addr,
        cluster_id: "nmos-registry-ours".to_owned(),
        member_name: "member-1".to_owned(),
        incarnation: 1,
        tls: None,
        rpc_timeout_ms: 500,
        conn_read_timeout_ms: CONN_READ_TIMEOUT_MS,
    }));

    let recorder = Arc::new(Recorder::default());
    member
        .start(Arc::clone(&recorder) as Arc<dyn PeerHandler>)
        .await
        .expect("listens");
    outsider
        .start(Arc::new(Recorder::default()) as Arc<dyn PeerHandler>)
        .await
        .expect("listens");

    // Long enough for several reconnection attempts, each of which must be
    // refused again rather than eventually succeeding.
    tokio::time::sleep(std::time::Duration::from_millis(400)).await;
    assert!(
        outsider.live().is_empty(),
        "a member from another cluster linked up",
    );

    outsider.close().await;
    member.close().await;
}

#[tokio::test]
async fn closing_reports_the_peer_down() {
    // The node uses this to stop counting a member toward quorum. A link that
    // went away silently would leave a leader waiting for acknowledgements
    // that are never coming.
    let (a, _ra, b, recorder_b) = pair("nmos-registry-test").await;

    a.close().await;
    assert!(
        until(|| recorder_b.peer_down.load(Ordering::SeqCst) >= 1).await,
        "the surviving member never learned its peer had gone",
    );
    assert!(until(|| b.live().is_empty()).await);

    b.close().await;
}

// -- a handler that could not save ------------------------------------------
//
// What the Python's transport does with the exception a failed save raises out
// of its handler: nothing is answered, and the connection the message came by
// ends -- closed by `_serve` on the side that received a request, dropped and
// dialled again by `_maintain` on the side that received a reply.

#[tokio::test(flavor = "multi_thread")]
async fn a_request_that_cannot_be_saved_gets_no_answer_and_ends_its_connection() {
    let (a, recorder_a, b, recorder_b) = pair("unsaved-request").await;
    let downs = recorder_a.peer_down.load(Ordering::SeqCst);
    recorder_b.saves_fail.store(true, Ordering::SeqCst);

    let heartbeat = Message::AppendEntries(AppendEntries {
        term: 3,
        leader: 0,
        prev_log_index: 0,
        prev_log_term: 0,
        leader_commit: 0,
        request_id: 0,
        entries: Vec::new(),
    });
    let answer = a.request(1, &heartbeat, Stream::Control, Some(2_000)).await;
    assert_eq!(
        recorder_b.appends.load(Ordering::SeqCst),
        1,
        "the append never arrived"
    );
    assert!(
        answer.is_err(),
        "member 1 answered an append it could not save: {answer:?}",
    );
    assert!(
        until(|| recorder_a.peer_down.load(Ordering::SeqCst) > downs).await,
        "the connection outlived an append member 1 could not save",
    );

    // Dialled again, and answered on the new connection once saves work.
    recorder_b.saves_fail.store(false, Ordering::SeqCst);
    assert!(
        until(|| a.live() == vec![1]).await,
        "the link never came back"
    );
    let again = a.request(1, &heartbeat, Stream::Control, Some(2_000)).await;
    assert!(
        again.is_ok(),
        "no answer once saves worked again: {again:?}"
    );

    a.close().await;
    b.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_reply_that_cannot_be_saved_ends_the_link_it_came_by() {
    let (a, recorder_a, b, _recorder_b) = pair("unsaved-reply").await;
    let downs = recorder_a.peer_down.load(Ordering::SeqCst);
    recorder_a.saves_fail.store(true, Ordering::SeqCst);

    // Fire-and-forget, so the answer goes to the handler, which cannot save
    // what it says.
    a.send(
        1,
        &Message::AppendEntries(AppendEntries {
            term: 3,
            leader: 0,
            prev_log_index: 0,
            prev_log_term: 0,
            leader_commit: 0,
            request_id: 0,
            entries: Vec::new(),
        }),
        Stream::Control,
    );
    assert!(
        until(|| recorder_a.append_replies.load(Ordering::SeqCst) == 1).await,
        "the reply never reached member 0",
    );
    assert!(
        until(|| recorder_a.peer_down.load(Ordering::SeqCst) > downs).await,
        "the link outlived a reply member 0 could not save",
    );

    recorder_a.saves_fail.store(false, Ordering::SeqCst);
    assert!(
        until(|| a.live() == vec![1]).await,
        "the link never came back"
    );

    a.close().await;
    b.close().await;
}

/// A forward and concurrent append traffic each get their own answer.
///
/// The ordinary path, where the two ids do *not* collide: a forward is
/// answered with a `ForwardReply` and an append reply reaches the handler
/// rather than being swallowed. The collision itself is reproduced by
/// `a_reply_of_the_wrong_kind_does_not_satisfy_a_waiter` below.
///
/// Kept separate because this one passes with and without the kind check --
/// confirmed by mutation -- so on its own it would be a test that looks like
/// coverage and is not.
#[tokio::test(flavor = "multi_thread")]
async fn a_forward_and_an_append_each_get_their_own_answer() {
    let (a, recorder_a, b, recorder_b) = pair("collision").await;

    a.send(
        1,
        &Message::AppendEntries(AppendEntries {
            term: 1,
            leader: 0,
            prev_log_index: 0,
            prev_log_term: 0,
            leader_commit: 0,
            request_id: 1,
            entries: Vec::new(),
        }),
        Stream::Control,
    );

    let reply = a
        .request(
            1,
            &Message::Forward(Forward {
                verb: "register".to_owned(),
                resource_type: "node".to_owned(),
                resource_id: "a-node".to_owned(),
                body_text: "{\"id\":\"a-node\"}".to_owned(),
                request_id: 0,
            }),
            Stream::Control,
            Some(5_000),
        )
        .await
        .expect("the forward was answered");

    assert!(
        matches!(reply, Message::ForwardReply(_)),
        "the forward was answered with {:?}, not a ForwardReply",
        reply.message_type(),
    );
    assert!(
        until(|| recorder_b.appends.load(Ordering::SeqCst) >= 1).await,
        "b never received the append",
    );
    assert!(
        until(|| recorder_a.append_replies.load(Ordering::SeqCst) >= 1).await,
        "the append reply never reached the handler, so the leader would never \
         learn where that peer had got to",
    );

    a.close().await;
    b.close().await;
}

/// Every request maps to the one reply that answers it.
///
/// The table `resolve` discriminates on. An id alone cannot separate the
/// transport's request ids from the append ids the node mints for flow
/// control -- both spaces start at one -- so the kind is what makes a match
/// mean something.
#[test]
fn every_request_knows_the_reply_it_expects() {
    use nmos_registry_raft::wire::MessageType;

    let cases: &[(Message, Option<MessageType>)] = &[
        (
            Message::Forward(Forward {
                verb: String::new(),
                resource_type: String::new(),
                resource_id: String::new(),
                body_text: String::new(),
                request_id: 0,
            }),
            Some(MessageType::ForwardReply),
        ),
        (
            Message::AppendEntries(AppendEntries {
                term: 0,
                leader: 0,
                prev_log_index: 0,
                prev_log_term: 0,
                leader_commit: 0,
                request_id: 0,
                entries: Vec::new(),
            }),
            Some(MessageType::AppendEntriesReply),
        ),
    ];
    for (message, expected) in cases {
        assert_eq!(
            message.expected_reply(),
            *expected,
            "{:?} expects the wrong reply",
            message.message_type(),
        );
    }

    // A reply draws nothing, so it can never itself be awaited -- which is
    // what stops a waiter being registered for one.
    assert_eq!(
        Message::ForwardReply(ForwardReply {
            ok: true,
            created: false,
            error: String::new(),
            detail: String::new(),
            applied_index: 0,
            not_owner: false,
            request_id: 0,
            owner: None,
        })
        .expected_reply(),
        None,
    );
}

/// Every message that carries a correlation id is stamped with one, and has it
/// read back: the ten both implementations' messages carry it on.
///
/// The Python's `_with_request_id` and `_resolve` take the id of any message
/// with the field; these two name the messages, and named eight until part 17
/// of the fix record found the snapshot chunk and its answer missing -- so
/// `request` refused a chunk, and an answer to one could not reach a waiter.
#[test]
fn every_message_that_carries_a_request_id_is_stamped_and_read() {
    use nmos_registry_raft::transport::{request_id_of, with_request_id};

    let carriers = [
        Message::AppendEntries(AppendEntries {
            term: 1,
            leader: 0,
            prev_log_index: 0,
            prev_log_term: 0,
            leader_commit: 0,
            request_id: 0,
            entries: Vec::new(),
        }),
        Message::AppendEntriesReply(AppendEntriesReply {
            term: 1,
            success: true,
            match_index: 0,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: 0,
        }),
        Message::InstallSnapshot(InstallSnapshot {
            term: 1,
            leader: 0,
            last_index: 1,
            last_term: 1,
            offset: 0,
            data: Vec::new(),
            done: false,
            ownership: Vec::new(),
            request_id: 0,
        }),
        Message::InstallSnapshotReply(InstallSnapshotReply {
            term: 1,
            bytes_received: 0,
            done: false,
            commit_index: 0,
            request_id: 0,
        }),
        Message::ReadIndex(ReadIndex { request_id: 0 }),
        Message::ReadIndexReply(ReadIndexReply {
            ok: true,
            index: 0,
            reason: String::new(),
            request_id: 0,
        }),
        Message::Propose(Propose {
            proposals: Vec::new(),
            request_id: 0,
        }),
        Message::ProposeReply(ProposeReply {
            accepted: true,
            reason: String::new(),
            term: 1,
            first_index: 1,
            request_id: 0,
            leader: None,
        }),
        Message::Forward(Forward {
            verb: String::new(),
            resource_type: String::new(),
            resource_id: String::new(),
            body_text: String::new(),
            request_id: 0,
        }),
        Message::ForwardReply(ForwardReply {
            ok: true,
            created: false,
            error: String::new(),
            detail: String::new(),
            applied_index: 0,
            not_owner: false,
            request_id: 0,
            owner: None,
        }),
    ];
    let losing: Vec<String> = carriers
        .iter()
        .filter_map(|message| {
            let kind = message.message_type();
            match with_request_id(message, 7) {
                None => Some(format!("{kind:?} is not stamped")),
                Some(stamped) if request_id_of(&stamped) != 7 => {
                    Some(format!("{kind:?} is not read back"))
                }
                Some(_) => None,
            }
        })
        .collect();
    assert!(
        losing.is_empty(),
        "messages that carry a request_id and lose it: {losing:?}"
    );
}

/// Starting a transport does not keep the node alive.
///
/// The node owns its transport (`RaftNode` holds `Arc<dyn Transport>`) and
/// `start` hands the transport the node back as its handler. Held strongly
/// that is a cycle -- transport -> node -> transport -- and `Arc` is reference
/// counted, not traced, so nothing collects it.
///
/// `close` clears the handler, so the cycle was broken in practice. This
/// asserts the structural property instead, because that is the one a future
/// refactor can silently remove: after `start` returns, the strong count is
/// back where it began, whatever anyone later does or forgets to do in
/// `close`.
#[tokio::test(flavor = "multi_thread")]
async fn starting_a_transport_takes_no_strong_reference_to_the_handler() {
    let probe = std::net::TcpListener::bind(loopback(0)).expect("a port");
    let addr = probe.local_addr().expect("an address");
    drop(probe);

    let transport = Arc::new(RaftTransport::new(TransportSettings {
        local: 0,
        peers: HashMap::new(),
        bind: addr,
        cluster_id: "ownership".to_owned(),
        member_name: "member-0".to_owned(),
        incarnation: 1,
        tls: None,
        rpc_timeout_ms: 500,
        conn_read_timeout_ms: CONN_READ_TIMEOUT_MS,
    }));

    let recorder = Arc::new(Recorder::default());
    let before = Arc::strong_count(&recorder);
    transport
        .start(Arc::clone(&recorder) as Arc<dyn PeerHandler>)
        .await
        .expect("listens");

    assert_eq!(
        Arc::strong_count(&recorder),
        before,
        "the transport kept a strong reference to its handler, which with the \
         node's own `Arc<dyn Transport>` is a cycle that nothing collects",
    );

    // And it still works through the weak reference: a live handler is
    // reachable, which is the other half of the property.
    let watch = Arc::downgrade(&recorder);
    assert!(watch.upgrade().is_some(), "the handler went early");

    transport.close().await;
    drop(recorder);
    assert!(
        watch.upgrade().is_none(),
        "the handler outlived every strong handle to it",
    );
}

/// The id collision, reproduced rather than argued about.
///
/// Two id spaces meet in one `pending` map: the transport mints ids for
/// `request`, while `AppendEntries` carries an id of the leader's own minting
/// for flow control. Both start at one, so they collide -- most readily just
/// after a leader change, when a member that had been a follower has a low
/// append sequence and a low request id at the same time.
///
/// Matched on the number alone, the `AppendEntriesReply` is handed to the
/// caller awaiting a `ForwardReply`: that caller sees the wrong message and
/// gives up -- a registration refused with 503 -- and the append reply never
/// reaches the node, so the peer's `match_index` stalls for a tick.
///
/// **Constructible only because the forward handler is served off the link
/// reader.** Held open on the reader, as it was until the deadlock fix, this
/// would stall b's link and the colliding append would never be read at all --
/// which is why an earlier attempt at this test passed with and without the
/// fix, and proved nothing.
#[tokio::test(flavor = "multi_thread")]
async fn a_reply_of_the_wrong_kind_does_not_satisfy_a_waiter() {
    let (a, recorder_a, b, recorder_b) = pair("wrong-kind").await;

    // Hold the next forward open at b, so its waiter stays registered.
    let (release, held) = tokio::sync::oneshot::channel();
    *recorder_b.forward_gate.lock().await = Some(held);

    // The first request of this transport's life takes id 1.
    let requester = Arc::clone(&a);
    let forwarding = tokio::spawn(async move {
        requester
            .request(
                1,
                &Message::Forward(Forward {
                    verb: "register".to_owned(),
                    resource_type: "node".to_owned(),
                    resource_id: "a-node".to_owned(),
                    body_text: "{\"id\":\"a-node\"}".to_owned(),
                    request_id: 0,
                }),
                Stream::Control,
                Some(10_000),
            )
            .await
    });
    assert!(
        until(|| recorder_b
            .forward_gate
            .try_lock()
            .is_ok_and(|gate| gate.is_none()))
        .await,
        "the forward never reached b, so no waiter is outstanding to collide with",
    );

    // An append carrying the *same* number, from the other id space entirely.
    a.send(
        1,
        &Message::AppendEntries(AppendEntries {
            term: 1,
            leader: 0,
            prev_log_index: 0,
            prev_log_term: 0,
            leader_commit: 0,
            request_id: 1,
            entries: Vec::new(),
        }),
        Stream::Control,
    );

    assert!(
        until(|| recorder_a.append_replies.load(Ordering::SeqCst) >= 1).await,
        "the append reply never reached the handler: it was delivered to the \
         caller awaiting a ForwardReply instead, which loses the leader's view \
         of that peer as well as answering the wrong question",
    );

    // The receiver is alive -- the handler is waiting on it -- so this
    // cannot fail; naming the result says that rather than discarding it.
    let _released = release.send(());
    let reply = forwarding.await.expect("the task ran").expect("a reply");
    assert!(
        matches!(reply, Message::ForwardReply(_)),
        "the forward was answered with {:?}: the append's reply carried the \
         same number and was handed to this caller",
        reply.message_type(),
    );

    a.close().await;
    b.close().await;
}

// -- a connection that stops moving is closed --------------------------------
//
// Over a real network a connection can go quiet without breaking: a path that
// stops carrying packets sends no reset, and TCP keeps whatever was written,
// retransmitting it until the path comes back -- then delivers all of it, in
// order. A transport that waits for that has no bound on how late a message
// arrives; the chaos soak measured writes answered 503 committing 4.6-26 s
// later. etcd bounds it (`rafthttp`): a 5 s read and write deadline on every
// peer connection, a link heartbeat every third of that, and a connection whose
// deadline passes is closed with its queue discarded. These tests hold a
// connection's bytes in a relay the member dials through -- the model of a
// stalled path, which a cut is not -- and check what the transport does.

/// Small, so these take seconds; the mechanism does not depend on the value.
const READ_TIMEOUT_MS: u64 = 500;

/// How long a stalled path holds what it was given: well past the deadline.
const HOLD_MS: u64 = 2_000;

/// One direction of a member's connections to a peer, through a relay that can
/// hold every byte it carries, both ways, and then let it all go in order.
///
/// Holding, not dropping: a relay that closed the connection would be a cut,
/// and a cut is what every transport already notices.
struct Relay {
    port: u16,
    hold_ms: Arc<AtomicU64>,
    accepting: tokio::task::JoinHandle<()>,
}

impl Relay {
    async fn start(target: std::net::SocketAddr) -> Self {
        let listener = tokio::net::TcpListener::bind(loopback(0))
            .await
            .expect("a port");
        let port = listener.local_addr().expect("an address").port();
        let hold_ms = Arc::new(AtomicU64::new(0));
        let shared = Arc::clone(&hold_ms);
        let accepting = tokio::spawn(async move {
            while let Ok((caller, _)) = listener.accept().await {
                let Ok(callee) = tokio::net::TcpStream::connect(target).await else {
                    continue;
                };
                let hold = Arc::clone(&shared);
                tokio::spawn(async move {
                    let (from_caller, to_caller) = caller.into_split();
                    let (from_callee, to_callee) = callee.into_split();
                    let mut forward =
                        tokio::spawn(relay(from_caller, to_callee, Arc::clone(&hold)));
                    let mut backward = tokio::spawn(relay(from_callee, to_caller, hold));
                    // Either direction ending ends both, as a proxy closing its
                    // two sockets does: what one side still holds is lost.
                    tokio::select! {
                        _ = &mut forward => {}
                        _ = &mut backward => {}
                    }
                    forward.abort();
                    backward.abort();
                });
            }
        });
        Self {
            port,
            hold_ms,
            accepting,
        }
    }

    /// Hold every chunk read from now on for `ms` before passing it on.
    fn hold(&self, ms: u64) {
        self.hold_ms.store(ms, Ordering::SeqCst);
    }
}

impl Drop for Relay {
    fn drop(&mut self) {
        self.accepting.abort();
    }
}

async fn relay(
    mut from: tokio::net::tcp::OwnedReadHalf,
    mut to: tokio::net::tcp::OwnedWriteHalf,
    hold_ms: Arc<AtomicU64>,
) {
    use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
    let mut buffer = vec![0u8; 65_536];
    loop {
        let read = match from.read(&mut buffer).await {
            Ok(0) | Err(_) => return,
            Ok(read) => read,
        };
        let hold = hold_ms.load(Ordering::SeqCst);
        if hold > 0 {
            tokio::time::sleep(std::time::Duration::from_millis(hold)).await;
        }
        if to.write_all(&buffer[..read]).await.is_err() {
            return;
        }
    }
}

/// Members 0 and 1, each reaching the other only through a relay.
struct Relayed {
    transports: [Arc<RaftTransport>; 2],
    recorders: [Arc<Recorder>; 2],
    /// `relays[i]` carries member `i`'s connections to the other.
    relays: [Relay; 2],
}

async fn relayed() -> Relayed {
    let probes = [
        std::net::TcpListener::bind(loopback(0)).expect("a port"),
        std::net::TcpListener::bind(loopback(0)).expect("a port"),
    ];
    let addrs = [
        probes[0].local_addr().expect("an address"),
        probes[1].local_addr().expect("an address"),
    ];
    drop(probes);
    let relays = [Relay::start(addrs[1]).await, Relay::start(addrs[0]).await];
    let make = |local: u64| {
        let mut peers = HashMap::new();
        peers.insert(
            1 - local,
            ("127.0.0.1".to_owned(), relays[local as usize].port),
        );
        Arc::new(RaftTransport::new(TransportSettings {
            local,
            peers,
            bind: addrs[local as usize],
            cluster_id: "liveness".to_owned(),
            member_name: format!("member-{local}"),
            incarnation: local + 10,
            tls: None,
            rpc_timeout_ms: 2_000,
            conn_read_timeout_ms: READ_TIMEOUT_MS,
        }))
    };
    let transports = [make(0), make(1)];
    let recorders = [Arc::new(Recorder::default()), Arc::new(Recorder::default())];
    for (transport, recorder) in transports.iter().zip(&recorders) {
        transport
            .start(Arc::clone(recorder) as Arc<dyn PeerHandler>)
            .await
            .expect("listens");
    }
    assert!(
        until(|| !transports[0].live().is_empty() && !transports[1].live().is_empty()).await,
        "the two members never linked up",
    );
    Relayed {
        transports,
        recorders,
        relays,
    }
}

impl Relayed {
    async fn close(self) {
        for transport in &self.transports {
            transport.close().await;
        }
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_message_held_past_the_read_deadline_is_never_delivered() {
    let pair = relayed().await;
    pair.relays[0].hold(HOLD_MS);
    pair.transports[0].send(
        1,
        &Message::Promote(Promote {
            term: 4,
            leader: 0,
            through_index: 9,
        }),
        Stream::Control,
    );
    // Long enough that the relay has taken the frame and is holding it;
    // lifting the hold then frees only what comes after.
    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
    pair.relays[0].hold(0);
    tokio::time::sleep(std::time::Duration::from_millis(HOLD_MS + 1_000)).await;

    assert_eq!(
        pair.recorders[1].promotes.load(Ordering::SeqCst),
        0,
        "a message held {HOLD_MS} ms by a stalled path was delivered when the path recovered, \
         {}x the read deadline late: nothing bounds how late a message can arrive",
        HOLD_MS / READ_TIMEOUT_MS,
    );
    pair.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_link_that_stops_moving_is_reported_down_within_the_deadline() {
    let pair = relayed().await;
    let stalled = std::time::Instant::now();
    pair.relays[0].hold(10 * HOLD_MS);

    let down = until(|| pair.recorders[0].peer_down.load(Ordering::SeqCst) > 0).await;
    let after = stalled.elapsed();

    assert!(
        down,
        "member 0's link to member 1 carried nothing for {after:?} and is still reported up",
    );
    // The deadline, plus up to one heartbeat interval for the last frame read
    // before the stall, plus scheduling.
    let bound = std::time::Duration::from_millis(READ_TIMEOUT_MS + READ_TIMEOUT_MS / 3 + 250);
    assert!(after <= bound, "reported down {after:?} into the stall");
    pair.close().await;
}

// -- both ends of a connection beat --------------------------------------------
//
// A connection carries requests one way and their answers the other, and the
// dialling end's reads were fed only by answers -- to its requests, and to its
// own heartbeat -- all of them queued behind whatever it was sending. So a
// frame taking longer than the deadline to cross, a snapshot chunk over a slow
// link, left its sender hearing nothing, and the sender closed a connection
// that was working: measured, a leader dialling a new BULK connection every
// ~5.1 s while two 16 KiB chunks crawled across at 2 KiB/s, never past the
// first (part 17 of the fix record). The accepting end now sends a heartbeat of
// its own, so each end's reads are fed by the other end's timer, whatever the
// other direction carries. Port of `TestBothEndsOfAConnectionBeat`.

/// What a slowed path holds every piece it relays, both ways: a frame of many
/// pieces crawls across, its reader seeing bytes well inside the deadline.
const TRICKLE_MS: u64 = 150;

/// A chunk as a leader sends one; its size is what a test makes slow.
fn chunk(data: Vec<u8>) -> Message {
    Message::InstallSnapshot(InstallSnapshot {
        term: 3,
        leader: 0,
        last_index: 9,
        last_term: 3,
        offset: 0,
        data,
        done: false,
        ownership: Vec::new(),
        request_id: 0,
    })
}

#[tokio::test(flavor = "multi_thread")]
async fn the_accepting_end_of_a_connection_sends_its_own_heartbeat() {
    let (member, _recorder, address) =
        lone_member_reading("heartbeat", nowhere(), READ_TIMEOUT_MS).await;
    let mut socket = as_member_one(address, "heartbeat").await;

    // Admitted -- and now say nothing, and listen.
    let heard = tokio::time::timeout(
        std::time::Duration::from_millis(READ_TIMEOUT_MS),
        read_frame(&mut socket),
    )
    .await;
    match heard {
        Ok(Ok(frame)) => assert!(
            frame.message_type == MessageType::Pong && frame.is_reply(),
            "the accepting end's heartbeat was a {:?}; a Pong nobody asked for draws no answer",
            frame.message_type,
        ),
        Ok(Err(error)) => panic!(
            "the accepting end of a connection said nothing before it closed it ({error}): the \
             dialling end's reads are fed only by answers, which queue behind whatever it is \
             sending"
        ),
        Err(_) => panic!(
            "the accepting end of a connection said nothing for {READ_TIMEOUT_MS} ms, the whole \
             read deadline: the dialling end's reads are fed only by answers, which queue behind \
             whatever it is sending"
        ),
    }
    member.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_frame_slower_than_the_read_deadline_is_answered() {
    // Sent as a leader sends a chunk, and answered as a member answers one: on
    // the connection it came by, to the sender's handler.
    let pair = relayed().await;
    let answered =
        |bytes: usize| pair.recorders[0].chunk_answered.load(Ordering::SeqCst) >= bytes as u64;
    // BULK up, and answering, before anything is slowed.
    let warming = std::time::Instant::now();
    while !answered(16) {
        assert!(
            warming.elapsed() < std::time::Duration::from_secs(5),
            "BULK never came up"
        );
        pair.transports[0].send(1, &chunk(vec![b'x'; 16]), Stream::Bulk);
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }

    // Half a megabyte crawls across in pieces, each held on the way, the member
    // reading some of it every `TRICKLE_MS` -- inside its deadline -- while
    // nothing it could answer comes back.
    pair.relays[0].hold(TRICKLE_MS);
    let began = std::time::Instant::now();
    pair.transports[0].send(1, &chunk(vec![0; 512 * 1024]), Stream::Bulk);
    let limit = std::time::Duration::from_secs(20);
    while !answered(512 * 1024) && began.elapsed() < limit {
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
    let took = began.elapsed();
    assert!(
        answered(512 * 1024),
        "a frame taking longer than the {READ_TIMEOUT_MS} ms read deadline to cross was never \
         answered in {took:?}: its sender heard nothing while it crossed, and closed a connection \
         that was working",
    );
    assert!(
        took > std::time::Duration::from_millis(2 * READ_TIMEOUT_MS),
        "the frame crossed in {took:?}, inside the deadline: this proves nothing about one that \
         does not",
    );
    pair.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn an_idle_link_stays_up_past_the_read_deadline() {
    // The heartbeat's guard: with nothing to send, a link must still carry
    // enough to keep both ends' reads inside the deadline.
    let pair = relayed().await;
    tokio::time::sleep(std::time::Duration::from_millis(5 * READ_TIMEOUT_MS)).await;
    for (member, recorder) in pair.recorders.iter().enumerate() {
        assert_eq!(
            recorder.peer_down.load(Ordering::SeqCst),
            0,
            "member {member} saw an idle, healthy link go down",
        );
    }
    pair.close().await;
}

// -- an undecodable frame -----------------------------------------------------
//
// A frame that passes its checksum and will not decode is warned about ("raft:
// undecodable frame") and answered with nothing, and the connection carries on:
// the checksum proves the stream is still aligned, so the fault is the
// sender's. The Python does the same since it was made to match
// (`TestAnUndecodableFrameIsSkipped`). No member of this build sends such a
// frame, so here the test plays the peer that does.

/// A frame whose checksum passes and whose payload no member can decode.
fn undecodable(kind: MessageType, stream: Stream, reply: bool) -> Vec<u8> {
    let flags = if reply { FLAG_REPLY } else { 0 };
    // A varint that never ends: the first field's tag already fails.
    encode_frame(&Frame::new(stream, kind, flags, vec![0xFF; 11])).expect("a frame")
}

/// A heartbeat from `leader` in term 3, correlated by `request_id`.
fn heartbeat(leader: u64, request_id: u64) -> Message {
    Message::AppendEntries(AppendEntries {
        term: 3,
        leader,
        prev_log_index: 0,
        prev_log_term: 0,
        leader_commit: 0,
        request_id,
        entries: Vec::new(),
    })
}

/// Member 0 alone, listening on a port of its own, with member 1 at `peer`.
async fn lone_member(
    cluster_id: &str,
    peer: std::net::SocketAddr,
) -> (Arc<RaftTransport>, Arc<Recorder>, std::net::SocketAddr) {
    lone_member_reading(cluster_id, peer, CONN_READ_TIMEOUT_MS).await
}

/// As [`lone_member`], with every connection's read deadline at
/// `read_timeout_ms`.
async fn lone_member_reading(
    cluster_id: &str,
    peer: std::net::SocketAddr,
    read_timeout_ms: u64,
) -> (Arc<RaftTransport>, Arc<Recorder>, std::net::SocketAddr) {
    let probe = std::net::TcpListener::bind(loopback(0)).expect("a port");
    let address = probe.local_addr().expect("an address");
    drop(probe);
    let mut peers = HashMap::new();
    peers.insert(1, ("127.0.0.1".to_owned(), peer.port()));
    let member = Arc::new(RaftTransport::new(TransportSettings {
        local: 0,
        peers,
        bind: address,
        cluster_id: cluster_id.to_owned(),
        member_name: "member-0".to_owned(),
        incarnation: 10,
        tls: None,
        rpc_timeout_ms: 2_000,
        conn_read_timeout_ms: read_timeout_ms,
    }));
    let recorder = Arc::new(Recorder::default());
    member
        .start(Arc::clone(&recorder) as Arc<dyn PeerHandler>)
        .await
        .expect("member 0 listens");
    (member, recorder, address)
}

/// An address nothing listens on.
fn nowhere() -> std::net::SocketAddr {
    let probe = std::net::TcpListener::bind(loopback(0)).expect("a port");
    probe.local_addr().expect("an address")
}

/// Connect to `member` as member 1 would, and be admitted.
async fn as_member_one(member: std::net::SocketAddr, cluster_id: &str) -> tokio::net::TcpStream {
    let mut socket = tokio::net::TcpStream::connect(member)
        .await
        .expect("connects");
    let hello = Message::Hello(Hello {
        major: u64::from(PROTOCOL_MAJOR),
        minor: u64::from(PROTOCOL_MINOR),
        cluster_id: cluster_id.to_owned(),
        member_name: "member-1".to_owned(),
        member_index: 1,
        incarnation: 11,
        stream: Stream::Control,
    });
    socket
        .write_all(&frame_for(&hello, Stream::Control, false))
        .await
        .expect("the hello goes");
    let ack = read_frame(&mut socket).await.expect("an acknowledgement");
    match decode_message(ack.message_type, &ack.payload) {
        Ok(Message::HelloAck(ack)) if ack.accepted => socket,
        other => panic!("not admitted: {other:?}"),
    }
}

/// Be member 1 for everything member 0 dials: admit each link, answer its
/// heartbeat pings, and answer each append twice -- with bytes that do not
/// decode, then with the real reply.
async fn answer_undecodably_then_truly(listener: tokio::net::TcpListener) {
    loop {
        let Ok((mut socket, _)) = listener.accept().await else {
            return;
        };
        tokio::spawn(async move {
            let Ok(hello) = read_frame(&mut socket).await else {
                return;
            };
            let Ok(Message::Hello(hello)) = decode_message(hello.message_type, &hello.payload)
            else {
                return;
            };
            let ack = Message::HelloAck(HelloAck {
                accepted: true,
                reason: String::new(),
                minor: u64::from(PROTOCOL_MINOR),
                member_index: 1,
                incarnation: 11,
            });
            if socket
                .write_all(&frame_for(&ack, hello.stream, false))
                .await
                .is_err()
            {
                return;
            }
            while let Ok(frame) = read_frame(&mut socket).await {
                let answers = match decode_message(frame.message_type, &frame.payload) {
                    Ok(Message::Ping(ping)) => vec![frame_for(
                        &Message::Pong(Pong { nonce: ping.nonce }),
                        frame.stream,
                        true,
                    )],
                    Ok(Message::AppendEntries(append)) => vec![
                        undecodable(MessageType::AppendEntriesReply, frame.stream, true),
                        frame_for(
                            &Message::AppendEntriesReply(AppendEntriesReply {
                                term: append.term,
                                success: true,
                                match_index: append.prev_log_index,
                                conflict_index: 0,
                                conflict_term: 0,
                                catching_up: false,
                                request_id: append.request_id,
                            }),
                            frame.stream,
                            true,
                        ),
                    ],
                    _ => Vec::new(),
                };
                for bytes in answers {
                    if socket.write_all(&bytes).await.is_err() {
                        return;
                    }
                }
            }
        });
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn an_undecodable_request_is_not_answered_and_its_connection_stays() {
    let (member, recorder, address) = lone_member("undecodable-request", nowhere()).await;
    let mut socket = as_member_one(address, "undecodable-request").await;

    socket
        .write_all(&undecodable(
            MessageType::AppendEntries,
            Stream::Control,
            false,
        ))
        .await
        .expect("written");
    // Behind it on the same connection, and answered: the connection carried
    // on past it, and the answer is this request's, not the other's.
    socket
        .write_all(&frame_for(&heartbeat(1, 7), Stream::Control, false))
        .await
        .expect("written");
    let answer = tokio::time::timeout(std::time::Duration::from_secs(5), read_frame(&mut socket))
        .await
        .expect("an answer within the deadline")
        .expect("the connection was ended for one undecodable frame");
    match decode_message(answer.message_type, &answer.payload) {
        Ok(Message::AppendEntriesReply(reply)) => assert_eq!(reply.request_id, 7),
        other => panic!("answered with {other:?}"),
    }
    assert_eq!(
        recorder.appends.load(Ordering::SeqCst),
        1,
        "the undecodable frame reached the handler",
    );

    member.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn an_undecodable_reply_leaves_the_link_up() {
    let listener = tokio::net::TcpListener::bind(loopback(0))
        .await
        .expect("a port");
    let fake = listener.local_addr().expect("an address");
    let (member, recorder, _) = lone_member("undecodable-reply", fake).await;
    let peer = tokio::spawn(answer_undecodably_then_truly(listener));

    assert!(
        until(|| member.live() == vec![1]).await,
        "member 0 never linked to member 1",
    );
    let downs = recorder.peer_down.load(Ordering::SeqCst);

    // Fire-and-forget, so the real reply goes to the handler; the undecodable
    // one before it must reach nothing and end nothing.
    member.send(1, &heartbeat(0, 0), Stream::Control);
    assert!(
        until(|| recorder.append_replies.load(Ordering::SeqCst) == 1).await,
        "the real reply behind the undecodable one never reached member 0",
    );
    assert_eq!(
        recorder.peer_down.load(Ordering::SeqCst),
        downs,
        "the link was dropped for one undecodable reply",
    );
    assert_eq!(member.live(), vec![1]);

    member.close().await;
    peer.abort();
}
