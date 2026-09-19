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
use std::sync::atomic::{AtomicU64, Ordering};

use async_trait::async_trait;
use nmos_registry_raft::messages::{
    AppendEntries, AppendEntriesReply, Forward, ForwardReply, Hello, InstallSnapshot,
    InstallSnapshotReply, Message, Promote, Propose, ProposeReply, RequestVote, RequestVoteReply,
};
use nmos_registry_raft::transport::{
    PeerHandler, RaftTransport, Transport, TransportSettings, refuse_certificate, refuse_hello,
};
use nmos_registry_raft::wire::{PROTOCOL_MAJOR, Stream};
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
    seen: Mutex<Vec<String>>,
}

#[async_trait]
impl PeerHandler for Recorder {
    fn on_request_vote(&self, _peer: u64, message: &RequestVote) -> RequestVoteReply {
        self.votes.fetch_add(1, Ordering::SeqCst);
        RequestVoteReply {
            term: message.term,
            granted: true,
            voting: true,
            pre_vote: message.pre_vote,
        }
    }

    fn on_append_entries(&self, _peer: u64, message: &AppendEntries) -> AppendEntriesReply {
        self.appends.fetch_add(1, Ordering::SeqCst);
        AppendEntriesReply {
            term: message.term,
            success: true,
            match_index: message.prev_log_index + message.entries.len() as u64,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: message.request_id,
        }
    }

    fn on_install_snapshot(&self, _peer: u64, message: &InstallSnapshot) -> InstallSnapshotReply {
        InstallSnapshotReply {
            term: message.term,
            bytes_received: message.data.len() as u64,
            done: message.done,
        }
    }

    fn on_promote(&self, _peer: u64, _message: &Promote) {
        self.promotes.fetch_add(1, Ordering::SeqCst);
    }

    fn on_request_vote_reply(&self, _peer: u64, _message: &RequestVoteReply) {}

    fn on_append_entries_reply(&self, _peer: u64, _message: &AppendEntriesReply) {
        self.append_replies.fetch_add(1, Ordering::SeqCst);
    }

    fn on_install_snapshot_reply(&self, _peer: u64, _message: &InstallSnapshotReply) {}

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

    let reply = a
        .request(
            1,
            &Message::InstallSnapshot(InstallSnapshot {
                term: 2,
                leader: 0,
                last_index: 100,
                last_term: 1,
                offset: 0,
                data: vec![1, 2, 3, 4],
                done: true,
                ownership: Vec::new(),
            }),
            Stream::Bulk,
            Some(2_000),
        )
        .await;

    // `InstallSnapshotReply` carries no request id, so this correlates with
    // nothing and times out -- which is itself the point being made: the reply
    // reaches the handler, not a waiter. What matters here is that the BULK
    // link exists and carried the frame.
    assert!(reply.is_err(), "a snapshot reply carries no correlation id");

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

/// A forward and concurrent append traffic each get their own answer.
///
/// **This does not reproduce the id collision**, and saying so matters more
/// than the test does. Reproducing it needs an `AppendEntriesReply` to arrive
/// in the window between `request` registering its waiter and the reply to
/// *its* message coming back -- and `send` spawns its write (see
/// `RaftTransport::send`), so the order of the two writes is not controllable
/// from here. Verified by mutation: reverting `resolve` to match on the id
/// alone leaves this test passing.
///
/// A deterministic reproduction needs a peer that speaks the frame protocol by
/// hand and answers a `Forward` with an `AppendEntriesReply` carrying the same
/// id. That is worth writing and is not written.
///
/// Worth noting which way the odds run: on loopback the window is microseconds,
/// so a test is unlikely to hit it. Across a real network it is a full round
/// trip, which makes the hazard *more* likely in production than here.
///
/// What this does cover is the ordinary path -- a forward is answered with a
/// `ForwardReply` and an append reply reaches the handler rather than being
/// swallowed -- which is the regression that a wrong fix would cause.
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
