// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Elections, replication, and the defects a from-the-paper port reintroduces.
//!
//! Port of the node-level half of `nmos/raft/tests/test_consensus.py`, driven
//! through the in-memory fabric so the interleavings that matter can be
//! arranged rather than waited for.
//!
//! Every test here names the symptom the defect produced, because a test whose
//! failure message says only `assertion failed` leaves the next person to
//! rediscover why the line is written the way it is.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

mod fabric;

use std::sync::Arc;
use std::time::Duration;

use nmos_cluster::{Derivation, MemberSpec, derive_cluster};
use nmos_registry::registry::Registry;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_raft::cluster::{RAFT_FLAVOUR, derive_raft_layout};
use nmos_registry_raft::cursors::CursorAllocator;
use nmos_registry_raft::machine::StateMachine;
use nmos_registry_raft::node::{PROPOSALS_PER_INCARNATION, RaftNode, RaftTiming, Role};
use nmos_registry_raft::operations::{Operation, OperationKind, ProposalId};
use nmos_registry_raft::persist::TermStore;
use nmos_registry_raft::transport::Transport;

use fabric::Fabric;

static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// A directory that removes itself, named with a counter and nothing else.
struct Scratch(std::path::PathBuf);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "nmos-raft-consensus-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed),
        ));
        std::fs::create_dir_all(&path).expect("a scratch directory");
        Self(path)
    }

    fn state(&self, member: u64) -> std::path::PathBuf {
        self.0.join(format!("member-{member}.json"))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        drop(std::fs::remove_dir_all(&self.0));
    }
}

/// Timings compressed so a test runs in milliseconds rather than seconds.
///
/// The *ratio* is what matters and is preserved: the election window stays
/// comfortably above the heartbeat, or a healthy leader's heartbeats would race
/// its followers' timers and the cluster would churn under no load at all.
fn quick() -> RaftTiming {
    RaftTiming {
        heartbeat_ms: 10,
        election_min_ms: 60,
        election_max_ms: 120,
        ..RaftTiming::default()
    }
}

/// A cluster of `size` members on the fabric, none started.
struct Cluster {
    nodes: Vec<Arc<RaftNode>>,
    fabric: Arc<Fabric>,
    _scratch: Scratch,
}

impl Cluster {
    fn build(size: usize, timing: RaftTiming) -> Self {
        let scratch = Scratch::new();
        let fabric = Fabric::new();

        let specs: Vec<MemberSpec> = (0..size)
            .map(|index| MemberSpec {
                host: "127.0.0.1".to_owned(),
                client_port: 2481 + (index as u16) * 2,
                peer_port: 2482 + (index as u16) * 2,
                name: Some(format!("member-{index}")),
                bind_address: None,
            })
            .collect();

        let mut nodes = Vec::new();
        for index in 0..size {
            let layout = derive_cluster(
                &specs,
                &Derivation {
                    local_host: "127.0.0.1",
                    local_peer_port: Some(2482 + (index as u16) * 2),
                    namespace: "/nmos",
                    tls: false,
                    flavour: RAFT_FLAVOUR,
                },
            )
            .expect("a valid cluster");
            let token = layout.token.clone();
            let raft = derive_raft_layout(&layout, token);

            let registry = Arc::new(Registry::new(RegistryStore::new()));
            let machine = StateMachine::new(
                index as u64,
                CursorAllocator::new(index as u64).expect("a lane"),
            );
            nodes.push(RaftNode::new(
                raft,
                fabric.transport(index as u64) as Arc<dyn Transport>,
                TermStore::new(scratch.state(index as u64)),
                machine,
                registry,
                timing,
            ));
        }

        Self {
            nodes,
            fabric,
            _scratch: scratch,
        }
    }

    async fn start_all(&self) {
        for node in &self.nodes {
            node.start().await.expect("starts");
        }
    }

    async fn close_all(&self) {
        for node in &self.nodes {
            node.close().await;
        }
    }

    fn leaders(&self) -> Vec<u64> {
        self.nodes
            .iter()
            .filter(|node| node.role() == Role::Leader)
            .map(|node| node.index())
            .collect()
    }
}

/// Poll for a condition rather than sleeping a fixed time: a fixed sleep is
/// either flaky on a loaded machine or slow on an idle one.
async fn until(mut ready: impl FnMut() -> bool) -> bool {
    for _ in 0..400 {
        if ready() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    false
}

/// A proposable operation.
///
/// **Not** a no-op. `OperationKind::Noop` produces no outcome by design -- the
/// applier returns nothing for it -- so a waiter registered against one can
/// never be resolved and the proposer waits forever. That is correct and
/// unreachable in production: the no-op is the term-establishing entry a new
/// leader appends *directly*, and no caller proposes one. It is a trap for a
/// test, though, and it cost a diagnosis: the entry committed and applied on
/// all three members while the future never completed.
///
/// An ownership claim is the cheapest operation that does produce an outcome
/// and touches no registry state a test would have to set up.
fn claim(member: u64, sequence: u64) -> Operation {
    Operation {
        proposal: ProposalId { member, sequence },
        kind: OperationKind::ClaimOwnership {
            node_id: format!("node-{member}-{sequence}"),
            owner: member,
        },
    }
}

// -- elections --------------------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn a_single_member_cluster_leads_itself() {
    // Quorum of one. A one-member cluster that could not elect itself would be
    // a registry that accepts nothing.
    let cluster = Cluster::build(1, quick());
    cluster.start_all().await;

    assert!(
        until(|| cluster.nodes[0].role() == Role::Leader).await,
        "a lone member never became leader",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn three_members_elect_exactly_one_leader() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;

    assert!(
        until(|| cluster.leaders().len() == 1).await,
        "no single leader emerged: {:?}",
        cluster.leaders(),
    );

    // And it stays one. Two leaders in a term is the failure every other rule
    // here exists to prevent, so it is asserted over an interval rather than at
    // one instant.
    for _ in 0..20 {
        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(
            cluster.leaders().len() <= 1,
            "two members lead at once: {:?}",
            cluster.leaders(),
        );
    }
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_that_cannot_reach_a_quorum_does_not_campaign() {
    // The disruptive-server problem. Without the quorum gate a partitioned
    // member campaigns on every timeout, and arrives after the partition heals
    // carrying a term far above everyone else's -- forcing the healthy leader
    // to step down and causing an election the cluster had no reason to hold.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    let outcast = (0..3u64).find(|&m| m != leader).expect("a follower");
    cluster.fabric.isolate(outcast, &[0, 1, 2]);

    let before = cluster.nodes[outcast as usize].term();
    tokio::time::sleep(Duration::from_millis(400)).await;
    let after = cluster.nodes[outcast as usize].term();

    assert_eq!(
        after, before,
        "an isolated member raised its term from {before} to {after}; when the \
         partition heals it deposes a leader that never stopped being healthy",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_being_served_refuses_to_help_depose_its_leader() {
    // Raft §6's disruption problem, and defect 3.5. The quorum gate stops a
    // *partitioned* member; it does not stop one whose scheduler stalled long
    // enough to miss its heartbeats. Without the lease, that member's campaign
    // is answered and a healthy leader is replaced for nothing.
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    let follower = (0..3u64).find(|&m| m != leader).expect("a follower");
    let disruptor = (0..3u64)
        .find(|&m| m != leader && m != follower)
        .expect("a third");

    // The lease exists only once a heartbeat has actually arrived: until then
    // the follower has heard from nobody and has no leader to vouch for. The
    // leader knowing it leads is not that moment, so the test waits for the
    // follower's own view rather than the leader's.
    assert!(
        until(|| cluster.nodes[follower as usize].leader() == Some(leader)).await,
        "the follower never heard from the leader",
    );

    // Heartbeats are flowing, so the follower's lease is held. A candidate
    // asking for its vote right now must be refused -- and, crucially, without
    // the follower adopting the candidate's term.
    let before = cluster.nodes[follower as usize].term();
    let reply = cluster.nodes[follower as usize].on_request_vote(
        disruptor,
        &RequestVote {
            term: before + 5,
            candidate: disruptor,
            last_log_index: 0,
            last_log_term: 0,
            probe: false,
            amnesiac: Vec::new(),
            pre_vote: false,
        },
    );

    assert!(
        !reply.granted,
        "a follower being served by a healthy leader granted a vote against it",
    );
    assert_eq!(
        cluster.nodes[follower as usize].term(),
        before,
        "the follower adopted the candidate's term, which is itself the \
         disruption: it clears the leader and the vote, and the cluster holds \
         an election it had no reason to",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_pre_vote_changes_nothing_on_the_voter() {
    // The entire contract of a pre-vote. Breaking any part of it would make the
    // round as disruptive as the election it exists to avoid.
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    let voter = (0..3u64).find(|&m| m != leader).expect("a follower");

    // Let the lease lapse, so the refusal below is the pre-vote path rather
    // than the lease path.
    cluster.fabric.isolate_pair(leader, voter);
    tokio::time::sleep(Duration::from_millis(150)).await;

    let term_before = cluster.nodes[voter as usize].term();
    let asker = (0..3u64)
        .find(|&m| m != leader && m != voter)
        .expect("a third");
    let reply = cluster.nodes[voter as usize].on_request_vote(
        asker,
        &RequestVote {
            term: term_before + 1,
            candidate: asker,
            last_log_index: 0,
            last_log_term: 0,
            probe: false,
            amnesiac: Vec::new(),
            pre_vote: true,
        },
    );

    assert!(
        reply.pre_vote,
        "the reply does not say which question it answers"
    );
    assert_eq!(
        cluster.nodes[voter as usize].term(),
        term_before,
        "answering a pre-vote raised the voter's term, which is the term \
         increment the round exists to avoid",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_probe_surveys_the_cluster_without_changing_it() {
    // A member that has forgotten cannot campaign until it knows how many
    // others have too, and cannot learn that without asking. Asking by
    // campaigning would raise the term on every attempt while never succeeding.
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let target = 1u64;
    let before = cluster.nodes[target as usize].term();
    let reply = cluster.nodes[target as usize].on_request_vote(
        2,
        &RequestVote {
            term: before + 9,
            candidate: 2,
            last_log_index: 0,
            last_log_term: 0,
            probe: true,
            amnesiac: Vec::new(),
            pre_vote: false,
        },
    );

    assert!(!reply.granted, "a probe was answered with a vote");
    assert_eq!(
        cluster.nodes[target as usize].term(),
        before,
        "a probe raised the surveyed member's term",
    );
    cluster.close_all().await;
}

// -- replication and commitment ---------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn a_proposal_commits_and_applies_on_every_member() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0] as usize;
    cluster.nodes[leader]
        .propose(claim(leader as u64, 1))
        .await
        .expect("commits");

    assert!(
        until(|| cluster.nodes.iter().all(|node| node.last_applied() >= 1)).await,
        "not every member applied: {:?}",
        cluster
            .nodes
            .iter()
            .map(|n| n.last_applied())
            .collect::<Vec<_>>(),
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_proposal_at_a_follower_is_forwarded_to_the_leader() {
    // A registration does not have to arrive at the leader, and the member that
    // took it is the one that must answer -- so a follower's proposal has to
    // reach the leader and its outcome has to come back.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    let follower = (0..3u64).find(|&m| m != leader).expect("a follower") as usize;

    // The leader knowing it leads is not the same as the follower knowing it:
    // a follower learns from the first `AppendEntries`, and until then it has
    // nobody to forward to. Proposing in that window is a real "no leader
    // elected", not a defect -- so the test waits for the follower's view
    // rather than the leader's.
    assert!(
        until(|| cluster.nodes[follower].leader() == Some(leader)).await,
        "the follower never learned who leads",
    );

    cluster.nodes[follower]
        .propose(claim(follower as u64, 1))
        .await
        .expect("commits");
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_never_applies_past_what_is_committed() {
    // `go.etcd.io/raft` asserts this at the moment it would break, and so does
    // this implementation. The defect that prompted it bounded the apply batch
    // by size and not by the commit index, so a follower whose log ran ahead of
    // the commit index applied entries that might never commit -- and
    // `last_applied` then advanced past indices a later leader overwrote, which
    // no further replication repairs.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0] as usize;
    for sequence in 1..=12u64 {
        cluster.nodes[leader]
            .propose(claim(leader as u64, sequence))
            .await
            .expect("commits");
    }

    for _ in 0..40 {
        for node in &cluster.nodes {
            assert!(
                node.last_applied() <= node.commit_index(),
                "member {} applied through {} but committed only through {}",
                node.index(),
                node.last_applied(),
                node.commit_index(),
            );
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_cluster_survives_losing_a_minority() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    let casualty = (0..3u64).find(|&m| m != leader).expect("a follower");
    cluster.nodes[casualty as usize].close().await;

    cluster.nodes[leader as usize]
        .propose(claim(leader, 99))
        .await
        .expect("a three-member cluster commits with two members");
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_leader_that_loses_its_quorum_stops_leading() {
    // Check-quorum. A leader cut off from a majority cannot commit anything,
    // and the other side has had long enough to elect someone else -- so
    // continuing to answer as leader would mean reporting ready while accepting
    // writes that can never commit.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    cluster.fabric.isolate(leader, &[0, 1, 2]);

    assert!(
        until(|| cluster.nodes[leader as usize].role() != Role::Leader).await,
        "a leader with no quorum kept leading, so it still reports ready while \
         accepting writes that can never commit",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn the_cluster_elects_a_new_leader_after_the_old_one_goes() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let first = cluster.leaders()[0];
    cluster.nodes[first as usize].close().await;

    // Among the *survivors*: a closed node keeps whatever role it last held,
    // because shutting down is not a consensus event and nothing tells it it
    // has been replaced. Counting it would make this assertion unsatisfiable.
    let survivors = || -> Vec<u64> {
        cluster
            .nodes
            .iter()
            .filter(|node| node.index() != first && node.role() == Role::Leader)
            .map(|node| node.index())
            .collect()
    };

    assert!(
        until(|| survivors().len() == 1).await,
        "no replacement leader emerged among the survivors",
    );
    cluster.close_all().await;
}

// -- defect 3.7: proposal ids are seeded from the incarnation ----------------

#[tokio::test(flavor = "multi_thread")]
async fn proposal_ids_do_not_repeat_across_a_restart() {
    // A restarted member's entries outlive it: they are still in the cluster's
    // log and apply after it returns. If the new incarnation mints ids the
    // previous one already used, an old entry's outcome resolves a *new*
    // caller's future -- observed as a registration answered with an
    // unregistration's result.
    let scratch = Scratch::new();
    let path = scratch.state(0);

    let mut first = TermStore::new(path.clone());
    let one = first.load().expect("loads");
    assert_eq!(one.incarnation, 1);

    let mut second = TermStore::new(path);
    let two = second.load().expect("loads");
    assert_eq!(two.incarnation, 2);

    let first_range = one.incarnation * PROPOSALS_PER_INCARNATION;
    let second_range = two.incarnation * PROPOSALS_PER_INCARNATION;
    assert!(
        second_range > first_range,
        "the second incarnation's proposal ids start at {second_range}, not \
         above the first's {first_range}",
    );
    assert!(
        second_range - first_range >= PROPOSALS_PER_INCARNATION,
        "the two incarnations' id ranges overlap, so an entry proposed before \
         the restart resolves a caller that arrived after it",
    );
}

// -- the election restriction ------------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn a_candidate_with_a_shorter_log_is_refused() {
    // Raft §5.4.1. A voter that holds a committed entry refuses a candidate
    // that does not, which is what stops a leader being elected without it.
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0] as usize;
    for sequence in 1..=5u64 {
        cluster.nodes[leader]
            .propose(claim(leader as u64, sequence))
            .await
            .expect("commits");
    }
    assert!(
        until(|| cluster.nodes.iter().all(|n| n.last_applied() >= 5)).await,
        "the entries never replicated",
    );

    let voter = (0..3u64)
        .find(|&m| m as usize != leader)
        .expect("a follower");
    // Let the lease lapse so this exercises the up-to-dateness check and not
    // the lease.
    cluster.fabric.isolate(voter, &[0, 1, 2]);
    tokio::time::sleep(Duration::from_millis(200)).await;

    let term = cluster.nodes[voter as usize].term();
    let reply = cluster.nodes[voter as usize].on_request_vote(
        99,
        &RequestVote {
            term: term + 1,
            candidate: 99,
            // An empty log: this candidate holds none of the committed entries.
            last_log_index: 0,
            last_log_term: 0,
            probe: false,
            amnesiac: Vec::new(),
            pre_vote: true,
        },
    );

    assert!(
        !reply.granted,
        "a voter holding five committed entries granted a pre-vote to a \
         candidate with an empty log -- which is how a leader is elected \
         without a committed entry",
    );
    cluster.close_all().await;
}

// -- the misses the first falsification pass found ---------------------------

#[tokio::test(flavor = "multi_thread")]
async fn an_isolated_member_does_not_even_pre_campaign() {
    // The quorum gate, asserted on what it actually changes.
    //
    // Asserting on the *term* does not work, and finding that out was the
    // point: pre-vote already stops the term rising, because a pre-candidate
    // increments nothing and its peers are unreachable. So removing the gate
    // left the term flat and the earlier test passed. What the gate changes is
    // that the member does not campaign at all -- it stays a follower rather
    // than becoming a pre-candidate and sending into a void every timeout.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    let outcast = (0..3u64).find(|&m| m != leader).expect("a follower");
    cluster.fabric.isolate(outcast, &[0, 1, 2]);

    // Several election windows.
    for _ in 0..40 {
        tokio::time::sleep(Duration::from_millis(10)).await;
        let role = cluster.nodes[outcast as usize].role();
        assert_eq!(
            role,
            Role::Follower,
            "an isolated member became {role:?}; without the quorum gate it \
             campaigns on every timeout into a cluster that cannot hear it",
        );
    }
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_real_vote_applies_the_election_restriction_too() {
    // §5.4.1 on the *real* vote path, not only the pre-vote one. They are
    // separate branches, and the first falsification pass showed it: removing
    // the check from the real path left the pre-vote test passing.
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0] as usize;
    for sequence in 1..=4u64 {
        cluster.nodes[leader]
            .propose(claim(leader as u64, sequence))
            .await
            .expect("commits");
    }
    assert!(
        until(|| cluster.nodes.iter().all(|n| n.last_applied() >= 4)).await,
        "the entries never replicated",
    );

    let voter = (0..3u64)
        .find(|&m| m as usize != leader)
        .expect("a follower");
    // Let the lease lapse, so this is the up-to-dateness check and not the
    // lease refusing.
    cluster.fabric.isolate(voter, &[0, 1, 2]);
    tokio::time::sleep(Duration::from_millis(200)).await;

    let term = cluster.nodes[voter as usize].term();
    let reply = cluster.nodes[voter as usize].on_request_vote(
        99,
        &RequestVote {
            term: term + 1,
            candidate: 99,
            last_log_index: 0,
            last_log_term: 0,
            probe: false,
            amnesiac: Vec::new(),
            // A real vote, which records something durable -- unlike the
            // pre-vote above.
            pre_vote: false,
        },
    );

    assert!(
        !reply.granted,
        "a voter holding committed entries granted a *real* vote to a \
         candidate with an empty log, which is how a leader is elected without \
         a committed entry",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_vouches_only_for_the_window_the_message_covered() {
    // Defect 3.3's other half, at the node. Figure 2 has the leader set
    // `matchIndex = prevLogIndex + entries.length` from what it SENT. Reporting
    // this follower's own last index instead overstates whenever it holds
    // stale uncommitted entries beyond that window -- entries from a previous
    // term that this leader has never seen.
    //
    // The leader stores the number verbatim and counts it toward the quorum, so
    // an overstated match lets it commit an index a majority does not hold.
    // That is Leader Completeness broken, and it was found by chaos at about
    // one run in ten because it needs a heartbeat to arrive while the
    // follower's log runs ahead.
    //
    // Arranged directly here rather than waited for: replicate four entries,
    // give this follower an **uncommitted** tail beyond them, then send a
    // heartbeat whose window ends where the committed part does.
    //
    // The tail has to be uncommitted for the hazard to exist at all. Entries at
    // or below a follower's commit index are on a quorum by definition, so
    // vouching for them overstates nothing -- and a message anchored below the
    // commit index is answered with that commit index by the guard in
    // `on_append_entries` (`go.etcd.io/raft`, `raft.go:1796`), never reaching
    // the arithmetic under test.
    use nmos_registry_raft::messages::{AppendEntries, WireEntry};
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0] as usize;
    for sequence in 1..=4u64 {
        cluster.nodes[leader]
            .propose(claim(leader as u64, sequence))
            .await
            .expect("commits");
    }
    let follower = (0..3u64)
        .find(|&m| m as usize != leader)
        .expect("a follower");
    assert!(
        until(|| cluster.nodes[follower as usize].last_applied() >= 4).await,
        "the follower never caught up",
    );

    let term = cluster.nodes[follower as usize].term();
    // Read the term at index 1 rather than assume it: the cluster may have held
    // several elections before settling, so index 1's term is whatever the
    // first successful leader held.
    // Take this follower off the fabric first. The cluster is live and the
    // runtime is multi-threaded, so a real append can land between any two
    // calls below and move the commit index out from under the scenario --
    // and a message anchored below the commit index is then answered by the
    // guard in `on_append_entries` rather than by the arithmetic under test.
    //
    // The Python harness needs no equivalent because its event loop cannot
    // interleave between two synchronous calls. That is a difference in the
    // harnesses, not in what is being asserted.
    let others: Vec<u64> = (0..3u64).filter(|&m| m != follower).collect();
    cluster.fabric.isolate(follower, &others);
    let settled = cluster.nodes[follower as usize].commit_index();
    let settled_term = cluster.nodes[follower as usize]
        .log_term_at(settled)
        .expect("the commit index is held");

    // The stale uncommitted tail: two entries past everything committed, which
    // a previous term could have left here and this leader may never have seen.
    let tail: Vec<WireEntry> = (1..=2u64)
        .map(|offset| WireEntry {
            term,
            index: settled + offset,
            payload: claim(leader as u64, 90 + offset).encode(),
        })
        .collect();
    let seeded = cluster.nodes[follower as usize].on_append_entries(
        leader as u64,
        &AppendEntries {
            term,
            leader: leader as u64,
            prev_log_index: settled,
            prev_log_term: settled_term,
            leader_commit: settled,
            request_id: 6,
            entries: tail,
        },
    );
    assert!(seeded.success, "the follower refused the uncommitted tail");
    assert!(
        cluster.nodes[follower as usize].last_log_index() > settled,
        "the follower holds no uncommitted tail, so this proves nothing",
    );

    let anchor = cluster.nodes[follower as usize].commit_index();
    let anchor_term = cluster.nodes[follower as usize]
        .log_term_at(anchor)
        .expect("the commit index is held");
    assert!(
        cluster.nodes[follower as usize].last_log_index() > anchor,
        "the follower holds nothing past its commit index, so this proves \
         nothing",
    );
    let reply = cluster.nodes[follower as usize].on_append_entries(
        leader as u64,
        &AppendEntries {
            term,
            leader: leader as u64,
            // A heartbeat covering nothing beyond the committed point, while
            // this follower holds more entries past it.
            prev_log_index: anchor,
            prev_log_term: anchor_term,
            leader_commit: anchor,
            request_id: 7,
            entries: Vec::new(),
        },
    );

    assert!(reply.success, "the consistency check failed unexpectedly");
    assert_eq!(
        reply.match_index, anchor,
        "the follower vouched for index {} when the message covered only \
         index {anchor}; the leader stores that verbatim and counts it toward \
         the quorum, so it can commit an index no majority holds",
        reply.match_index,
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_peer_is_never_promoted_below_its_bar() {
    // The promotion rule itself: a peer's acknowledgements start counting again
    // only once it has reached everything that was committed when the leader
    // noticed it was behind. Promoting earlier puts a member that is still
    // missing committed entries back into the electorate, where it can grant a
    // vote the election restriction exists to refuse.
    //
    // **What this does not cover, stated rather than implied.** The other half
    // of that defect is that `become_leader` must *reset* `promote_through`,
    // because it is only ever assigned on a false-to-true transition of
    // `catching_up` -- so a stale bar means a second leadership never
    // re-decides it. Reproducing that needs a peer that reports catching-up
    // across a leadership change, and this fabric delivers live: a peer's real
    // reply overwrites any injected state within a tick, and forcing the same
    // member to lead twice is timing-dependent. The Python reaches it with a
    // deterministic harness that owns the clock and the delivery order; until
    // there is one here, **removing the reset in `become_leader` is not
    // falsifiable by this suite** and the reasoning in its comment is what
    // carries it.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0] as usize;
    for sequence in 1..=6u64 {
        cluster.nodes[leader]
            .propose(claim(leader as u64, sequence))
            .await
            .expect("commits");
    }

    let peer = (0..3u64).find(|&m| m as usize != leader).expect("a peer");
    for _ in 0..40 {
        if let Some((matched, catching_up, bar)) = cluster.nodes[leader].peer_progress(peer) {
            assert!(
                catching_up || bar == 0 || matched >= bar,
                "member {peer} is promoted (not catching up) at match {matched} \
                 with a bar of {bar}: it is back in the electorate while still \
                 missing committed entries",
            );
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    cluster.close_all().await;
}

// -- what the leader will believe about a peer ------------------------------
//
// From reading `go.etcd.io/raft` beside this implementation rather than from a
// failing test, which is why each carries the number that made the case.

#[tokio::test(flavor = "multi_thread")]
async fn a_peer_is_never_recorded_as_holding_less_than_it_did() {
    // etcd's `MaybeUpdate`: `if n <= pr.Match { return false }`.
    //
    // A follower vouches for the window of the message it is answering, so an
    // entries-less send draws a reply vouching for `prev_log_index` alone --
    // less than a preceding append's reply vouched for. Taking that as news
    // walks the peer backwards and re-sends entries it already holds.
    //
    // Measured before the guard: 418 of 2,744 replies on an idle five-member
    // cluster, 15.2%, every one of them carrying request id 0.
    use nmos_registry_raft::messages::AppendEntriesReply;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0] as usize;
    for sequence in 1..=4u64 {
        cluster.nodes[leader]
            .propose(claim(leader as u64, sequence))
            .await
            .expect("commits");
    }
    let peer = (0..3u64)
        .find(|&m| m as usize != leader)
        .expect("a follower");
    assert!(
        until(|| cluster.nodes[leader]
            .peer_progress(peer)
            .is_some_and(|(matched, _, _)| matched >= 4))
        .await,
        "the leader never saw this peer reach index 4",
    );

    let (before, _, _) = cluster.nodes[leader]
        .peer_progress(peer)
        .expect("the peer is tracked");
    let term = cluster.nodes[leader].term();

    // What an entries-less send anchored well behind draws back.
    cluster.nodes[leader].on_append_entries_reply(
        peer,
        &AppendEntriesReply {
            term,
            success: true,
            match_index: 1,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: 0,
        },
    );

    let (after, _, _) = cluster.nodes[leader]
        .peer_progress(peer)
        .expect("the peer is tracked");
    assert_eq!(
        after, before,
        "member {peer} was recorded as holding only {after} because a \
         heartbeat vouched for that much; it had already acknowledged {before}",
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn an_append_below_the_commit_index_is_answered_with_it() {
    // etcd returns early here (`raft.go:1796`), replying with its own commit
    // index. A delayed or duplicated append anchored below what this member has
    // committed would otherwise be answered with `prev_log_index +
    // len(entries)` -- a match below our commit index, which walks the leader's
    // view of us backwards and makes it re-send what we hold.
    use nmos_registry_raft::messages::AppendEntries;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0] as usize;
    for sequence in 1..=4u64 {
        cluster.nodes[leader]
            .propose(claim(leader as u64, sequence))
            .await
            .expect("commits");
    }
    let follower = (0..3usize)
        .find(|&m| m != leader)
        .expect("a follower");
    assert!(
        until(|| cluster.nodes[follower].commit_index() > 1).await,
        "the follower committed nothing, so this proves nothing",
    );

    let committed = cluster.nodes[follower].commit_index();
    let term = cluster.nodes[follower].term();
    let prev_log_term = cluster.nodes[follower]
        .log_term_at(1)
        .expect("index 1 is held");

    let reply = cluster.nodes[follower].on_append_entries(
        leader as u64,
        &AppendEntries {
            term,
            leader: leader as u64,
            prev_log_index: 1,
            prev_log_term,
            leader_commit: committed,
            request_id: 9,
            entries: Vec::new(),
        },
    );

    assert!(reply.success, "a stale append was rejected outright");
    assert_eq!(
        reply.match_index, committed,
        "answered with {} for a message anchored at index 1, though this \
         member has committed through {committed} -- the leader stores that \
         verbatim",
        reply.match_index,
    );
}
