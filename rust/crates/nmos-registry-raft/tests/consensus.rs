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
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_raft::cluster::{RAFT_FLAVOUR, RaftLayout, derive_raft_layout};
use nmos_registry_raft::cursors::CursorAllocator;
use nmos_registry_raft::machine::StateMachine;
use nmos_registry_raft::node::{PROPOSALS_PER_INCARNATION, RaftNode, RaftTiming, Role};
use nmos_registry_raft::operations::{Operation, OperationKind, ProposalId};
use nmos_registry_raft::ownership::OwnershipTable;
use nmos_registry_raft::persist::{PersistentStateError, TermStore};
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
        Self::build_with(size, timing, &[])
    }

    /// As [`Self::build`], with the `restarted` members on their second
    /// incarnation: each loads a term store that has been loaded once before,
    /// so it starts non-voting -- exactly what a restart leaves.
    fn build_with(size: usize, timing: RaftTiming, restarted: &[u64]) -> Self {
        Self::build_from(size, timing, restarted, &[])
    }

    /// As [`Self::build_with`], with the `broken` members' state machines
    /// already applied through [`BEYOND`]: on the first apply after the first
    /// commit each finds itself applied past its commit index, which Raft
    /// makes impossible.
    fn build_from(size: usize, timing: RaftTiming, restarted: &[u64], broken: &[u64]) -> Self {
        let scratch = Scratch::new();
        let fabric = Fabric::new();

        let mut nodes = Vec::new();
        for index in 0..size {
            let raft = layout_of(size, index);

            let registry = Arc::new(Registry::new(RegistryStore::new()));
            let mut machine = StateMachine::new(
                index as u64,
                CursorAllocator::new(index as u64).expect("a lane"),
            );
            if broken.contains(&(index as u64)) {
                machine.install_snapshot(
                    &registry,
                    RegistryStore::new(),
                    OwnershipTable::new(),
                    BEYOND,
                );
            }
            let path = scratch.state(index as u64);
            if restarted.contains(&(index as u64)) {
                let first = TermStore::new(path.clone())
                    .load()
                    .expect("a first incarnation");
                assert_eq!(first.incarnation, 1, "the store had been loaded before");
            }
            nodes.push(
                RaftNode::new(
                    raft,
                    fabric.transport(index as u64) as Arc<dyn Transport>,
                    TermStore::new(path),
                    machine,
                    registry,
                    timing,
                )
                .expect("a term file only this member has written"),
            );
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

/// Member `index`'s layout in a cluster of `size`, as every member derives it.
fn layout_of(size: usize, index: usize) -> RaftLayout {
    let specs: Vec<MemberSpec> = (0..size)
        .map(|member| MemberSpec {
            host: "127.0.0.1".to_owned(),
            client_port: 2481 + (member as u16) * 2,
            peer_port: 2482 + (member as u16) * 2,
            name: Some(format!("member-{member}")),
            bind_address: None,
        })
        .collect();
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
    derive_raft_layout(&layout, token)
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
    let reply = cluster.nodes[follower as usize]
        .on_request_vote(
            disruptor,
            &RequestVote {
                term: before + 5,
                candidate: disruptor,
                last_log_index: 0,
                last_log_term: 0,
                pre_vote: false,
            },
        )
        .expect("the term and vote were saved");

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
    let reply = cluster.nodes[voter as usize]
        .on_request_vote(
            asker,
            &RequestVote {
                term: term_before + 1,
                candidate: asker,
                last_log_index: 0,
                last_log_term: 0,
                pre_vote: true,
            },
        )
        .expect("the term and vote were saved");

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

// -- evidence that a member has forgotten -----------------------------------
//
// A member counts as forgotten for the election of term T only on its own reply
// to that candidacy, saying `voting = false` at term T -- binding, because it
// has adopted T and only a leader of T or later could promote it. Observations
// carried between rounds went stale and elected a leader that lacked a
// committed entry (E1: seeds 59925, 110797, 111540). See "When every voter has
// forgotten" in `node.rs`.
//
// The candidate-side tests run one real member against peers that answer as
// each test scripts: what a candidate counts is the question, so the replies are
// the input.

type Answer = Box<
    dyn Fn(
            &nmos_registry_raft::messages::RequestVote,
        ) -> nmos_registry_raft::messages::RequestVoteReply
        + Send
        + Sync,
>;

/// A peer that answers vote requests as scripted, and everything else blandly.
struct Scripted {
    pre_vote: Answer,
    vote: Answer,
    /// What its append replies say of it: `true` for a member catching up.
    catching_up: bool,
}

impl Scripted {
    /// Grants every question, saying it `voting`.
    fn granting(voting: bool) -> Arc<Self> {
        Arc::new(Self {
            pre_vote: Box::new(move |request| grant(request, voting)),
            vote: Box::new(move |request| grant(request, voting)),
            catching_up: false,
        })
    }

    /// A member that has restarted and not been promoted: it grants, saying it
    /// has forgotten, and answers every append as one catching up.
    fn restarted() -> Arc<Self> {
        Arc::new(Self {
            pre_vote: Box::new(|request| grant(request, false)),
            vote: Box::new(|request| grant(request, false)),
            catching_up: true,
        })
    }
}

/// A grant, carrying the term asked about.
fn grant(
    request: &nmos_registry_raft::messages::RequestVote,
    voting: bool,
) -> nmos_registry_raft::messages::RequestVoteReply {
    nmos_registry_raft::messages::RequestVoteReply {
        term: request.term,
        granted: true,
        voting,
        pre_vote: request.pre_vote,
    }
}

/// A refusal carrying `term` -- the voter's own, which for a refused pre-vote is
/// one below the prospective term asked about.
fn refuse(
    request: &nmos_registry_raft::messages::RequestVote,
    term: u64,
    voting: bool,
) -> nmos_registry_raft::messages::RequestVoteReply {
    nmos_registry_raft::messages::RequestVoteReply {
        term,
        granted: false,
        voting,
        pre_vote: request.pre_vote,
    }
}

#[async_trait::async_trait]
impl nmos_registry_raft::transport::PeerHandler for Scripted {
    fn on_request_vote(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::RequestVote,
    ) -> Result<nmos_registry_raft::messages::RequestVoteReply, PersistentStateError> {
        Ok(if message.pre_vote {
            (self.pre_vote)(message)
        } else {
            (self.vote)(message)
        })
    }

    fn on_append_entries(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::AppendEntries,
    ) -> Result<nmos_registry_raft::messages::AppendEntriesReply, PersistentStateError> {
        Ok(nmos_registry_raft::messages::AppendEntriesReply {
            term: message.term,
            success: false,
            match_index: 0,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: self.catching_up,
            request_id: message.request_id,
        })
    }

    fn on_install_snapshot(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::InstallSnapshot,
    ) -> Result<nmos_registry_raft::messages::InstallSnapshotReply, PersistentStateError> {
        Ok(nmos_registry_raft::messages::InstallSnapshotReply {
            term: message.term,
            bytes_received: 0,
            done: false,
            commit_index: 0,
            request_id: message.request_id,
        })
    }

    fn on_promote(&self, _peer: u64, _message: &nmos_registry_raft::messages::Promote) {}

    fn on_request_vote_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::RequestVoteReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    fn on_append_entries_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::AppendEntriesReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    fn on_install_snapshot_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::InstallSnapshotReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    async fn on_propose(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::Propose,
    ) -> nmos_registry_raft::messages::ProposeReply {
        nmos_registry_raft::messages::ProposeReply {
            accepted: false,
            reason: "a scripted peer".to_owned(),
            term: 0,
            first_index: 0,
            request_id: message.request_id,
            leader: None,
        }
    }

    async fn on_forward(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::Forward,
    ) -> nmos_registry_raft::messages::ForwardReply {
        nmos_registry_raft::messages::ForwardReply {
            ok: false,
            created: false,
            error: "unavailable".to_owned(),
            detail: "a scripted peer".to_owned(),
            applied_index: 0,
            not_owner: false,
            request_id: message.request_id,
            owner: None,
        }
    }

    async fn on_read_index(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::ReadIndex,
    ) -> nmos_registry_raft::messages::ReadIndexReply {
        nmos_registry_raft::messages::ReadIndexReply {
            ok: false,
            index: 0,
            reason: "a scripted peer".to_owned(),
            request_id: message.request_id,
        }
    }

    fn on_peer_state(&self, _peer: u64, _up: bool, _incarnation: u64) {}
}

/// Member 0 real, every other member scripted; started, and past enough election
/// windows that it has stood for election more than once. Returns the term it
/// started at alongside.
///
/// The scripts are held for the cluster's life: the fabric keeps only a weak
/// reference to each handler.
async fn scripted(
    size: usize,
    restarted: bool,
    peers: Vec<Arc<Scripted>>,
) -> (Cluster, Vec<Arc<Scripted>>, u64) {
    let cluster = Cluster::build_with(size, quick(), if restarted { &[0] } else { &[] });
    for (offset, peer) in peers.iter().enumerate() {
        cluster
            .fabric
            .transport(offset as u64 + 1)
            .start(Arc::clone(peer) as Arc<dyn nmos_registry_raft::transport::PeerHandler>)
            .await
            .expect("the fabric starts unconditionally");
    }
    cluster.nodes[0].start().await.expect("starts");
    let started_at = cluster.nodes[0].term();
    assert_eq!(
        cluster.nodes[0].voting(),
        !restarted,
        "the real member is not in the state the test assumes",
    );
    tokio::time::sleep(Duration::from_millis(12 * quick().election_max_ms)).await;
    (cluster, peers, started_at)
}

#[tokio::test(flavor = "multi_thread")]
async fn a_non_voting_member_votes_as_one_that_has_forgotten() {
    // It grants on log currency, says it cannot vote, and votes once per term.
    // Its grant counts only where the candidate's own round proves that no
    // quorum of voters can exist. It used to refuse unless *it* judged the
    // cluster had forgotten, from observations of other members' past state --
    // which went stale (E1).
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build_with(3, quick(), &[1]);
    cluster.start_all().await;
    let member = Arc::clone(&cluster.nodes[1]);
    assert!(
        !member.voting(),
        "the restarted member is voting, so this proves nothing"
    );
    // Out of reach, and past its lease, so what answers below is the vote rule
    // and not the lease.
    cluster.fabric.isolate(1, &[0, 2]);
    tokio::time::sleep(Duration::from_millis(2 * quick().election_max_ms)).await;

    // Exactly as current as the member, however much it caught up first: the
    // grant then turns on the vote rule alone.
    let (last, last_term) = (
        member.last_log_index(),
        member.log_term_at(member.last_log_index()).unwrap_or(0),
    );
    let term = member.term() + 5;
    let ask = |candidate: u64| RequestVote {
        term,
        candidate,
        last_log_index: last,
        last_log_term: last_term,
        pre_vote: false,
    };
    let reply = member
        .on_request_vote(2, &ask(2))
        .expect("the term and vote were saved");
    assert!(!reply.voting);
    assert_eq!(reply.term, term, "the reply is not binding for the round");
    assert!(
        reply.granted,
        "a non-voting member refused a candidate its log does not outrank; a \
         whole-cluster restart could never gather a vote quorum",
    );
    assert!(
        !member
            .on_request_vote(0, &ask(0))
            .expect("the term and vote were saved")
            .granted,
        "it voted twice in one term",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_non_voting_grant_does_not_count_toward_an_ordinary_election() {
    // Member 1 grants everything and says it cannot vote; member 2 votes and
    // refuses. One voter's grant -- the candidate's own -- is not a quorum, and
    // nothing in any round proves a quorum of voters impossible.
    let refusing = Arc::new(Scripted {
        pre_vote: Box::new(|request| refuse(request, request.term - 1, true)),
        vote: Box::new(|request| refuse(request, request.term, true)),
        catching_up: false,
    });
    let (cluster, _held, started_at) =
        scripted(3, false, vec![Scripted::granting(false), refusing]).await;

    assert_ne!(
        cluster.nodes[0].role(),
        Role::Leader,
        "elected on its own vote and a grant from a member that said it cannot \
         vote",
    );
    assert_eq!(
        cluster.nodes[0].term(),
        started_at,
        "the pre-vote predicted a recovery the election would refuse, and raised \
         the term for it -- against a cluster that might have had a leader",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_reply_from_an_earlier_term_proves_nothing() {
    // Member 1 votes in the pre-vote and, restarted by the real round, grants
    // saying it cannot vote. Member 2 refuses the real round the way a member
    // under a leader's lease does: at its own, lower term. That lower-term
    // "cannot vote" is no proof of anything -- a leader exists -- so it must not
    // turn the round into a recovery election.
    let restarted_between = Arc::new(Scripted {
        pre_vote: Box::new(|request| grant(request, true)),
        vote: Box::new(|request| grant(request, false)),
        catching_up: false,
    });
    let under_a_lease = Arc::new(Scripted {
        pre_vote: Box::new(|request| refuse(request, request.term - 1, true)),
        vote: Box::new(|request| refuse(request, request.term - 1, false)),
        catching_up: false,
    });
    let (cluster, _held, started_at) =
        scripted(3, false, vec![restarted_between, under_a_lease]).await;

    assert!(
        cluster.nodes[0].term() > started_at,
        "the member never reached a real round, so this proves nothing",
    );
    assert_ne!(
        cluster.nodes[0].role(),
        Role::Leader,
        "an earlier term's refusal was taken as proof that its sender had \
         forgotten, and made a recovery election of an ordinary one",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_round_that_proves_the_cluster_has_forgotten_elects() {
    // The recovery the clause exists for, on binding evidence alone: a
    // non-voting candidate whose every peer answers, in its term, that it
    // cannot vote either. No quorum of voters can exist, a quorum granted, and
    // every member not proven forgotten -- none -- granted.
    let (cluster, _held, _) = scripted(
        3,
        true,
        vec![Scripted::granting(false), Scripted::granting(false)],
    )
    .await;

    assert_eq!(
        cluster.nodes[0].role(),
        Role::Leader,
        "a round proving that every member has forgotten elected no one; a \
         whole-cluster restart would never recover",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_recovery_election_is_refused_by_a_member_that_remembers() {
    // Five members, three proven forgotten, and a committed entry surviving on
    // one of the two that remember. The candidate lacks it: the member holding
    // it refuses, and since its approval is required, the candidate cannot win.
    let remembers_more = Arc::new(Scripted {
        pre_vote: Box::new(|request| refuse(request, request.term - 1, true)),
        vote: Box::new(|request| refuse(request, request.term, true)),
        catching_up: false,
    });
    let (cluster, _held, _) = scripted(
        5,
        false,
        vec![
            Scripted::granting(false),
            Scripted::granting(false),
            Scripted::granting(false),
            remembers_more,
        ],
    )
    .await;

    assert_ne!(
        cluster.nodes[0].role(),
        Role::Leader,
        "a recovery election was won without the member that still remembers -- \
         the only one whose up-to-dateness check still protects anything",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_recovery_election_is_won_with_every_member_that_remembers() {
    // The same five, with the member that remembers granting: the candidate is
    // as current as it, so it is the candidate the clause is there to elect.
    let (cluster, _held, _) = scripted(
        5,
        false,
        vec![
            Scripted::granting(false),
            Scripted::granting(false),
            Scripted::granting(false),
            Scripted::granting(true),
        ],
    )
    .await;

    assert_eq!(
        cluster.nodes[0].role(),
        Role::Leader,
        "every member that remembers granted and three were proven forgotten, \
         yet no one was elected",
    );
    cluster.close_all().await;
}

// -- a snapshot already held, and correlated chunks ---------------------------
//
// S2: a follower refused a snapshot at or below its commit index with the answer
// a discarded transfer gets, so the leader started again from zero -- a
// ping-pong at network speed. It now answers with its commit index, as etcd
// does (`raft.go:1840-1854`), and the leader stops.
//
// S9: chunks travel on BULK while a reconnect is reported for CONTROL, so a
// reconnect reset a transfer whose last chunk was still in flight and still
// answered, and that answer started a second stream beside the first (seed
// 60195). Chunks now carry a correlation id fenced by `reply_floor`, and an
// unanswered one is sent again once overdue -- the backstop a chunk lost with its
// BULK connection needed (S6).

/// What a member that needs a snapshot was sent, in order.
#[derive(Default)]
struct Holder {
    /// `(offset, length, request_id)` of every chunk.
    chunks: parking_lot::Mutex<Vec<(u64, u64, u64)>>,
    /// Every chunk whole, for a test to hand to a real member.
    messages: parking_lot::Mutex<Vec<nmos_registry_raft::messages::InstallSnapshot>>,
    /// `(prev_log_index, request_id)` of every append.
    appends: parking_lot::Mutex<Vec<(u64, u64)>>,
}

#[async_trait::async_trait]
impl nmos_registry_raft::transport::PeerHandler for Holder {
    fn on_request_vote(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::RequestVote,
    ) -> Result<nmos_registry_raft::messages::RequestVoteReply, PersistentStateError> {
        Ok(refuse(message, message.term, true))
    }

    fn on_append_entries(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::AppendEntries,
    ) -> Result<nmos_registry_raft::messages::AppendEntriesReply, PersistentStateError> {
        self.appends
            .lock()
            .push((message.prev_log_index, message.request_id));
        Ok(nmos_registry_raft::messages::AppendEntriesReply {
            term: message.term,
            success: false,
            match_index: 0,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: message.request_id,
        })
    }

    fn on_install_snapshot(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::InstallSnapshot,
    ) -> Result<nmos_registry_raft::messages::InstallSnapshotReply, PersistentStateError> {
        self.chunks.lock().push((
            message.offset,
            message.data.len() as u64,
            message.request_id,
        ));
        self.messages.lock().push(message.clone());
        Ok(nmos_registry_raft::messages::InstallSnapshotReply {
            term: message.term,
            bytes_received: 0,
            done: false,
            commit_index: 0,
            request_id: message.request_id,
        })
    }

    fn on_promote(&self, _peer: u64, _message: &nmos_registry_raft::messages::Promote) {}

    fn on_request_vote_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::RequestVoteReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    fn on_append_entries_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::AppendEntriesReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    fn on_install_snapshot_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::InstallSnapshotReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    async fn on_propose(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::Propose,
    ) -> nmos_registry_raft::messages::ProposeReply {
        nmos_registry_raft::messages::ProposeReply {
            accepted: false,
            reason: "a member that needs a snapshot".to_owned(),
            term: 0,
            first_index: 0,
            request_id: message.request_id,
            leader: None,
        }
    }

    async fn on_forward(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::Forward,
    ) -> nmos_registry_raft::messages::ForwardReply {
        nmos_registry_raft::messages::ForwardReply {
            ok: false,
            created: false,
            error: "unavailable".to_owned(),
            detail: "a member that needs a snapshot".to_owned(),
            applied_index: 0,
            not_owner: false,
            request_id: message.request_id,
            owner: None,
        }
    }

    async fn on_read_index(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::ReadIndex,
    ) -> nmos_registry_raft::messages::ReadIndexReply {
        nmos_registry_raft::messages::ReadIndexReply {
            ok: false,
            index: 0,
            reason: "a member that needs a snapshot".to_owned(),
            request_id: message.request_id,
        }
    }

    fn on_peer_state(&self, _peer: u64, _up: bool, _incarnation: u64) {}
}

/// A leader transferring a snapshot of several chunks to member 2, a [`Holder`]
/// whose own answers the fabric drops: the replies a test is about, it hands
/// over itself. On the paused clock, so the overdue backstop fires only when a
/// test lets time pass.
async fn mid_transfer() -> (Cluster, Arc<RaftNode>, u64, Arc<Holder>) {
    let timing = RaftTiming {
        compaction_threshold: 4,
        max_log_entries: 8,
        snapshot_chunk: 16,
        ..quick()
    };
    let cluster = Cluster::build(3, timing);
    let holder = Arc::new(Holder::default());
    cluster
        .fabric
        .transport(2)
        .start(Arc::clone(&holder) as Arc<dyn nmos_registry_raft::transport::PeerHandler>)
        .await
        .expect("the fabric starts unconditionally");
    cluster.fabric.cut(2, 0);
    cluster.fabric.cut(2, 1);
    for index in 0..2 {
        cluster.nodes[index].start().await.expect("starts");
    }
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    // Only until the first chunk goes out: every proposal lets the clock run,
    // and a chunk left unanswered past `election_min` is -- rightly -- sent
    // again under a new id, which would leave a test answering one no longer
    // in flight.
    for sequence in 1..=64u64 {
        if !holder.chunks.lock().is_empty() {
            break;
        }
        node.propose(claim(leader, sequence))
            .await
            .expect("commits");
    }
    assert!(
        until(|| !holder.chunks.lock().is_empty()).await,
        "the leader never sent the member a snapshot",
    );
    let (_, bytes) = node.snapshot_held().expect("a snapshot is held");
    assert!(
        bytes as u64 > 3 * timing.snapshot_chunk as u64,
        "a snapshot of so few chunks proves nothing about a transfer",
    );
    (cluster, node, 2, holder)
}

/// The chunk in flight: the latest sent.
fn in_flight(holder: &Holder) -> (u64, u64, u64) {
    *holder.chunks.lock().last().expect("a chunk was sent")
}

/// An answer from member 2 to the chunk `request_id`.
fn chunk_reply(
    node: &RaftNode,
    received: u64,
    request_id: u64,
) -> nmos_registry_raft::messages::InstallSnapshotReply {
    nmos_registry_raft::messages::InstallSnapshotReply {
        term: node.term(),
        bytes_received: received,
        done: false,
        commit_index: 0,
        request_id,
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_answers_a_snapshot_it_already_holds_with_its_commit_index() {
    use nmos_registry_raft::messages::InstallSnapshot;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    for sequence in 1..=3u64 {
        node.propose(claim(leader, sequence))
            .await
            .expect("commits");
    }
    let follower = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let member = Arc::clone(&cluster.nodes[follower as usize]);
    assert!(
        until(|| member.commit_index() >= 3).await,
        "the follower never caught up"
    );
    // Off the fabric first: the cluster is live and the runtime is
    // multi-threaded, so a real append landing between reading the commit index
    // and the call below moves the number the assertion compares against --
    // measured as a reply of 4 against a commit of 3 read a moment before. See
    // `a_follower_vouches_only_for_the_window_the_message_covered`.
    let others: Vec<u64> = (0..3u64).filter(|&m| m != follower).collect();
    cluster.fabric.isolate(follower, &others);
    // And whatever was already past the cut, landed: it moves the commit index
    // under the comparison as surely (`Fabric::settled`).
    cluster.fabric.settled(follower, &others).await;
    let commit = member.commit_index();

    let reply = member
        .on_install_snapshot(
            leader,
            &InstallSnapshot {
                term: member.term(),
                leader,
                last_index: commit,
                last_term: member.log_term_at(commit).unwrap_or(0),
                offset: 0,
                data: b"the first chunk of a snapshot this member holds".to_vec(),
                done: false,
                ownership: Vec::new(),
                request_id: 7,
            },
        )
        .expect("the term and vote were saved");

    assert_eq!(
        reply.commit_index, commit,
        "a follower committed through {commit} did not say so; the leader can only start the \
         transfer again",
    );
    assert_eq!(reply.request_id, 7);
    assert!(reply.bytes_received == 0 && !reply.done);
    assert_eq!(
        member.snapshot_buffers(),
        0,
        "it began assembling a snapshot it holds"
    );
    cluster.close_all().await;
}

#[tokio::test(start_paused = true)]
async fn a_snapshot_the_follower_already_holds_ends_the_transfer() {
    use nmos_registry_raft::transport::PeerHandler;

    let (cluster, node, peer, holder) = mid_transfer().await;
    let (_, _, request_id) = in_flight(&holder);
    let sent = holder.chunks.lock().len();
    // Within this leader's log, which bounds what a follower can report, and
    // past its snapshot, so crediting the follower's statement is told apart
    // from crediting the pin.
    let held = node.last_log_index();
    let (through, _) = node.snapshot_held().expect("a snapshot is held");
    assert!(held > through, "the leader holds nothing past its snapshot");

    node.on_install_snapshot_reply(
        peer,
        &nmos_registry_raft::messages::InstallSnapshotReply {
            term: node.term(),
            bytes_received: 0,
            done: false,
            commit_index: held,
            request_id,
        },
    )
    .expect("the term and vote were saved");

    let (matched, _, _) = node.peer_progress(peer).expect("the peer is tracked");
    assert_eq!(
        matched, held,
        "the follower's own statement of what it holds was not credited"
    );
    assert!(
        until(|| holder.appends.lock().last().map(|&(prev, _)| prev) == Some(held)).await,
        "the leader did not return to replication from what the follower holds",
    );
    assert_eq!(
        holder.chunks.lock().len(),
        sent,
        "the leader started the transfer again for a follower that already holds everything \
         it covers",
    );
    cluster.close_all().await;
}

#[tokio::test(start_paused = true)]
async fn a_reconnect_does_not_fork_the_transfer() {
    use nmos_registry_raft::transport::PeerHandler;

    let (cluster, node, peer, holder) = mid_transfer().await;
    let sent = |holder: &Holder| holder.chunks.lock().len();
    let (zero_offset, zero_len, zero_id) = in_flight(&holder);
    assert_eq!(zero_offset, 0);
    let before = sent(&holder);
    node.on_install_snapshot_reply(peer, &chunk_reply(&node, zero_len, zero_id))
        .expect("the term and vote were saved");
    assert!(until(|| sent(&holder) > before).await, "no second chunk");
    let (one_offset, one_len, one_id) = in_flight(&holder);
    assert_eq!(one_offset, zero_len);

    // CONTROL reconnects; the chunk on BULK is still in flight. The leader's
    // view of the peer starts over, and it probes with an append -- which the
    // member answers as one holding nothing would, as a real one would.
    let before = sent(&holder);
    let appends = holder.appends.lock().len();
    node.on_peer_state(peer, true, 1);
    assert!(
        until(|| holder.appends.lock().len() > appends).await,
        "the leader did not probe the reconnected member",
    );
    let (_, probe) = *holder.appends.lock().last().expect("an append");
    node.on_append_entries_reply(
        peer,
        &nmos_registry_raft::messages::AppendEntriesReply {
            term: node.term(),
            success: false,
            match_index: 0,
            conflict_index: 1,
            conflict_term: 0,
            catching_up: false,
            request_id: probe,
        },
    )
    .expect("the term and vote were saved");
    assert!(
        until(|| sent(&holder) > before).await,
        "the transfer never started again"
    );
    let (restart_offset, restart_len, restart_id) = in_flight(&holder);
    assert_eq!(restart_offset, 0);

    // The old chunk's reply arrives, true about its own stream.
    let before = sent(&holder);
    node.on_install_snapshot_reply(peer, &chunk_reply(&node, one_offset + one_len, one_id))
        .expect("the term and vote were saved");
    tokio::time::sleep(Duration::from_millis(2 * quick().heartbeat_ms)).await;
    assert_eq!(
        sent(&holder),
        before,
        "a reply to a chunk sent before the reconnect drove the transfer: a second stream \
         now runs beside the first",
    );

    node.on_install_snapshot_reply(peer, &chunk_reply(&node, restart_len, restart_id))
        .expect("the term and vote were saved");
    assert!(
        until(|| sent(&holder) > before).await,
        "the live stream did not continue"
    );
    assert_eq!(in_flight(&holder).0, restart_len);
    cluster.close_all().await;
}

#[tokio::test(start_paused = true)]
async fn an_unanswered_chunk_is_sent_again_once_overdue() {
    use nmos_registry_raft::transport::PeerHandler;

    let (cluster, node, peer, holder) = mid_transfer().await;
    let (offset, len, first) = in_flight(&holder);
    let before = holder.chunks.lock().len();
    // Its BULK connection dropped with it, and nothing will answer.
    assert!(
        until(|| holder.chunks.lock().len() > before).await,
        "a chunk lost in flight was never sent again: the transfer stalls for as long as \
         the CONTROL connection stays up",
    );
    let (again_offset, _, again) = in_flight(&holder);
    assert_eq!(again_offset, offset);
    assert_ne!(again, first, "sent again under the same id");

    // Should the first copy's reply turn up after all, it is fenced.
    let before = holder.chunks.lock().len();
    node.on_install_snapshot_reply(peer, &chunk_reply(&node, len, first))
        .expect("the term and vote were saved");
    tokio::time::sleep(Duration::from_millis(2 * quick().heartbeat_ms)).await;
    assert_eq!(
        holder.chunks.lock().len(),
        before,
        "the reply to a copy already sent again drove the transfer",
    );
    cluster.close_all().await;
}

// A chunk the leader sends again must not cost the transfer. The overdue
// backstop makes a chunk's delivery at-least-once: a chunk that is slow rather
// than lost arrives, and so does the copy sent after it. The follower threw its
// whole buffer away on the copy -- its offset no longer matched what it had
// assembled -- and answered zero, which is the answer to the chunk in flight, so
// the leader started the transfer again from nothing. Measured in 16 runs of
// chaos-soak seed 140692: 343 of the 362 follower resets were copies of a chunk
// already held, and 350 of the 486 restarts followed one.
//
// Member 2 of `mid_transfer` is a `Holder`; the real member it stands in for,
// never started, is handed the leader's chunks here.

/// Chunk `number` of `payload`, as the transfer that began with `first` sends it.
fn chunk_of(
    first: &nmos_registry_raft::messages::InstallSnapshot,
    payload: &[u8],
    size: usize,
    number: usize,
    request_id: u64,
) -> nmos_registry_raft::messages::InstallSnapshot {
    let offset = number * size;
    let end = (offset + size).min(payload.len());
    nmos_registry_raft::messages::InstallSnapshot {
        offset: offset as u64,
        data: payload[offset..end].to_vec(),
        done: end == payload.len(),
        request_id,
        ..first.clone()
    }
}

/// The first chunk of `mid_transfer`'s transfer, and the snapshot it is of.
fn first_chunk(
    node: &RaftNode,
    holder: &Holder,
) -> (nmos_registry_raft::messages::InstallSnapshot, Arc<[u8]>) {
    let first = holder.messages.lock()[0].clone();
    let payload = node.snapshot_payload();
    assert_eq!(first.offset, 0);
    assert_eq!(
        first.data.as_slice(),
        &payload[..first.data.len()],
        "the leader compacted after the transfer began: its snapshot is not the one sent",
    );
    (first, payload)
}

#[tokio::test(start_paused = true)]
async fn a_copy_of_a_chunk_already_held_is_answered_not_thrown_away() {
    use nmos_registry_raft::transport::PeerHandler;

    let (cluster, node, peer, holder) = mid_transfer().await;
    let leader = cluster.leaders()[0];
    let member = Arc::clone(&cluster.nodes[peer as usize]);
    let (first, payload) = first_chunk(&node, &holder);
    let size = first.data.len();

    let mut reply = member
        .on_install_snapshot(leader, &chunk_of(&first, &payload, size, 0, 1))
        .expect("the term and vote were saved");
    for number in 1..3 {
        reply = member
            .on_install_snapshot(
                leader,
                &chunk_of(&first, &payload, size, number, number as u64 + 1),
            )
            .expect("the term and vote were saved");
    }
    let held = reply.bytes_received;
    assert_eq!(held, 3 * size as u64);

    // The third chunk once more, as the leader sends it when the first copy's
    // answer is overdue: the first copy arrived, and so does this.
    let again = member
        .on_install_snapshot(leader, &chunk_of(&first, &payload, size, 2, 99))
        .expect("the term and vote were saved");
    assert_eq!(
        again.bytes_received, held,
        "a copy of a chunk this member already holds was answered with {}: it threw away \
         {held} bytes of the transfer, and the leader starts again from zero",
        again.bytes_received,
    );
    assert_eq!(again.request_id, 99);
    assert!(!again.done);

    let mut number = 3;
    while !reply.done {
        reply = member
            .on_install_snapshot(
                leader,
                &chunk_of(&first, &payload, size, number, 100 + number as u64),
            )
            .expect("the term and vote were saved");
        assert!(reply.bytes_received > 0, "chunk {number} was refused");
        number += 1;
    }
    assert!(
        member.commit_index() >= first.last_index,
        "the snapshot never installed"
    );
    cluster.close_all().await;
}

#[tokio::test(start_paused = true)]
async fn a_chunk_that_disagrees_with_what_is_held_still_restarts_the_transfer() {
    // The boundary of the rule above: a copy is recognised by its bytes, not by
    // its offset alone. Different bytes at an offset already passed are no copy,
    // and keeping the buffer would be the splice that "restart rather than
    // splice" exists to prevent.
    use nmos_registry_raft::transport::PeerHandler;

    let (cluster, node, peer, holder) = mid_transfer().await;
    let leader = cluster.leaders()[0];
    let member = Arc::clone(&cluster.nodes[peer as usize]);
    let (first, payload) = first_chunk(&node, &holder);
    let size = first.data.len();
    for number in 0..3 {
        member
            .on_install_snapshot(
                leader,
                &chunk_of(&first, &payload, size, number, number as u64 + 1),
            )
            .expect("the term and vote were saved");
    }
    let mut other = chunk_of(&first, &payload, size, 2, 99);
    for byte in &mut other.data {
        *byte ^= 0xFF;
    }

    let reply = member
        .on_install_snapshot(leader, &other)
        .expect("the term and vote were saved");

    assert!(
        reply.bytes_received == 0 && !reply.done,
        "bytes that disagree with those held at offset {} were accepted as a copy ({} \
         acknowledged)",
        other.offset,
        reply.bytes_received,
    );
    assert_eq!(member.snapshot_buffers(), 0, "the buffer was kept");
    cluster.close_all().await;
}

#[tokio::test(start_paused = true)]
async fn a_chunk_sent_again_while_its_first_copy_is_slow_does_not_restart_the_transfer() {
    // End to end: the leader's own backstop, the member's own answers, in the
    // order one connection delivers them.
    use nmos_registry_raft::transport::PeerHandler;

    let (cluster, node, peer, holder) = mid_transfer().await;
    let leader = cluster.leaders()[0];
    let member = Arc::clone(&cluster.nodes[peer as usize]);
    let (first, payload) = first_chunk(&node, &holder);
    let latest = || {
        holder
            .messages
            .lock()
            .last()
            .cloned()
            .expect("a chunk was sent")
    };
    let sent = || holder.messages.lock().len();

    // The chunk in flight, answered by the member, and the answer handed back;
    // the leader's next chunk is delivered asynchronously, so wait for it.
    for _ in 0..2 {
        let before = sent();
        let reply = member
            .on_install_snapshot(leader, &latest())
            .expect("the term and vote were saved");
        node.on_install_snapshot_reply(peer, &reply)
            .expect("the term and vote were saved");
        assert!(
            until(|| sent() > before).await,
            "the transfer did not go on"
        );
    }
    let slow = latest();
    // Its answer is overdue -- the chunk is slow, not lost -- so the leader
    // sends it again.
    let before = sent();
    assert!(
        until(|| sent() > before).await,
        "an overdue chunk was never sent again"
    );
    let again = latest();
    assert_eq!(again.offset, slow.offset);
    assert_ne!(again.request_id, slow.request_id);

    let first_answer = member
        .on_install_snapshot(leader, &slow)
        .expect("the term and vote were saved");
    let second_answer = member
        .on_install_snapshot(leader, &again)
        .expect("the term and vote were saved");
    node.on_install_snapshot_reply(peer, &first_answer)
        .expect("the term and vote were saved");
    node.on_install_snapshot_reply(peer, &second_answer)
        .expect("the term and vote were saved");
    assert_eq!(
        second_answer.bytes_received, first_answer.bytes_received,
        "the copy was answered {} after the first was answered {}: the member threw the \
         transfer away, and the leader starts it again from zero",
        second_answer.bytes_received, first_answer.bytes_received,
    );

    // Bounded by the snapshot's size: no transfer has more chunks than bytes.
    for _ in 0..payload.len() {
        let before = sent();
        assert!(
            until(|| sent() > before).await,
            "the transfer did not go on"
        );
        let reply = member
            .on_install_snapshot(leader, &latest())
            .expect("the term and vote were saved");
        node.on_install_snapshot_reply(peer, &reply)
            .expect("the term and vote were saved");
        if reply.done {
            break;
        }
    }
    let starts = holder
        .messages
        .lock()
        .iter()
        .filter(|chunk| chunk.offset == 0)
        .count();
    assert_eq!(starts, 1, "the transfer started {starts} times");
    assert!(
        member.commit_index() >= first.last_index,
        "the snapshot never installed"
    );
    cluster.close_all().await;
}

// -- a waiter does not outlive its caller -------------------------------------
//
// A waiter was removed only when its entry applied (or on relinquish, or at
// close). A forwarded proposal that never becomes an entry this member applies
// -- its `Propose` never delivered, refused by a member that was no longer
// leader, or accepted and then overwritten -- kept its waiter for the life of
// the member. Measured by the chaos soak (40 runs, 1,046 leaked waiters): 85%
// never delivered, 11% refused, 4% accepted and never applied here, every one
// forwarded, and in every run its client had already given up. etcd removes the
// waiter when the client's context ends (`v3_server.go:1117`, `:1129`).

#[tokio::test(flavor = "multi_thread")]
async fn a_forwarded_proposal_that_never_arrives_leaves_no_waiter() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let member = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let node = Arc::clone(&cluster.nodes[member as usize]);
    // Known, or the proposal is refused at once ("no leader elected") and no
    // waiter is ever registered: a leader is elected before every follower
    // has heard from it.
    assert!(
        until(|| node.leader() == Some(leader)).await,
        "member {member} never learned its leader"
    );

    // Its way to the leader is cut; the leader's heartbeats still arrive, so
    // it keeps forwarding to it -- into nothing.
    cluster.fabric.cut(member, leader);
    let answer =
        tokio::time::timeout(Duration::from_millis(200), node.propose(claim(member, 1))).await;
    assert!(answer.is_err(), "the proposal was answered: {answer:?}");
    cluster.fabric.heal();
    tokio::time::sleep(Duration::from_millis(20 * quick().heartbeat_ms)).await;

    assert_eq!(
        node.pending_waiters(),
        0,
        "its caller gave up, and member {member} still holds a waiter for it -- for as long \
         as it lives",
    );
    cluster.close_all().await;
}

/// A leader that stops leading fails none of its callers. etcd releases nothing
/// when a leader steps down: a proposal already appended waits for its entry --
/// which a later leader may commit -- or for its caller. This implementation
/// failed every registered waiter on relinquish; the Python one never did.
#[tokio::test(flavor = "multi_thread")]
async fn a_leader_that_loses_its_quorum_does_not_fail_its_callers() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    let others: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();

    cluster.fabric.isolate(leader, &others);
    let proposing = Arc::clone(&node);
    let caller = tokio::spawn(async move { proposing.propose(claim(leader, 1)).await });
    assert!(
        until(|| node.role() != Role::Leader).await,
        "the cut-off leader never stood down"
    );
    tokio::time::sleep(Duration::from_millis(5 * quick().heartbeat_ms)).await;
    assert!(
        !caller.is_finished(),
        "standing down answered its caller, about an entry a later leader may still commit",
    );

    // The caller gives up, and its waiter goes with it.
    caller.abort();
    assert!(
        until(|| node.pending_waiters() == 0).await,
        "the abandoned waiter stayed"
    );
    cluster.close_all().await;
}

// -- reading ----------------------------------------------------------------
//
// etcd's ReadIndex, quorum-confirmed (`ReadOnlySafe`): the index a read must
// have applied before it may answer from its own store -- the commit index when
// the read began, released once a quorum has confirmed, after that moment, that
// the leader giving it still leads. Each test is one condition etcd's raft
// imposes on it (`raft.go:1354-1368`, `:1600-1609`, `:1764-1770`,
// `:2146-2156`); `nmos/raft/tests/test_consensus.py` holds the same set.

/// The three members of a started cluster: its leader, then the two others --
/// once both have heard from it.
async fn led(cluster: &Cluster) -> (u64, u64, u64) {
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let others: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();
    assert!(
        until(|| others
            .iter()
            .all(|&member| cluster.nodes[member as usize].leader() == Some(leader)))
        .await,
        "a follower never learned its leader"
    );
    (leader, others[0], others[1])
}

#[tokio::test(flavor = "multi_thread")]
async fn a_leader_confirms_a_read_with_a_quorum() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    let (leader, _, _) = led(&cluster).await;
    let node = &cluster.nodes[leader as usize];
    node.propose(claim(leader, 1)).await.expect("commits");
    let committed = node.commit_index();

    let index = node.read_index(1_000).await.expect("a read index");
    assert!(
        index >= committed,
        "a read begun after index {committed} committed was given index {index}"
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_leader_cut_off_from_its_quorum_confirms_no_read() {
    // Its commit index may already be stale: a majority can have elected a
    // leader and committed without it, and it cannot tell.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    let (leader, first, second) = led(&cluster).await;
    let node = &cluster.nodes[leader as usize];
    node.propose(claim(leader, 1)).await.expect("commits");
    cluster.fabric.isolate(leader, &[first, second]);

    let answer = node.read_index(1_000).await;
    assert!(
        answer.is_err(),
        "a leader nobody could hear confirmed a read: {answer:?}"
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_new_leader_confirms_no_read_below_what_it_inherited() {
    // Nothing is read until an entry of its own term commits. A new leader
    // holds every committed entry but may not know an earlier leader committed
    // it (Raft §8), so its commit index can sit below what a client has been
    // told is done; etcd postpones reads until then
    // (`committedEntryInCurrentTerm`, `raft.go:1363-1367`). Built exactly: an
    // old leader commits E with one follower and is cut off before telling
    // it; that follower wins the next term knowing E only as uncommitted, and
    // is asked for a read before its first entry commits.
    use nmos_registry_raft::messages::Message;
    use std::sync::atomic::{AtomicBool, Ordering};

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    let (old, heir, other) = led(&cluster).await;
    let e_index = cluster.nodes[old as usize].last_log_index() + 1;

    // `other` hears nothing more from the old leader, and the heir nothing
    // after acknowledging E: cut the instant its acknowledgement arrives, so
    // the commit the old leader sends in answer is lost. And, once asked, hold
    // back the acknowledgements that would commit the heir's own first entry.
    cluster.fabric.cut(old, other);
    let holding = Arc::new(AtomicBool::new(false));
    let hold = Arc::clone(&holding);
    let fabric = Arc::downgrade(&cluster.fabric);
    cluster.fabric.intercept(move |from, to, message| {
        if let Message::AppendEntriesReply(ref reply) = *message {
            if from == heir
                && to == old
                && reply.match_index >= e_index
                && let Some(fabric) = fabric.upgrade()
            {
                fabric.cut(old, heir);
            }
            if from == other && to == heir && hold.load(Ordering::SeqCst) {
                return false;
            }
        }
        true
    });
    cluster.nodes[old as usize]
        .propose(claim(old, 1))
        .await
        .expect("E commits");
    assert!(cluster.nodes[old as usize].commit_index() >= e_index);
    assert!(
        cluster.nodes[heir as usize].commit_index() < e_index,
        "the heir learned that E committed; the scenario needs it not to"
    );

    holding.store(true, Ordering::SeqCst);
    cluster.fabric.isolate(old, &[heir, other]);
    let node = Arc::clone(&cluster.nodes[heir as usize]);
    assert!(
        until(|| node.role() == Role::Leader).await,
        "the heir never won"
    );
    assert!(node.commit_index() < e_index);

    let reading = Arc::clone(&node);
    let read = tokio::spawn(async move { reading.read_index(2_000).await });
    assert!(
        until(|| node.pending_reads() == 1).await,
        "the read was never recorded"
    );
    tokio::time::sleep(Duration::from_millis(2 * quick().heartbeat_ms)).await;
    holding.store(false, Ordering::SeqCst);

    let index = read.await.expect("joins").expect("a read index");
    assert!(
        index >= e_index,
        "a read on the new leader was given index {index}, below E at {e_index} -- committed \
         and acknowledged before the read began",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_is_given_its_leaders_read_index() {
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    let (leader, follower, _) = led(&cluster).await;
    cluster.nodes[leader as usize]
        .propose(claim(leader, 1))
        .await
        .expect("commits");
    let committed = cluster.nodes[leader as usize].commit_index();

    let index = cluster.nodes[follower as usize]
        .read_index(1_000)
        .await
        .expect("a read index");
    assert!(
        index >= committed,
        "a read begun at a follower after index {committed} committed was given index {index}"
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_with_no_leader_is_told_at_once() {
    // etcd drops the request with no leader (`raft.go:1764-1768`); a caller
    // here is told at once rather than left to its deadline. One member of
    // three, started alone: it can never elect anyone. (A member cut off after
    // hearing from its leader keeps naming it -- it cannot campaign without a
    // quorum -- and is refused by its link instead.)
    let cluster = Cluster::build(3, quick());
    let alone = &cluster.nodes[0];
    alone.start().await.expect("starts");
    tokio::time::sleep(Duration::from_millis(5 * quick().heartbeat_ms)).await;
    assert_eq!(alone.leader(), None);

    let started = tokio::time::Instant::now();
    let answer = alone.read_index(5_000).await;
    assert!(
        answer
            .as_ref()
            .is_err_and(|error| error.0.contains("no leader")),
        "{answer:?}"
    );
    assert!(started.elapsed() < Duration::from_secs(1));
    alone.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_lone_voter_reads_at_its_commit_index() {
    // Answered at once, as etcd answers one (`raft.go:1355-1361`).
    let cluster = Cluster::build(1, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let node = &cluster.nodes[0];
    node.propose(claim(0, 1)).await.expect("commits");

    assert_eq!(
        node.read_index(1_000).await.expect("a read index"),
        node.commit_index()
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_reply_that_says_catching_up_confirms_no_read() {
    // Only a voter's answer confirms a read, judged by the reply itself. etcd
    // counts voters' acknowledgements alone (`raft.go:1604-1605`), as this
    // leader counts only voters toward a commit and check-quorum. It judged a
    // reply by the flag the leader held *before* reading it, so the first
    // reply of a member that had restarted with nothing -- the one that says it
    // is catching up -- was counted as a voter's.
    use nmos_registry_raft::messages::AppendEntriesReply;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    let (leader, restarted, gone) = led(&cluster).await;
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    node.propose(claim(leader, 1)).await.expect("commits");

    // Nobody else can answer: one member is gone, and the other's own replies
    // are lost -- the leader goes on sending it heartbeats -- so the reply
    // below is its first.
    cluster.fabric.isolate(gone, &[leader, restarted]);
    cluster.fabric.cut(restarted, leader);
    let reading = Arc::clone(&node);
    let read = tokio::spawn(async move { reading.read_index(1_000).await });
    assert!(
        until(|| node.pending_reads() == 1).await,
        "the read was never recorded"
    );

    node.on_append_entries_reply(
        restarted,
        &AppendEntriesReply {
            term: node.term(),
            success: true,
            match_index: 0,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: true,
            // Above anything the read was recorded after.
            request_id: u64::MAX,
        },
    )
    .expect("the term and vote were saved");
    tokio::time::sleep(Duration::from_millis(2 * quick().heartbeat_ms)).await;
    if read.is_finished() {
        let answer = read.await.expect("joins");
        assert!(
            answer.is_err(),
            "a member that said it was catching up confirmed a read: {answer:?}"
        );
    } else {
        read.abort();
    }
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

// -- paging cursors are unique across a restart --------------------------------
//
// The cursor counterpart of the proposal ids above, and found the same way: the
// chaos soak reported two Nodes holding one cursor, five times in about 12,000
// runs, every pair minted by consecutive incarnations of one member, each time
// while that machine's wall clock had stepped back. Owner bits keep members
// apart but not incarnations; once the log is ahead of the clock an allocation
// depends only on the log prefix applied, and a restarted member that replays
// the same prefix mints the same cursor. Port of `TestCursorsAcrossRestarts`.

/// A cursor the log holds, a minute past this member's clock.
///
/// What a member is left with when its wall clock steps back, or while a peer's
/// clock runs fast: every cursor it has applied is in its future, so each
/// allocation is pushed above the log rather than read from the clock.
fn ahead_of_the_clock() -> TaiCursor {
    TaiCursor::new(TaiCursor::now().seconds + 60, 0)
}

/// One incarnation of member `index` of a `size`-member cluster, over the state
/// file `scratch` keeps for it, having replayed a log whose newest cursor is
/// `ahead`.
///
/// The allocator lives inside the node, so the replay is played into the
/// machine before the node is built: the same high-water mark a restarted
/// member reaches by applying the log it rejoins.
fn incarnation(
    scratch: &Scratch,
    fabric: &Arc<Fabric>,
    size: usize,
    index: u64,
    ahead: TaiCursor,
) -> Arc<RaftNode> {
    let mut machine = StateMachine::new(index, CursorAllocator::new(index).expect("a lane"));
    machine.cursors_mut().observe(ResourceType::Node, ahead);
    RaftNode::new(
        layout_of(size, index as usize),
        fabric.transport(index) as Arc<dyn Transport>,
        TermStore::new(scratch.state(index)),
        machine,
        Arc::new(Registry::new(RegistryStore::new())),
        quick(),
    )
    .expect("a term file only this member has written")
}

#[tokio::test(flavor = "multi_thread")]
async fn a_cursor_handed_out_before_a_restart_is_never_handed_out_again() {
    let scratch = Scratch::new();
    let fabric = Fabric::new();
    let ahead = ahead_of_the_clock();

    let first = incarnation(&scratch, &fabric, 3, 1, ahead);
    let before = first.allocate_cursor(ResourceType::Node).expect("reserved");
    drop(first);

    // The new incarnation replays the log it rejoins -- the same prefix, so the
    // same high-water mark -- before it allocates.
    let second = incarnation(&scratch, &fabric, 3, 1, ahead);
    assert_eq!(second.incarnation(), 2);
    let after = second
        .allocate_cursor(ResourceType::Node)
        .expect("reserved");
    assert!(
        after > before,
        "incarnation 2 handed out {after}, and incarnation 1 had already handed \
         out {before}: two resources can now share a paging cursor",
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_term_change_carries_the_reservation_forward() {
    // A save for the vote rewrites the whole file, reservation included. Every
    // save replaces the file, so a term change that wrote only the term and
    // vote would erase the bound the last allocation recorded, and the next
    // incarnation would resume below cursors already handed out.
    let scratch = Scratch::new();
    let fabric = Fabric::new();
    let ahead = ahead_of_the_clock();
    let nodes: Vec<Arc<RaftNode>> = (0..3)
        .map(|index| incarnation(&scratch, &fabric, 3, index, ahead))
        .collect();
    for node in &nodes {
        node.start().await.expect("starts");
    }
    let leader_of = |nodes: &[Arc<RaftNode>]| {
        nodes
            .iter()
            .find(|node| node.role() == Role::Leader)
            .map(|node| node.index())
    };
    assert!(until(|| leader_of(&nodes).is_some()).await, "no leader");
    let leader = leader_of(&nodes).expect("a leader");
    let member = (0..3).find(|&index| index != leader).expect("a follower");

    let before = nodes[member as usize]
        .allocate_cursor(ResourceType::Node)
        .expect("reserved");
    let term = nodes[member as usize].term();

    // A new election: the member adopts a later term and saves it.
    nodes[leader as usize].close().await;
    assert!(
        until(|| nodes[member as usize].term() > term).await,
        "no term change reached the member",
    );
    for node in &nodes {
        node.close().await;
    }

    let replacement = incarnation(&scratch, &fabric, 3, member, ahead);
    let after = replacement
        .allocate_cursor(ResourceType::Node)
        .expect("reserved");
    assert!(
        after > before,
        "after a term change, the next incarnation handed out {after}, not \
         above {before}: the vote's save erased the reservation",
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_reservation_that_cannot_be_written_hands_out_nothing() {
    // The cursor stays inside the member. Returning it anyway would reopen the
    // defect exactly when the disk is already misbehaving; refusing makes the
    // registration a retryable 503.
    //
    // The write is made to fail by taking the state directory away, which
    // fails for every user -- a read-only directory would not stop root.
    let scratch = Scratch::new();
    let fabric = Fabric::new();
    let node = incarnation(&scratch, &fabric, 1, 0, ahead_of_the_clock());

    std::fs::remove_dir_all(&scratch.0).expect("removed");
    let refused = node
        .allocate_cursor(ResourceType::Node)
        .expect_err("no reservation, no cursor");
    assert!(
        refused.0.contains("could not reserve paging cursors"),
        "refused, but not as a reservation: {}",
        refused.0,
    );

    std::fs::create_dir_all(&scratch.0).expect("restored");
    let cursor = node
        .allocate_cursor(ResourceType::Node)
        .expect("reserved once the disk is back");
    let stored: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(scratch.state(0)).expect("written"))
            .expect("json");
    let reservation = stored["cursor_reservation"]
        .as_str()
        .and_then(TaiCursor::parse)
        .expect("a reservation on disk");
    assert!(
        reservation >= cursor,
        "{reservation} does not cover {cursor}"
    );
}

// -- an unusable term file refuses to start ------------------------------------
//
// The term file is what makes a vote durable, so a member that cannot read it
// cannot know whether it has already voted, and must not start. This one did:
// it logged the refusal and carried on as a brand-new member -- term 0, no
// vote, incarnation 1 -- and, measured, then granted a second vote in a term
// its file had recorded a vote in, and overwrote the file with the second.
// The Python's constructor raises the refusal. Port of
// `TestAnUnusableTermFileRefusesToStart`.

/// A term file recording a vote for member 0 in term 7, in two unusable forms:
/// written by a build with a newer state version, which is what a rollback
/// leaves behind, and torn mid-write.
const UNUSABLE: [(&str, &str); 2] = [
    (
        "a newer state version",
        "{\n  \"version\": 2,\n  \"term\": 7,\n  \"voted_for\": 0,\n  \
         \"incarnation\": 3,\n  \"cursor_reservation\": null\n}",
    ),
    (
        "a torn file",
        "{\n  \"version\": 1,\n  \"term\": 7,\n  \"voted_fo",
    ),
];

/// Member 1 of 3, over the term file at `path`.
fn member_over(
    path: std::path::PathBuf,
    fabric: &Arc<Fabric>,
) -> Result<Arc<RaftNode>, PersistentStateError> {
    RaftNode::new(
        layout_of(3, 1),
        fabric.transport(1) as Arc<dyn Transport>,
        TermStore::new(path),
        StateMachine::new(1, CursorAllocator::new(1).expect("a lane")),
        Arc::new(Registry::new(RegistryStore::new())),
        quick(),
    )
}

/// Whether `node` grants member 2 its vote in `term`.
///
/// A vote that could not be saved is not granted: nothing is answered.
fn grants_member_two(node: &RaftNode, term: u64) -> bool {
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    node.on_request_vote(
        2,
        &RequestVote {
            term,
            candidate: 2,
            last_log_index: 0,
            last_log_term: 0,
            pre_vote: false,
        },
    )
    .is_ok_and(|reply| reply.granted)
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_whose_term_file_is_unusable_refuses_to_start() {
    for (form, text) in UNUSABLE {
        let scratch = Scratch::new();
        let fabric = Fabric::new();
        let path = scratch.state(1);
        std::fs::write(&path, text).expect("written");

        let refusal = match member_over(path.clone(), &fabric) {
            Err(refusal) => refusal,
            Ok(node) => {
                let (term, incarnation) = (node.term(), node.incarnation());
                let granted = grants_member_two(&node, 7);
                panic!(
                    "member 1 started over {form}, as term {term} incarnation \
                     {incarnation}, and {} member 2 a vote in term 7 -- the \
                     term its file had recorded a vote for member 0 in",
                    if granted { "granted" } else { "refused" },
                );
            }
        };
        // The store's own refusal, word for word: it names the file and says
        // what to do about it.
        let expected = TermStore::new(path.clone())
            .load()
            .expect_err("the file is unusable");
        assert_eq!(refusal, expected, "{form}");
        // Left as found: it is the only record of the vote.
        assert_eq!(
            std::fs::read_to_string(&path).expect("still there"),
            text,
            "{form}"
        );
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_that_cannot_write_its_first_term_file_refuses_to_start() {
    // A member with no file is new and starts from nothing -- once that
    // nothing is on disk. Started without it, every vote it granted would be
    // one a restart forgets. The directory is taken away rather than made
    // read-only, which would not stop root.
    let scratch = Scratch::new();
    let fabric = Fabric::new();
    std::fs::remove_dir_all(&scratch.0).expect("removed");

    let refusal = match member_over(scratch.state(1), &fabric) {
        Err(refusal) => refusal,
        Ok(node) => panic!(
            "member 1 started with nowhere to record a vote{}",
            if grants_member_two(&node, 1) {
                ", and granted member 2 one in term 1 that a restart would forget"
            } else {
                ""
            },
        ),
    };
    assert!(
        refusal.0.contains("could not create a temporary file"),
        "refused, but not for the write: {}",
        refusal.0,
    );
}

// -- a decision whose save failed is not sent --------------------------------
//
// A term or vote that could not be saved must not leave the member: granted
// and then forgotten at a restart, it is the double vote the term file exists
// to prevent. This member logged the failed save and carried on -- measured,
// with every save failing it granted a vote, answered a newer term's append and
// snapshot, and, alone, made itself leader. The Python's save raises out of
// whatever made the decision, so nothing that rests on it is sent; here each
// handler returns the failure, and the transport answers nothing and ends the
// connection (`transport.rs`). Port of `TestADecisionWhoseSaveFailedIsNotSent`.

/// Make every later save of member `member` fail.
///
/// Its term file becomes a directory, so the rename that publishes a save
/// fails -- for root too, which a read-only directory would not stop.
fn break_saves(scratch: &Scratch, member: u64) {
    let path = scratch.state(member);
    std::fs::remove_file(&path).expect("the file construction wrote");
    std::fs::create_dir_all(path.join("in-the-way")).expect("a directory where the file was");
}

/// Member `candidate` asking for a vote in `term`, with a log nothing is ahead
/// of.
fn vote_for(
    node: &RaftNode,
    candidate: u64,
    term: u64,
) -> Result<nmos_registry_raft::messages::RequestVoteReply, PersistentStateError> {
    use nmos_registry_raft::messages::RequestVote;
    use nmos_registry_raft::transport::PeerHandler;

    node.on_request_vote(
        candidate,
        &RequestVote {
            term,
            candidate,
            last_log_index: 0,
            last_log_term: 0,
            pre_vote: false,
        },
    )
}

/// A heartbeat from member 0 as leader of `term`.
fn append_in(
    node: &RaftNode,
    term: u64,
) -> Result<nmos_registry_raft::messages::AppendEntriesReply, PersistentStateError> {
    use nmos_registry_raft::messages::AppendEntries;
    use nmos_registry_raft::transport::PeerHandler;

    node.on_append_entries(
        0,
        &AppendEntries {
            term,
            leader: 0,
            prev_log_index: 0,
            prev_log_term: 0,
            leader_commit: 0,
            request_id: 0,
            entries: Vec::new(),
        },
    )
}

/// A first snapshot chunk from member 0 as leader of `term`.
fn snapshot_in(
    node: &RaftNode,
    term: u64,
) -> Result<nmos_registry_raft::messages::InstallSnapshotReply, PersistentStateError> {
    use nmos_registry_raft::messages::InstallSnapshot;
    use nmos_registry_raft::transport::PeerHandler;

    node.on_install_snapshot(
        0,
        &InstallSnapshot {
            term,
            leader: 0,
            last_index: 0,
            last_term: 0,
            offset: 0,
            data: Vec::new(),
            done: false,
            ownership: Vec::new(),
            request_id: 0,
        },
    )
}

/// Member 0 refusing this member its vote -- or, with `pre_vote`, its pre-vote
/// -- in a reply that carries `term`.
fn refused_in(node: &RaftNode, term: u64, pre_vote: bool) -> Result<(), PersistentStateError> {
    use nmos_registry_raft::messages::RequestVoteReply;
    use nmos_registry_raft::transport::PeerHandler;

    node.on_request_vote_reply(
        0,
        &RequestVoteReply {
            term,
            granted: false,
            voting: true,
            pre_vote,
        },
    )
}

/// An append reply from member 0 that carries `term`.
fn append_reply_in(node: &RaftNode, term: u64) -> Result<(), PersistentStateError> {
    use nmos_registry_raft::messages::AppendEntriesReply;
    use nmos_registry_raft::transport::PeerHandler;

    node.on_append_entries_reply(
        0,
        &AppendEntriesReply {
            term,
            success: false,
            match_index: 0,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: 0,
        },
    )
}

/// A snapshot reply from member 0 that carries `term`.
fn snapshot_reply_in(node: &RaftNode, term: u64) -> Result<(), PersistentStateError> {
    use nmos_registry_raft::messages::InstallSnapshotReply;
    use nmos_registry_raft::transport::PeerHandler;

    node.on_install_snapshot_reply(
        0,
        &InstallSnapshotReply {
            term,
            bytes_received: 0,
            done: false,
            commit_index: 0,
            request_id: 0,
        },
    )
}

#[tokio::test(flavor = "multi_thread")]
async fn a_vote_that_cannot_be_saved_is_not_granted() {
    let cluster = Cluster::build(3, quick());
    let node = &cluster.nodes[1];
    // Into term 1 while saves still work, by a refusal that carries it: the
    // member steps up with no leader, so no lease stands in the way, and the
    // vote below is the only thing left to save.
    refused_in(node, 1, false).expect("saved while saves still work");
    break_saves(&cluster._scratch, 1);

    if let Ok(reply) = vote_for(node, 2, 1) {
        panic!(
            "member 1 answered member 2's request for its vote in term 1 \
             (granted={}) with nothing saved",
            reply.granted,
        );
    }
    // Kept in memory, as the Python keeps it: while it runs, this member gives
    // nobody else its vote in term 1.
    let other = vote_for(node, 0, 1).expect("a refusal saves nothing");
    assert!(
        !other.granted,
        "member 1 granted member 0 its vote in term 1, having given it to member 2",
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_newer_term_that_cannot_be_saved_is_not_answered() {
    let cluster = Cluster::build(3, quick());
    let node = &cluster.nodes[1];
    break_saves(&cluster._scratch, 1);

    let answered: Vec<&str> = [
        ("RequestVote", vote_for(node, 2, 1).is_ok()),
        ("AppendEntries", append_in(node, 2).is_ok()),
        ("InstallSnapshot", snapshot_in(node, 3).is_ok()),
    ]
    .into_iter()
    .filter_map(|(kind, answered)| answered.then_some(kind))
    .collect();
    assert!(
        answered.is_empty(),
        "member 1 answered {answered:?} in terms it could not save",
    );
    // Adopted in memory, as in the Python, so nothing older is taken for
    // current while the member runs.
    assert_eq!(node.term(), 3);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_reply_whose_newer_term_cannot_be_saved_ends_there() {
    let cluster = Cluster::build(3, quick());
    let node = &cluster.nodes[1];
    break_saves(&cluster._scratch, 1);

    let went_on: Vec<&str> = [
        ("RequestVoteReply", refused_in(node, 1, false).is_ok()),
        (
            "RequestVoteReply (pre-vote)",
            refused_in(node, 2, true).is_ok(),
        ),
        ("AppendEntriesReply", append_reply_in(node, 3).is_ok()),
        ("InstallSnapshotReply", snapshot_reply_in(node, 4).is_ok()),
    ]
    .into_iter()
    .filter_map(|(kind, went_on)| went_on.then_some(kind))
    .collect();
    assert!(
        went_on.is_empty(),
        "member 1 went on from {went_on:?} in terms it could not save",
    );
    assert_eq!(node.term(), 4);
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_that_cannot_save_its_vote_for_itself_never_leads() {
    // Alone, it wins every election it holds -- once its vote for itself is on
    // disk. The Python's `_campaign` raises before counting that vote, and each
    // election timeout tries again with the next term, in memory only.
    let lone = Cluster::build(1, quick());
    break_saves(&lone._scratch, 0);
    lone.start_all().await;

    let node = &lone.nodes[0];
    let mut led = false;
    let tried = until(|| {
        led |= node.role() == Role::Leader;
        node.term() >= 5
    })
    .await;
    let term = node.term();
    lone.close_all().await;
    assert!(
        !led,
        "member 0 led on a vote for itself it never saved (now term {term})",
    );
    assert!(tried, "member 0 stopped campaigning at term {term}");
}

// -- a majority catching up ---------------------------------------------------
//
// Two of three members restart together: the survivor, the only member holding
// the log, must lead them back to voting. Found on Windows and measured in the
// Python: the survivor won, then stood down a tick later ("lost contact with a
// quorum") although both peers were answering, because check-quorum left out
// members catching up while the quorum stayed two of three. Here the campaign
// check counted them, so the survivor won again and again, a tick at a time --
// and each new term threw away the snapshot the last had begun. A member
// catching up that answers proves the leader is not cut off, so check-quorum
// counts it, and so does the campaign check; commits, read confirmations and
// votes still leave it out. Port of `TestAMajorityCatchingUp`.

/// `quick()`, compacting early and sending snapshots in small chunks: a member
/// that restarts then needs a snapshot of many chunks, one round trip each, to
/// catch up -- far longer than the tick check-quorum runs on.
fn catching_up_timing() -> RaftTiming {
    RaftTiming {
        compaction_threshold: 16,
        snapshot_chunk: 16,
        ..quick()
    }
}

/// Poll `ready` for up to `seconds`: `until`'s two are too few for a catch-up
/// of a few thousand round trips on a loaded machine.
async fn within(seconds: u64, mut ready: impl FnMut() -> bool) -> bool {
    let deadline = std::time::Instant::now() + Duration::from_secs(seconds);
    while std::time::Instant::now() < deadline {
        if ready() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
    ready()
}

/// The one leader, read once: counting leaders and then indexing a second
/// reading races a leader standing down in between.
async fn the_leader(cluster: &Cluster) -> Option<usize> {
    let mut found = None;
    within(10, || {
        found = match cluster.leaders()[..] {
            [leader] => Some(leader as usize),
            _ => None,
        };
        found.is_some()
    })
    .await;
    found
}

/// Close member `index` and start it again over its own term file, as a
/// restart leaves it: an empty log, and an incarnation that does not vote until
/// a leader promotes it.
async fn restart(cluster: &mut Cluster, index: usize, timing: RaftTiming) {
    cluster.nodes[index].close().await;
    let node = RaftNode::new(
        layout_of(3, index),
        cluster.fabric.transport(index as u64) as Arc<dyn Transport>,
        TermStore::new(cluster._scratch.state(index as u64)),
        StateMachine::new(
            index as u64,
            CursorAllocator::new(index as u64).expect("a lane"),
        ),
        Arc::new(Registry::new(RegistryStore::new())),
        timing,
    )
    .expect("a term file only this member has written");
    node.start().await.expect("starts");
    cluster.nodes[index] = node;
}

/// A cluster whose leader and one follower restart together, leaving the third
/// -- the survivor -- the only member holding the log.
async fn two_of_three_restart_together() -> (Cluster, usize, [usize; 2]) {
    let timing = catching_up_timing();
    let mut cluster = Cluster::build(3, timing);
    cluster.start_all().await;
    let leader = the_leader(&cluster).await.expect("a leader");
    let node = Arc::clone(&cluster.nodes[leader]);
    let proposals = (1..=1500u64).map(|sequence| {
        let node = Arc::clone(&node);
        async move { node.propose(claim(leader as u64, sequence)).await }
    });
    for outcome in futures_util::future::join_all(proposals).await {
        outcome.expect("commits");
    }
    let committed = node.commit_index();
    assert!(
        within(10, || cluster
            .nodes
            .iter()
            .all(|n| n.last_applied() >= committed))
        .await,
        "the followers never applied the log",
    );
    let survivor = (leader + 2) % 3;
    let restarted = [leader, (leader + 1) % 3];
    for index in restarted {
        restart(&mut cluster, index, timing).await;
    }
    (cluster, survivor, restarted)
}

#[tokio::test(flavor = "multi_thread")]
async fn the_survivor_leads_two_restarted_members_back_to_voting() {
    let (cluster, survivor, restarted) = two_of_three_restart_together().await;

    let promoted = within(20, || restarted.iter().all(|&i| cluster.nodes[i].voting())).await;
    let voting: Vec<bool> = restarted
        .iter()
        .map(|&i| cluster.nodes[i].voting())
        .collect();
    assert!(
        promoted,
        "20 s after two of three restarted they are still catching up (voting \
         {voting:?}), the survivor m{survivor} is {:?}, leaders {:?}: nothing can \
         be written",
        cluster.nodes[survivor].role(),
        cluster.leaders(),
    );
    // And the cluster writes again.
    let leader = the_leader(&cluster).await.expect("a leader");
    cluster.nodes[leader]
        .propose(claim(leader as u64, 10_000))
        .await
        .expect("commits");

    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_leader_whose_peers_answer_as_catching_up_keeps_leading() {
    // Check-quorum asks whether this leader is cut off, and it is not: peers
    // that answered within the window are reachable, catching up or not, and
    // with a majority of the cluster answering no other member can gather one.
    let (cluster, _held, _) =
        scripted(3, false, vec![Scripted::restarted(), Scripted::restarted()]).await;
    let node = &cluster.nodes[0];
    assert!(
        within(5, || node.role() == Role::Leader).await,
        "never elected by its restarted peers",
    );
    let term = node.term();

    tokio::time::sleep(Duration::from_millis(10 * quick().election_max_ms)).await;
    assert!(
        node.role() == Role::Leader && node.term() == term,
        "elected in term {term}, now {:?} in term {}: it stood down with both \
         peers answering, and nobody else can lead them",
        node.role(),
        node.term(),
    );

    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_whose_reachable_peers_are_catching_up_still_campaigns() {
    // Whether an election could be won is for the round to decide. The flags a
    // member learned while leading outlive the leadership -- cleared only by a
    // promotion, a new leadership or a link going down -- and a campaign check
    // that left them out made the member that had to lead them unable to try.
    let (cluster, _held, _) =
        scripted(3, false, vec![Scripted::restarted(), Scripted::restarted()]).await;
    let node = &cluster.nodes[0];
    assert!(
        within(5, || node.role() == Role::Leader).await,
        "never elected by its restarted peers",
    );

    // Cut off, nobody answers, and it stands down.
    cluster.fabric.isolate(0, &[1, 2]);
    assert!(
        within(5, || node.role() != Role::Leader).await,
        "cut off, it never stood down",
    );
    cluster.fabric.heal();
    assert!(
        within(5, || node.role() == Role::Leader).await,
        "reachable again, its peers catching up as it last heard, and it never \
         campaigned",
    );

    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_leader_whose_majority_is_catching_up_reports_no_quorum() {
    // Readiness asks whether a write could commit, and it could not.
    let (cluster, _held, _) =
        scripted(3, false, vec![Scripted::restarted(), Scripted::restarted()]).await;
    let node = &cluster.nodes[0];
    assert!(
        within(5, || node.role() == Role::Leader).await,
        "never elected by its restarted peers",
    );
    // Past the first replies, which is when a new leader learns it.
    tokio::time::sleep(Duration::from_millis(5 * quick().heartbeat_ms)).await;

    assert!(
        !node.has_quorum(),
        "both peers are catching up, so nothing can commit, yet the member \
         reports a quorum -- the backend would call itself Ready",
    );

    cluster.close_all().await;
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
    let reply = cluster.nodes[voter as usize]
        .on_request_vote(
            99,
            &RequestVote {
                term: term + 1,
                candidate: 99,
                // An empty log: this candidate holds none of the committed entries.
                last_log_index: 0,
                last_log_term: 0,
                pre_vote: true,
            },
        )
        .expect("the term and vote were saved");

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
    // that the member does not campaign at all: it sends no vote requests into
    // a cluster that cannot hear it, where without the gate it sends some on
    // every timeout.
    //
    // Counted in what it sends, not read from its role. A round already under
    // way when the cut fell may still finish -- a grant already past the
    // fabric's check lands (`Fabric::drained`) -- and a member that won its
    // pre-vote campaigns once, as pre-vote says it should on what it was told:
    // measured, one run in forty under load, as the member found a candidate.
    // The gate governs every round after that.
    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");

    let leader = cluster.leaders()[0];
    let outcast = (0..3u64).find(|&m| m != leader).expect("a follower");
    let others: Vec<u64> = (0..3u64).filter(|&m| m != outcast).collect();
    cluster.fabric.isolate(outcast, &[0, 1, 2]);
    cluster.fabric.settled(outcast, &others).await;
    // One election window more, for a campaign that round began to send what
    // it sends.
    tokio::time::sleep(Duration::from_millis(120)).await;
    let before = cluster.fabric.dropped_from(outcast, "RequestVote");

    // Several election windows.
    tokio::time::sleep(Duration::from_millis(400)).await;
    let sent = cluster.fabric.dropped_from(outcast, "RequestVote") - before;
    assert_eq!(
        sent, 0,
        "an isolated member sent {sent} vote requests; without the quorum gate it campaigns on \
         every timeout into a cluster that cannot hear it",
    );
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
    let reply = cluster.nodes[voter as usize]
        .on_request_vote(
            99,
            &RequestVote {
                term: term + 1,
                candidate: 99,
                last_log_index: 0,
                last_log_term: 0,
                // A real vote, which records something durable -- unlike the
                // pre-vote above.
                pre_vote: false,
            },
        )
        .expect("the term and vote were saved");

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
    let seeded = cluster.nodes[follower as usize]
        .on_append_entries(
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
        )
        .expect("the term and vote were saved");
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
    let reply = cluster.nodes[follower as usize]
        .on_append_entries(
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
        )
        .expect("the term and vote were saved");

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

#[tokio::test(flavor = "multi_thread")]
async fn a_member_catching_up_still_counts_against_the_majority() {
    // Figure 2's leader rule commits N once "a majority of matchIndex[i] >= N"
    // -- a majority of the *voting configuration*. A member still catching up
    // after a restart contributes no acknowledgement, but it is still one of
    // the three, so the leader needs one other countable member at N.
    //
    // The rule once took the majority of the members it had counted instead:
    // with one of three catching up, the tally was two long, its "majority"
    // one, and the leader committed on its own. The chaos soak's commit audit
    // measured it in 92 of 96 runs of seed 11245 -- "m2 (term 57) advanced its
    // commit index 56 -> 60 ... m2 last=60 [leader]; m0 match=0 catching-up
    // (bar 56); m1 match=52" -- and in the runs that went on long enough, a
    // later leader that never had the entry wrote over it.
    use nmos_registry_raft::messages::AppendEntriesReply;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    for sequence in 1..=3u64 {
        node.propose(claim(leader, sequence))
            .await
            .expect("commits");
    }
    let peers: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();
    let (restarted, countable) = (peers[0], peers[1]);
    assert!(
        until(|| node
            .peer_progress(countable)
            .is_some_and(|(matched, _, _)| matched >= node.last_log_index()))
        .await,
        "member {countable} never acknowledged everything the leader holds",
    );
    let committed = node.commit_index();
    assert_eq!(
        committed,
        node.last_log_index(),
        "the leader has uncommitted entries already, so the one appended \
         below would not be the only one",
    );

    // Nothing real reaches the leader from here on: every reply it handles is
    // the one this test gives it.
    cluster.fabric.isolate(leader, &peers);

    // `restarted` comes back with an empty log, as a restart leaves it, and
    // the leader forgets what it knew of that member's log...
    node.on_peer_state(restarted, true, u64::MAX);
    // ...then appends an entry that only it holds.
    let proposing = Arc::clone(&node);
    let pending = tokio::spawn(async move { proposing.propose(claim(leader, 4)).await });
    assert!(
        until(|| node.last_log_index() > committed).await,
        "the leader never appended the entry",
    );
    let only_on_the_leader = node.last_log_index();

    // The restarted member answers: catching up, and far below its bar.
    assert_eq!(node.role(), Role::Leader, "stepped down before the reply");
    node.on_append_entries_reply(
        restarted,
        &AppendEntriesReply {
            term: node.term(),
            success: true,
            match_index: 1,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: true,
            request_id: u64::MAX,
        },
    )
    .expect("the term and vote were saved");

    let (restarted_at, still_catching_up, bar) =
        node.peer_progress(restarted).expect("the peer is tracked");
    let (countable_at, _, _) = node.peer_progress(countable).expect("the peer is tracked");
    assert!(
        still_catching_up && restarted_at < bar,
        "this needs member {restarted} catching up below its bar, and it is \
         at {restarted_at} with a bar of {bar} (catching up: {still_catching_up})",
    );
    assert_eq!(
        node.commit_index(),
        committed,
        "the leader committed through {} on its own: index {only_on_the_leader} \
         is held by the leader alone -- member {restarted} is catching up at \
         {restarted_at} and member {countable} holds {countable_at} -- and a \
         majority of three is two",
        node.commit_index(),
    );
    pending.abort();
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_catching_up_is_not_promoted_below_the_leaders_last_index() {
    // The promotion bar is the leader's whole log, not its commit index. A
    // leader cannot tell an entry an earlier leader committed -- which it holds,
    // by Leader Completeness, but has not yet learned is committed -- from one
    // nobody has committed: both sit above its commit index, inside its log.
    //
    // Barring at the commit index promoted a restarted member without an entry
    // an earlier leader had committed, and with its vote a candidate that never
    // had the entry was elected and wrote over it: the chaos soak's promotion
    // audit measured it (seeds 11245, 51509, 53951, 55705), and
    // `nmos/raft/tests/test_consensus.py` reproduces the whole sequence
    // deterministically. Here the entry above the commit index is one the
    // leader appended while cut off, the cheapest way to put one there; the
    // leader's view of it is the same.
    use nmos_registry_raft::messages::AppendEntriesReply;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    node.propose(claim(leader, 1)).await.expect("commits");
    let peers: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();
    let peer = peers[0];
    assert!(
        until(|| node.commit_index() == node.last_log_index()).await,
        "the leader has uncommitted entries already, so the one appended below \
         would not be the only one",
    );
    let committed = node.commit_index();

    cluster.fabric.isolate(leader, &peers);
    let proposing = Arc::clone(&node);
    let pending = tokio::spawn(async move { proposing.propose(claim(leader, 2)).await });
    assert!(
        until(|| node.last_log_index() > committed).await,
        "the leader never appended the entry",
    );
    let holding = node.last_log_index();

    // A restarted member, back and caught up exactly to the commit index.
    node.on_peer_state(peer, true, u64::MAX);
    assert_eq!(node.role(), Role::Leader, "stepped down before the reply");
    node.on_append_entries_reply(
        peer,
        &AppendEntriesReply {
            term: node.term(),
            success: true,
            match_index: committed,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: true,
            request_id: u64::MAX,
        },
    )
    .expect("the term and vote were saved");

    let (matched, catching_up, bar) = node.peer_progress(peer).expect("the peer is tracked");
    assert!(
        catching_up,
        "member {peer} was promoted back into the electorate at index {matched}, \
         the leader's commit index, while the leader holds entries through \
         {holding}: had one of them been committed by an earlier leader, the \
         member would now vote without it",
    );
    assert_eq!(
        bar, holding,
        "the bar is {bar}, not the leader's last index {holding}",
    );
    pending.abort();
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

    // The peer's own replies stop reaching the leader first: a real one landing
    // between the two reads below moves the peer forward -- rightly -- and the
    // comparison then fails for it, measured as 5 against 4. Cut, and then any
    // already past the cut waited out: the cut alone left one in forty runs
    // under load failing that way (`Fabric::drained`).
    cluster.fabric.cut(peer, leader as u64);
    cluster.fabric.drained(peer, leader as u64).await;
    let (before, _, _) = cluster.nodes[leader]
        .peer_progress(peer)
        .expect("the peer is tracked");
    let term = cluster.nodes[leader].term();

    // What an entries-less send anchored well behind draws back. Its id is
    // above the reply floor, as the Python twin's `reply_floor + 1` is: at or
    // below it the reply is fenced before the guard under test is reached --
    // measured: with the id 0 this sent before, the test passed with the guard
    // disabled.
    cluster.nodes[leader]
        .on_append_entries_reply(
            peer,
            &AppendEntriesReply {
                term,
                success: true,
                match_index: 1,
                conflict_index: 0,
                conflict_term: 0,
                catching_up: false,
                request_id: u64::MAX,
            },
        )
        .expect("the term and vote were saved");

    let (after, _, _) = cluster.nodes[leader]
        .peer_progress(peer)
        .expect("the peer is tracked");
    assert_eq!(
        after, before,
        "member {peer} was recorded as holding only {after} because a \
         heartbeat vouched for that much; it had already acknowledged {before}",
    );
    let next = cluster.nodes[leader]
        .peer_next_index(peer)
        .expect("the peer is tracked");
    assert!(
        next > before,
        "next_index fell to {next}, so this leader will re-send entries member \
         {peer} already holds",
    );
}

// -- a refusal that moves nothing --------------------------------------------
//
// etcd re-sends after a rejection only when the rejection lowers `Next`
// (`MaybeDecrTo`, `tracker/progress.go:226-254`: a stale one returns false and
// nothing is sent). This re-sent every rejection at once, whatever it did to
// `next_index` -- so a follower whose hint pointed where the leader already was
// drew the same append straight back, forever. Such a follower holds a
// committed snapshot the leader's log contradicts, which only lost committed
// data can produce (amnesia past the budget): the soak's seed 111504 measured
// 124,991 appends inside one millisecond of cluster time. The Python suite
// holds the same two (`TestARefusalThatMovesNothingWaitsForTheNextHeartbeat`).

#[tokio::test(start_paused = true)]
async fn a_refusal_pointing_where_the_leader_is_draws_no_resend() {
    use nmos_registry_raft::messages::AppendEntriesReply;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    node.propose(claim(leader, 1)).await.expect("commits");
    let peer = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");

    // What the leader sends this peer from now on is dropped where it is sent,
    // and counted there -- synchronously, on a clock that does not move.
    cluster.fabric.cut(leader, peer);
    let probing = node.peer_next_index(peer).expect("the peer is tracked");
    let before = cluster.fabric.dropped();
    node.on_append_entries_reply(
        peer,
        &AppendEntriesReply {
            term: node.term(),
            success: false,
            match_index: 0,
            conflict_index: probing,
            conflict_term: node.term(),
            catching_up: false,
            request_id: u64::MAX,
        },
    )
    .expect("the term and vote were saved");

    assert_eq!(node.peer_next_index(peer), Some(probing));
    assert_eq!(
        cluster.fabric.dropped() - before,
        0,
        "a refusal that left next_index at {probing} was re-sent at once, to draw the same \
         refusal",
    );
    cluster.close_all().await;
}

/// A peer that grants every vote and refuses every append as seed 111504's
/// follower did: its committed snapshot contradicts the leader's log at its
/// boundary, so each refusal asks to resume from the index the leader is
/// already sending from. Counts the appends it is sent.
struct Contradicting {
    voter: Arc<Scripted>,
    appends: std::sync::atomic::AtomicU64,
}

#[async_trait::async_trait]
impl nmos_registry_raft::transport::PeerHandler for Contradicting {
    fn on_request_vote(
        &self,
        peer: u64,
        message: &nmos_registry_raft::messages::RequestVote,
    ) -> Result<nmos_registry_raft::messages::RequestVoteReply, PersistentStateError> {
        self.voter.on_request_vote(peer, message)
    }

    fn on_append_entries(
        &self,
        _peer: u64,
        message: &nmos_registry_raft::messages::AppendEntries,
    ) -> Result<nmos_registry_raft::messages::AppendEntriesReply, PersistentStateError> {
        self.appends
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        Ok(nmos_registry_raft::messages::AppendEntriesReply {
            term: message.term,
            success: false,
            match_index: 0,
            conflict_index: message.prev_log_index + 1,
            conflict_term: 1,
            catching_up: false,
            request_id: message.request_id,
        })
    }

    fn on_install_snapshot(
        &self,
        peer: u64,
        message: &nmos_registry_raft::messages::InstallSnapshot,
    ) -> Result<nmos_registry_raft::messages::InstallSnapshotReply, PersistentStateError> {
        self.voter.on_install_snapshot(peer, message)
    }

    fn on_promote(&self, _peer: u64, _message: &nmos_registry_raft::messages::Promote) {}

    fn on_request_vote_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::RequestVoteReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    fn on_append_entries_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::AppendEntriesReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    fn on_install_snapshot_reply(
        &self,
        _peer: u64,
        _message: &nmos_registry_raft::messages::InstallSnapshotReply,
    ) -> Result<(), PersistentStateError> {
        Ok(())
    }

    async fn on_propose(
        &self,
        peer: u64,
        message: &nmos_registry_raft::messages::Propose,
    ) -> nmos_registry_raft::messages::ProposeReply {
        self.voter.on_propose(peer, message).await
    }

    async fn on_forward(
        &self,
        peer: u64,
        message: &nmos_registry_raft::messages::Forward,
    ) -> nmos_registry_raft::messages::ForwardReply {
        self.voter.on_forward(peer, message).await
    }

    async fn on_read_index(
        &self,
        peer: u64,
        message: &nmos_registry_raft::messages::ReadIndex,
    ) -> nmos_registry_raft::messages::ReadIndexReply {
        self.voter.on_read_index(peer, message).await
    }

    fn on_peer_state(&self, _peer: u64, _up: bool, _incarnation: u64) {}
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_that_can_never_accept_is_probed_once_a_heartbeat() {
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    let peers: Vec<Arc<Contradicting>> = (0..2)
        .map(|_| {
            Arc::new(Contradicting {
                voter: Scripted::granting(true),
                appends: std::sync::atomic::AtomicU64::new(0),
            })
        })
        .collect();
    for (offset, peer) in peers.iter().enumerate() {
        cluster
            .fabric
            .transport(offset as u64 + 1)
            .start(Arc::clone(peer) as Arc<dyn PeerHandler>)
            .await
            .expect("the fabric starts unconditionally");
    }
    cluster.nodes[0].start().await.expect("starts");
    assert!(
        until(|| cluster.nodes[0].role() == Role::Leader).await,
        "the real member never led"
    );

    let sent = || -> u64 {
        peers
            .iter()
            .map(|peer| peer.appends.load(std::sync::atomic::Ordering::Relaxed))
            .sum()
    };
    let before = sent();
    let heartbeats = 20;
    tokio::time::sleep(Duration::from_millis(heartbeats * quick().heartbeat_ms)).await;
    let appends = sent() - before;

    // A probe a heartbeat to each, with room for the tick's timing.
    assert!(
        appends <= 3 * 2 * heartbeats,
        "{appends} appends in {heartbeats} heartbeats to two followers that can never accept: \
         the leader and they are re-triggering each other with no delay between steps",
    );
    cluster.nodes[0].close().await;
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
    let follower = (0..3usize).find(|&m| m != leader).expect("a follower");
    assert!(
        until(|| cluster.nodes[follower].commit_index() > 1).await,
        "the follower committed nothing, so this proves nothing",
    );

    let committed = cluster.nodes[follower].commit_index();
    let term = cluster.nodes[follower].term();
    let prev_log_term = cluster.nodes[follower]
        .log_term_at(1)
        .expect("index 1 is held");

    let reply = cluster.nodes[follower]
        .on_append_entries(
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
        )
        .expect("the term and vote were saved");

    assert!(reply.success, "a stale append was rejected outright");
    assert_eq!(
        reply.match_index, committed,
        "answered with {} for a message anchored at index 1, though this \
         member has committed through {committed} -- the leader stores that \
         verbatim",
        reply.match_index,
    );
}

#[tokio::test(flavor = "multi_thread")]
async fn a_snapshot_reply_from_an_earlier_term_is_not_credited() {
    // A completion acknowledged in an earlier term, arriving now.
    // `InstallSnapshotReply` carries no correlation id, so the `reply_floor`
    // fence that protects appends cannot protect it -- only its term can.
    // Believing a stale `done = true` credits the peer with *this* leader's
    // current snapshot: the chaos soak measured a member credited with index
    // 504 from a reply sent in term 78 about a snapshot through 500, whose every
    // genuine rejection afterwards was discarded as stale, so it never caught
    // up. etcd drops every lower-term message before it reaches the progress
    // tracker (`raft.go:1133-1186`).
    use nmos_registry_raft::messages::InstallSnapshotReply;
    use nmos_registry_raft::transport::PeerHandler;

    let timing = RaftTiming {
        compaction_threshold: 4,
        max_log_entries: 8,
        snapshot_chunk: 256,
        ..quick()
    };
    let cluster = Cluster::build(3, timing);
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    for sequence in 1..=8u64 {
        node.propose(claim(leader, sequence))
            .await
            .expect("commits");
    }
    assert!(
        until(|| node.log_term_at(1).is_none()).await,
        "the leader never compacted, so this proves nothing",
    );
    let peers: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();
    let peer = peers[0];

    // Nothing real reaches the leader from here on, and its view of the peer
    // starts from nothing, as after a reconnect.
    cluster.fabric.isolate(leader, &peers);
    node.on_peer_state(peer, true, u64::MAX);
    assert_eq!(node.role(), Role::Leader, "stepped down before the reply");
    assert_eq!(
        node.peer_progress(peer).map(|(matched, _, _)| matched),
        Some(0)
    );

    let stale = node.term().saturating_sub(1);
    node.on_install_snapshot_reply(
        peer,
        &InstallSnapshotReply {
            term: stale,
            bytes_received: 0,
            done: true,
            // Past any floor, and crediting something: were the term not
            // checked, nothing else would stop this reply.
            commit_index: node.last_log_index(),
            request_id: u64::MAX,
        },
    )
    .expect("the term and vote were saved");

    let (matched, _, _) = node.peer_progress(peer).expect("the peer is tracked");
    assert_eq!(
        matched,
        0,
        "a snapshot acknowledgement from term {stale} credited member {peer} with \
         index {matched} in term {}: the snapshot it acknowledged is not the one this \
         leader holds, and every genuine rejection below that index will now be \
         discarded as stale",
        node.term(),
    );
    cluster.close_all().await;
}

// -- a snapshot transfer is of one snapshot ----------------------------------
//
// Both ends reassembled or sliced by offset alone: the leader sliced whatever
// snapshot it held *now* at the offset it had reached, and the follower
// appended any chunk whose offset lined up. The chaos soak's splice detector
// measured a follower fed the head of one snapshot and the tail of the next,
// and when the result happened to decode it installed -- replicas that had
// lost acknowledged writes while reporting themselves caught up.

#[tokio::test(flavor = "multi_thread")]
async fn a_chunk_of_another_snapshot_does_not_continue_a_transfer() {
    use nmos_registry_raft::messages::InstallSnapshot;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let follower = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let node = &cluster.nodes[follower as usize];

    let head = b"head of the snapshot through 40".to_vec();
    let reply = node
        .on_install_snapshot(
            leader,
            &InstallSnapshot {
                term: node.term(),
                leader,
                last_index: 40,
                last_term: 1,
                offset: 0,
                data: head.clone(),
                done: false,
                ownership: Vec::new(),
                request_id: 1,
            },
        )
        .expect("the term and vote were saved");
    assert_eq!(reply.bytes_received, head.len() as u64);

    let reply = node
        .on_install_snapshot(
            leader,
            &InstallSnapshot {
                term: node.term(),
                leader,
                last_index: 56,
                last_term: 1,
                offset: head.len() as u64,
                data: b"tail of the snapshot through 56".to_vec(),
                done: false,
                ownership: Vec::new(),
                request_id: 1,
            },
        )
        .expect("the term and vote were saved");
    assert!(
        reply.bytes_received == 0 && !reply.done,
        "the follower acknowledged {} bytes of a transfer that began as the snapshot \
         through 40 and went on with the snapshot through 56 -- a splice",
        reply.bytes_received,
    );
    assert_eq!(node.snapshot_buffers(), 0, "the spliced buffer was kept");
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_snapshot_whose_contents_disagree_with_its_transfer_is_refused() {
    use nmos_registry_core::store::RegistryStore;
    use nmos_registry_raft::messages::InstallSnapshot;
    use nmos_registry_raft::ownership::OwnershipTable;
    use nmos_registry_raft::snapshot::{SnapshotStore, collect_all};
    use nmos_registry_raft::transport::PeerHandler;

    // The bytes of a snapshot through (10, t1), about to be sent as if they
    // were one through (13, t1).
    let mut snapshots = SnapshotStore::new();
    snapshots
        .begin(10, 1, &OwnershipTable::new())
        .expect("begins");
    let store = RegistryStore::new();
    let (records, live) = collect_all(&store, snapshots.capture().expect("a capture is open"));
    let payload = snapshots.finish(records, &live).expect("finishes");

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let follower = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let node = &cluster.nodes[follower as usize];
    assert!(
        node.commit_index() < 10,
        "the follower is already past the snapshot"
    );

    let reply = node
        .on_install_snapshot(
            leader,
            &InstallSnapshot {
                term: node.term(),
                leader,
                last_index: 13,
                last_term: 1,
                offset: 0,
                data: payload,
                done: true,
                ownership: Vec::new(),
                request_id: 1,
            },
        )
        .expect("the term and vote were saved");
    assert!(
        !reply.done,
        "installed a snapshot whose contents end at 10 under a transfer claiming 13: the \
         leader now credits an index this member does not hold",
    );
    assert!(
        node.last_applied() < 10,
        "the refused snapshot was applied anyway"
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_member_caught_up_by_snapshot_holds_one_it_can_serve() {
    // The compacted prefix of a member's log is recoverable from its own
    // snapshot. A member that caught up by *installing* one starts its log at
    // the snapshot's boundary, so the entries below exist on it only as the
    // snapshot -- and should it ever lead, they are what a stranded follower
    // needs. It used to keep none: only compaction set a member's snapshot, so
    // such a leader sent its stranded followers nothing but keepalives for as
    // long as it led -- the chaos soak's commonest liveness failure.
    //
    // Nothing is proposed after the install, so the member cannot come by a
    // snapshot through its own compaction and pass this vacuously.
    let timing = RaftTiming {
        compaction_threshold: 4,
        max_log_entries: 8,
        snapshot_chunk: 256,
        ..quick()
    };
    let cluster = Cluster::build(3, timing);
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = Arc::clone(&cluster.nodes[leader as usize]);
    let peers: Vec<u64> = (0..3u64).filter(|&member| member != leader).collect();
    let outcast = peers[0];

    cluster.fabric.isolate(outcast, &[leader, peers[1]]);
    for sequence in 1..=30u64 {
        node.propose(claim(leader, sequence))
            .await
            .expect("commits");
    }
    assert!(
        until(|| node.log_term_at(1).is_none()).await,
        "the leader never compacted, so this proves nothing",
    );

    cluster.fabric.heal();
    // The fabric drops what a cut link carried and says nothing when it heals.
    // A real transport cannot: a lost message is a failed connection, and the
    // reconnect is announced (`on_peer_state`), which resets the leader's view
    // of the peer -- including a snapshot chunk it would otherwise wait on
    // forever. Delivered here as the transport would deliver it.
    {
        use nmos_registry_raft::transport::PeerHandler;
        node.on_peer_state(outcast, true, u64::MAX);
    }
    let returning = Arc::clone(&cluster.nodes[outcast as usize]);
    assert!(
        until(|| returning.commit_index() >= node.commit_index()).await,
        "the returning member never caught up",
    );
    assert!(
        returning.log_term_at(1).is_none(),
        "the returning member caught up by replication, not by snapshot, so this proves nothing",
    );
    let held = returning.snapshot_held();
    assert!(
        held.is_some_and(|(_, bytes)| bytes > 0),
        "member {outcast} caught up by installing a snapshot and holds {held:?}: as leader it \
         could send a follower below its log nothing but keepalives",
    );
    cluster.close_all().await;
}

// -- an abandoned partial snapshot ------------------------------------------

#[tokio::test(flavor = "multi_thread")]
async fn an_abandoned_partial_snapshot_does_not_stop_a_campaign() {
    // A campaign gate on partial snapshots was modelled on etcd's
    // `promotable()` / `hasNextOrInProgressSnapshot()`, which is a *complete*
    // snapshot awaiting application (`log.go:287-291`); etcd has no partial
    // transfers, and this member installs a completed one synchronously. Every
    // chunk and keepalive from a live leader resets the election timer, so the
    // gate was only ever reached once the transfer had stopped -- and then it
    // blocked the campaign the silence called for. The chaos soak measured it:
    // all 27 liveness failures of one 533-run soak were clusters with no leader
    // whose one viable candidate held an abandoned buffer.
    //
    // A pre-vote round lost to the leader's lease is over in microseconds, so
    // the campaign is observed by what it sends: no other member campaigns
    // while the leader lives, so every vote request delivered is this one's.
    use nmos_registry_raft::messages::InstallSnapshot;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let follower = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let node = Arc::clone(&cluster.nodes[follower as usize]);

    // Half a snapshot, and then the leader falls silent towards this member:
    // the transfer can never finish.
    let reply = node
        .on_install_snapshot(
            leader,
            &InstallSnapshot {
                term: node.term(),
                leader,
                last_index: 40,
                last_term: node.term(),
                offset: 0,
                data: b"half a snapshot".to_vec(),
                done: false,
                ownership: Vec::new(),
                request_id: 1,
            },
        )
        .expect("the term and vote were saved");
    assert!(reply.bytes_received > 0 && node.snapshot_buffers() == 1);
    cluster.fabric.cut(leader, follower);
    cluster.fabric.reset_counts();

    tokio::time::sleep(Duration::from_secs(1)).await;
    let asked = cluster
        .fabric
        .delivered()
        .iter()
        .find(|(kind, _)| *kind == "RequestVote")
        .map_or(0, |&(_, count)| count);
    assert!(
        asked > 0,
        "a member whose leader went silent for a second never campaigned, because it held \
         a partial snapshot that could no longer finish (buffers={})",
        node.snapshot_buffers(),
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_partial_snapshot_is_dropped_when_it_can_no_longer_complete() {
    // Nothing removed a partial buffer but a chunk at offset 0, a completion or
    // a refusal -- never its sender's link going, or a term passing -- so an
    // abandoned transfer held its memory for the life of the member.
    use nmos_registry_raft::messages::{AppendEntries, InstallSnapshot};
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let follower = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let node = Arc::clone(&cluster.nodes[follower as usize]);
    let half = |term: u64| InstallSnapshot {
        term,
        leader,
        last_index: 40,
        last_term: term,
        offset: 0,
        data: b"half a snapshot".to_vec(),
        done: false,
        ownership: Vec::new(),
        request_id: 1,
    };

    node.on_install_snapshot(leader, &half(node.term()))
        .expect("the term and vote were saved");
    assert_eq!(node.snapshot_buffers(), 1);
    node.on_peer_state(leader, false, 0);
    assert_eq!(
        node.snapshot_buffers(),
        0,
        "the buffer outlived its sender's link"
    );

    node.on_install_snapshot(leader, &half(node.term()))
        .expect("the term and vote were saved");
    assert_eq!(node.snapshot_buffers(), 1);
    let next = node.term() + 1;
    node.on_append_entries(
        leader,
        &AppendEntries {
            term: next,
            leader,
            prev_log_index: 0,
            prev_log_term: 0,
            leader_commit: 0,
            request_id: 1,
            entries: Vec::new(),
        },
    )
    .expect("the term and vote were saved");
    assert_eq!(node.term(), next, "the member never moved to the new term");
    assert_eq!(
        node.snapshot_buffers(),
        0,
        "the buffer outlived the term it was sent in"
    );
    cluster.close_all().await;
}

// -- a broken invariant stops the member ------------------------------------------

/// Where a broken member's state machine starts: far past anything a fresh
/// cluster commits, so the first apply finds `last_applied` beyond the commit
/// index -- the first thing `check_applied_within_committed` refuses.
const BEYOND: u64 = 100;

/// A member that finds its own state impossible stops, as
/// `RaftInvariantViolated` documents. It used to log, end its apply loop and
/// nothing else -- going on leading, voting and replicating from a store that
/// no longer moved. (Python: `TestABrokenInvariantStopsTheMember`.)
#[tokio::test]
async fn a_lone_leader_that_breaks_stops_leading() {
    let cluster = Cluster::build_from(1, quick(), &[], &[0]);
    cluster.start_all().await;
    let node = &cluster.nodes[0];

    // It led: it committed the no-op that established its term.
    assert!(
        until(|| node.commit_index() >= 1).await,
        "the lone member never led"
    );
    assert!(
        until(|| node.role() != Role::Leader).await,
        "the member whose invariant broke went on leading, from a store that had stopped applying",
    );
    // For longer than any election takes: a stopped member does not campaign.
    tokio::time::sleep(Duration::from_millis(10 * 120)).await;
    assert_ne!(node.role(), Role::Leader, "the stopped member led again");
    cluster.close_all().await;
}

#[tokio::test]
async fn a_member_that_breaks_leaves_the_others_serving() {
    let cluster = Cluster::build_from(3, quick(), &[], &[0]);
    cluster.start_all().await;
    let others = [&cluster.nodes[1], &cluster.nodes[2]];

    assert!(
        until(|| others.iter().all(|node| !node.live_peers().contains(&0))).await,
        "the member whose invariant broke is still linked to its peers: it went on taking part",
    );
    assert!(
        until(|| others.iter().any(|node| node.role() == Role::Leader)).await,
        "the two members left elected nobody",
    );
    let leader = others
        .iter()
        .find(|node| node.role() == Role::Leader)
        .expect("a leader, just seen");
    let outcome = tokio::time::timeout(
        Duration::from_secs(2),
        leader.propose(claim(leader.index(), 1)),
    )
    .await;
    assert!(
        matches!(outcome, Ok(Ok(_))),
        "the others could not commit: {outcome:?}"
    );
    assert_ne!(cluster.nodes[0].role(), Role::Leader);
    cluster.close_all().await;
}

#[tokio::test]
async fn a_caller_waiting_on_a_member_that_breaks_is_answered() {
    // Member 0 is sent no entries until its proposal waits on it: it hears its
    // leader -- an append without entries cannot advance a commit index -- but
    // commits nothing, so it does not apply, which is where it breaks. A
    // proposal queued before a member leads is refused for want of a leader
    // and waits on nothing, which is why this is built with care.
    use nmos_registry_raft::messages::Message;
    use std::sync::atomic::{AtomicBool, Ordering};

    let cluster = Cluster::build_from(3, quick(), &[], &[0]);
    let holding = Arc::new(AtomicBool::new(true));
    let hold = Arc::clone(&holding);
    cluster.fabric.intercept(move |_, to, message| {
        !(to == 0
            && hold.load(Ordering::SeqCst)
            && matches!(*message, Message::AppendEntries(ref append) if !append.entries.is_empty()))
    });
    // Its peers first, so the leader is one of them.
    for node in &cluster.nodes[1..] {
        node.start().await.expect("starts");
    }
    assert!(
        until(|| cluster.nodes[1..]
            .iter()
            .any(|node| node.role() == Role::Leader))
        .await,
        "no leader among the members that can apply",
    );
    let broken = &cluster.nodes[0];
    broken.start().await.expect("starts");
    assert!(
        until(|| broken.leader().is_some()).await,
        "member 0 never heard its leader"
    );

    let proposer = Arc::clone(broken);
    let proposing = tokio::spawn(async move { proposer.propose(claim(0, 1)).await });
    assert!(
        until(|| broken.pending_waiters() == 1).await,
        "the proposal never waited on member 0",
    );
    assert_eq!(
        broken.commit_index(),
        0,
        "member 0 committed while its entries were held"
    );

    holding.store(false, Ordering::SeqCst);
    let answered = tokio::time::timeout(Duration::from_secs(2), proposing).await;
    assert!(
        matches!(answered, Ok(Ok(Err(_)))),
        "a caller waiting on a member that stopped applying was not answered unavailable: \
         {answered:?}",
    );
    cluster.close_all().await;
}

#[tokio::test]
async fn a_member_that_breaks_says_why() {
    let cluster = Cluster::build_from(1, quick(), &[], &[0]);
    cluster.start_all().await;
    let node = &cluster.nodes[0];

    let why = tokio::time::timeout(Duration::from_secs(2), node.wait_for_failure())
        .await
        .expect("the member never reported stopping");
    assert!(why.contains(&format!("applied through {BEYOND}")), "{why}");
    assert_eq!(node.failure().as_deref(), Some(why.as_str()));
    cluster.close_all().await;
}

// -- a leader contradicting a committed entry stops the follower -----------------

/// A follower that has committed through at least index 3, then cut off from
/// the fabric so that only the test talks to it; and the member it follows.
///
/// L2's state, built by hand -- a follower holding committed entries its
/// leader contradicts -- because the protocol no longer produces it: Raft makes
/// it impossible, and the non-voting rejoin and the recovery election's veto
/// keep it so. (Python: `TestALeaderContradictingACommittedEntryStopsTheFollower`.)
async fn a_committed_follower(cluster: &Cluster) -> (u64, u64) {
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = &cluster.nodes[leader as usize];
    for sequence in 1..=2 {
        let outcome = tokio::time::timeout(
            Duration::from_secs(2),
            node.propose(claim(leader, sequence)),
        )
        .await;
        assert!(matches!(outcome, Ok(Ok(_))), "not committed: {outcome:?}");
    }
    let follower = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let member = &cluster.nodes[follower as usize];
    assert!(
        until(|| member.commit_index() >= 3).await,
        "the follower never caught up"
    );
    let others: Vec<u64> = (0..3u64).filter(|&m| m != follower).collect();
    cluster.fabric.isolate(follower, &others);
    // Nothing already past the cut may move the state the tests read next.
    cluster.fabric.settled(follower, &others).await;
    (follower, leader)
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_told_its_committed_entry_is_otherwise_stops() {
    use nmos_registry_raft::messages::AppendEntries;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    let (follower, leader) = a_committed_follower(&cluster).await;
    let member = &cluster.nodes[follower as usize];
    let commit = member.commit_index();
    let held = member
        .log_term_at(commit)
        .expect("the commit point is held");

    let reply = member
        .on_append_entries(
            leader,
            &AppendEntries {
                term: member.term() + 1,
                leader,
                prev_log_index: commit,
                prev_log_term: held + 1,
                leader_commit: commit,
                request_id: 5,
                entries: Vec::new(),
            },
        )
        .expect("the term and vote were saved");
    assert!(
        member.failure().is_some(),
        "a follower that committed ({commit}, t{held}) was told otherwise and went on as if it \
         could catch up: {reply:?}",
    );
    assert!(!reply.success && reply.request_id == 0, "{reply:?}");
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_sent_an_entry_contradicting_a_committed_one_stops() {
    // Anchored below the commit point, where it matches: the answer to such an
    // append credits `match = commit` without looking at what it carries.
    use nmos_registry_raft::messages::{AppendEntries, WireEntry};
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    let (follower, leader) = a_committed_follower(&cluster).await;
    let member = &cluster.nodes[follower as usize];
    let commit = member.commit_index();
    let (first, second) = (
        member.log_term_at(1).expect("index 1 is held"),
        member.log_term_at(2).expect("index 2 is held"),
    );

    let reply = member
        .on_append_entries(
            leader,
            &AppendEntries {
                term: member.term() + 1,
                leader,
                prev_log_index: 1,
                prev_log_term: first,
                leader_commit: commit,
                request_id: 5,
                entries: vec![WireEntry {
                    term: second + 1,
                    index: 2,
                    payload: claim(leader, 9).encode(),
                }],
            },
        )
        .expect("the term and vote were saved");
    assert!(
        member.failure().is_some(),
        "a follower was sent a different entry at committed index 2 and credited the leader \
         with its commit index: {reply:?}",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_sent_a_contradiction_at_its_snapshot_boundary_stops() {
    // Seed 111504's shape: the anchor lies below the boundary, where nothing is
    // left to compare, and the boundary's own term is kept. A compaction
    // threshold of two gives the follower a boundary within what it committed
    // -- wherever the count lands it, found below as the lowest index whose
    // term is still held.
    use nmos_registry_raft::messages::{AppendEntries, WireEntry};
    use nmos_registry_raft::transport::PeerHandler;

    let timing = RaftTiming {
        compaction_threshold: 2,
        ..quick()
    };
    let cluster = Cluster::build(3, timing);
    let (follower, leader) = a_committed_follower(&cluster).await;
    let member = &cluster.nodes[follower as usize];
    let commit = member.commit_index();
    assert!(
        until(|| member.last_applied() == commit && member.log_term_at(1).is_none()).await,
        "the follower never compacted",
    );
    // Cut off, it applies nothing more, so the boundary has stopped moving once
    // what it has applied is compacted.
    tokio::time::sleep(Duration::from_millis(50)).await;
    let boundary = (1..=commit)
        .find(|&index| member.log_term_at(index).is_some())
        .expect("a boundary at or below the commit point");
    let held = member
        .log_term_at(boundary)
        .expect("the boundary's term is kept");

    let reply = member
        .on_append_entries(
            leader,
            &AppendEntries {
                term: member.term() + 1,
                leader,
                prev_log_index: boundary - 1,
                prev_log_term: held + 1,
                leader_commit: commit,
                request_id: 5,
                entries: vec![WireEntry {
                    term: held + 1,
                    index: boundary,
                    payload: claim(leader, 9).encode(),
                }],
            },
        )
        .expect("the term and vote were saved");
    assert!(
        member.failure().is_some(),
        "a follower holding ({boundary}, t{held}) as its snapshot boundary, committed through \
         {commit}, was sent another entry there and credited the leader: {reply:?}",
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn a_follower_sent_a_snapshot_contradicting_a_committed_entry_stops() {
    use nmos_registry_raft::messages::InstallSnapshot;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    let (follower, leader) = a_committed_follower(&cluster).await;
    let member = &cluster.nodes[follower as usize];
    let commit = member.commit_index();
    let held = member
        .log_term_at(commit)
        .expect("the commit point is held");

    let reply = member
        .on_install_snapshot(
            leader,
            &InstallSnapshot {
                term: member.term() + 1,
                leader,
                last_index: commit,
                last_term: held + 1,
                offset: 0,
                data: b"the first chunk".to_vec(),
                done: false,
                ownership: Vec::new(),
                request_id: 7,
            },
        )
        .expect("the term and vote were saved");
    assert!(
        member.failure().is_some(),
        "a follower that committed ({commit}, t{held}) was sent a snapshot through another \
         term there and took it as one it already held: {reply:?}",
    );
    assert!(
        reply.commit_index == 0 && reply.request_id == 0,
        "{reply:?}"
    );
    cluster.close_all().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn what_agrees_with_a_committed_entry_is_answered_as_before() {
    use nmos_registry_raft::messages::{AppendEntries, WireEntry};
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    let (follower, leader) = a_committed_follower(&cluster).await;
    let member = &cluster.nodes[follower as usize];
    let commit = member.commit_index();
    let term = member.term();
    let (first, second) = (
        member.log_term_at(1).expect("index 1 is held"),
        member.log_term_at(2).expect("index 2 is held"),
    );

    // A delayed append that agrees: credited with the commit index.
    let reply = member
        .on_append_entries(
            leader,
            &AppendEntries {
                term,
                leader,
                prev_log_index: 1,
                prev_log_term: first,
                leader_commit: commit,
                request_id: 5,
                entries: vec![WireEntry {
                    term: second,
                    index: 2,
                    payload: claim(leader, 9).encode(),
                }],
            },
        )
        .expect("the term and vote were saved");
    assert!(reply.success && reply.match_index == commit, "{reply:?}");
    // An ordinary conflict beyond the commit index: a refusal with a hint.
    let reply = member
        .on_append_entries(
            leader,
            &AppendEntries {
                term,
                leader,
                prev_log_index: commit + 5,
                prev_log_term: term,
                leader_commit: commit,
                request_id: 6,
                entries: Vec::new(),
            },
        )
        .expect("the term and vote were saved");
    assert!(!reply.success && reply.conflict_index > commit, "{reply:?}");
    assert_eq!(member.failure(), None);
    cluster.close_all().await;
}

/// A reply cannot put a follower beyond the leader's own log.
///
/// In a correct run no reply does -- a follower vouches for a window the leader
/// sent, or for its commit index, which Leader Completeness puts inside the
/// leader's log. Only after committed data has been lost can it lie beyond, and
/// the unbounded credit then put `next_index` past the end of the log:
/// `send_append` sent a snapshot the follower ignored, and never an anchor it
/// could check. (Python: `TestALeaderCreditsNoMoreThanItsOwnLog`.)
#[tokio::test(flavor = "multi_thread")]
async fn a_leader_credits_no_follower_past_its_own_log() {
    use nmos_registry_raft::messages::AppendEntriesReply;
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    cluster.start_all().await;
    assert!(until(|| cluster.leaders().len() == 1).await, "no leader");
    let leader = cluster.leaders()[0];
    let node = &cluster.nodes[leader as usize];
    let peer = (0..3u64)
        .find(|&member| member != leader)
        .expect("a follower");
    let last = node.last_log_index();

    node.on_append_entries_reply(
        peer,
        &AppendEntriesReply {
            term: node.term(),
            success: true,
            match_index: last + 5,
            conflict_index: 0,
            conflict_term: 0,
            catching_up: false,
            request_id: u64::MAX,
        },
    )
    .expect("the term and vote were saved");
    let next = node.peer_next_index(peer).expect("a tracked peer");
    assert!(
        next <= node.last_log_index() + 1,
        "a reply vouching for {} put next_index at {next} against a log ending at {}",
        last + 5,
        node.last_log_index(),
    );
    cluster.close_all().await;
}

/// An append a follower cannot take is refused in its reply, keeping the link.
///
/// An undecodable entry, and entries that do not follow what the log holds. No
/// correct leader sends either. The Python member used to let both escape its
/// handler, so its transport dropped the whole connection and the leader
/// reconnected and sent them again; it now refuses them as this does. (Python:
/// `TestAnAppendThatCannotBeTakenIsRefusedInTheReply`.)
#[tokio::test(flavor = "multi_thread")]
async fn an_append_a_follower_cannot_take_is_refused_in_its_reply() {
    use nmos_registry_raft::messages::{AppendEntries, WireEntry};
    use nmos_registry_raft::transport::PeerHandler;

    let cluster = Cluster::build(3, quick());
    let (follower, leader) = a_committed_follower(&cluster).await;
    let member = &cluster.nodes[follower as usize];
    let last = member.last_log_index();
    let held = member.log_term_at(last).expect("the last entry is held");
    let term = member.term();

    for (index, payload, request_id) in [
        (last + 1, Vec::new(), 9),
        (last + 2, claim(leader, 9).encode(), 10),
    ] {
        let reply = member
            .on_append_entries(
                leader,
                &AppendEntries {
                    term,
                    leader,
                    prev_log_index: last,
                    prev_log_term: held,
                    leader_commit: member.commit_index(),
                    request_id,
                    entries: vec![WireEntry {
                        term,
                        index,
                        payload,
                    }],
                },
            )
            .expect("the term and vote were saved");
        assert!(
            !reply.success && reply.request_id == request_id,
            "an entry at {index} was answered {reply:?}",
        );
    }
    assert_eq!(member.last_log_index(), last, "something was appended");
    assert_eq!(member.failure(), None);
    cluster.close_all().await;
}
