// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! A snapshot over a slow link completes, at the link's own speed.
//!
//! Two defects stalled such a transfer for good, each on its own, and the chaos
//! soak could show neither: it models a link's latency, not its bandwidth, so no
//! message there takes longer to cross for being larger. Both were measured over
//! real sockets (part 17 of the fix record):
//!
//! * **A chunk slower than `election_min`.** A leader sent the chunk in flight
//!   again, under a new id, once its answer was overdue, and only the newest
//!   copy's answer drove the transfer. When one chunk's round trip outlasts
//!   `election_min` every answer arrives already superseded, so the transfer
//!   never passed its first chunk, and the copies -- each a whole chunk -- queued
//!   behind it: 194 copies of chunk 0 in 30 s at 16 KiB/s, 4 KiB chunks, a
//!   150 ms `election_min`. A chunk is now sent again only once the connection
//!   it went out on has ended, which is the only way it can be lost.
//! * **A chunk slower than the read deadline.** While a chunk crosses, all its
//!   sender can hear on that connection is answers: to the chunk, which cannot
//!   come until the chunk is whole, and to the sender's own heartbeat, which is
//!   queued behind the chunk. So a chunk taking longer than the deadline to
//!   cross starved the sender's reads, and it closed its own working
//!   connection: a new BULK connection every ~5.1 s, the transfer never past its
//!   first chunk. Both ends of a connection now send their own heartbeat.
//!
//! Three members on real [`RaftTransport`]s over loopback, every directed link
//! through a relay; the leader's connections to one member held to a rate, and
//! that member restarted empty, so it can catch up only by snapshot. Port of
//! `test_slow_link.py`.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::arithmetic_side_effects,
    clippy::panic
)]

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use nmos_cluster::{Derivation, MemberSpec, derive_cluster};
use nmos_registry::registry::Registry;
use nmos_registry_core::store::RegistryStore;
use nmos_registry_raft::cluster::{RAFT_FLAVOUR, RaftLayout, derive_raft_layout};
use nmos_registry_raft::cursors::CursorAllocator;
use nmos_registry_raft::machine::StateMachine;
use nmos_registry_raft::node::{RaftNode, RaftTiming, Role};
use nmos_registry_raft::operations::{Operation, OperationKind, ProposalId};
use nmos_registry_raft::persist::TermStore;
use nmos_registry_raft::transport::{
    CONN_READ_TIMEOUT_MS, DIAL_TIMEOUT_MS, RaftTransport, Transport, TransportSettings,
};

static NEXT: AtomicU64 = AtomicU64::new(0);

/// A directory of term files, removed when the test ends.
struct Scratch(std::path::PathBuf);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "nmos-raft-slow-link-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed),
        ));
        std::fs::create_dir_all(&path).expect("a scratch directory");
        Self(path)
    }

    fn state(&self, member: usize) -> std::path::PathBuf {
        self.0.join(format!("member-{member}.json"))
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        drop(std::fs::remove_dir_all(&self.0));
    }
}

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

fn claim(member: u64, sequence: u64) -> Operation {
    Operation {
        proposal: ProposalId { member, sequence },
        kind: OperationKind::ClaimOwnership {
            node_id: format!("node-{member}-{sequence}"),
            owner: member,
        },
    }
}

/// A claim whose node id is `bytes` long: an entry's payload, chosen.
fn sized_claim(member: u64, sequence: u64, bytes: usize) -> Operation {
    Operation {
        proposal: ProposalId { member, sequence },
        kind: OperationKind::ClaimOwnership {
            node_id: format!("{sequence}:{}", "x".repeat(bytes)),
            owner: member,
        },
    }
}

/// Poll `ready` for up to `limit`.
async fn within(limit: Duration, mut ready: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + limit;
    while Instant::now() < deadline {
        if ready() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    ready()
}

/// One directed link, through a forwarder whose rate can be held down.
struct Relay {
    port: u16,
    /// Bytes per second each direction of each connection is held to; zero is
    /// no limit.
    rate: Arc<AtomicU64>,
    accepting: tokio::task::JoinHandle<()>,
}

impl Relay {
    async fn start(target: SocketAddr) -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("a port");
        let port = listener.local_addr().expect("an address").port();
        let rate = Arc::new(AtomicU64::new(0));
        let shared = Arc::clone(&rate);
        let accepting = tokio::spawn(async move {
            while let Ok((caller, _)) = listener.accept().await {
                let Ok(callee) = tokio::net::TcpStream::connect(target).await else {
                    continue;
                };
                let rate = Arc::clone(&shared);
                tokio::spawn(async move {
                    let (from_caller, to_caller) = caller.into_split();
                    let (from_callee, to_callee) = callee.into_split();
                    let mut forward = tokio::spawn(pump(from_caller, to_callee, Arc::clone(&rate)));
                    let mut backward = tokio::spawn(pump(from_callee, to_caller, rate));
                    // Either direction ending ends both, as a proxy closing its
                    // two sockets does.
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
            rate,
            accepting,
        }
    }
}

impl Drop for Relay {
    fn drop(&mut self) {
        self.accepting.abort();
    }
}

async fn pump(
    mut from: tokio::net::tcp::OwnedReadHalf,
    mut to: tokio::net::tcp::OwnedWriteHalf,
    rate: Arc<AtomicU64>,
) {
    use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
    let mut buffer = vec![0u8; 65_536];
    loop {
        let limit = rate.load(Ordering::SeqCst);
        // Small pieces when rate-limited, so bytes flow evenly rather than in
        // bursts a read deadline could fall between.
        let want = if limit > 0 { 1024 } else { buffer.len() };
        let read = match from.read(&mut buffer[..want]).await {
            Ok(0) | Err(_) => return,
            Ok(read) => read,
        };
        // No limit, no wait.
        if let Some(micros) = (read as u64 * 1_000_000).checked_div(limit) {
            tokio::time::sleep(Duration::from_micros(micros)).await;
        }
        if to.write_all(&buffer[..read]).await.is_err() {
            return;
        }
    }
}

/// Three members, each reaching the others only through relays.
struct Cluster {
    nodes: Vec<Arc<RaftNode>>,
    relays: HashMap<(usize, usize), Relay>,
    addrs: Vec<SocketAddr>,
    timing: RaftTiming,
    read_timeout_ms: u64,
    scratch: Scratch,
}

impl Cluster {
    async fn start(timing: RaftTiming, read_timeout_ms: u64) -> Self {
        let probes: Vec<_> = (0..3)
            .map(|_| std::net::TcpListener::bind("127.0.0.1:0").expect("a port"))
            .collect();
        let addrs: Vec<SocketAddr> = probes
            .iter()
            .map(|probe| probe.local_addr().expect("an address"))
            .collect();
        drop(probes);
        let mut relays = HashMap::new();
        for from in 0..3 {
            for to in (0..3).filter(|&to| to != from) {
                relays.insert((from, to), Relay::start(addrs[to]).await);
            }
        }
        let mut cluster = Self {
            nodes: Vec::new(),
            relays,
            addrs,
            timing,
            read_timeout_ms,
            scratch: Scratch::new(),
        };
        cluster.nodes = (0..3).map(|index| cluster.member(index)).collect();
        for node in &cluster.nodes {
            node.start().await.expect("starts");
        }
        cluster
    }

    /// Member `index` over a new transport and its own term file.
    fn member(&self, index: usize) -> Arc<RaftNode> {
        let raft = layout_of(3, index);
        let peers = (0..3)
            .filter(|&other| other != index)
            .map(|other| {
                (
                    other as u64,
                    ("127.0.0.1".to_owned(), self.relays[&(index, other)].port),
                )
            })
            .collect();
        let transport = Arc::new(RaftTransport::new(TransportSettings {
            local: index as u64,
            peers,
            bind: self.addrs[index],
            cluster_id: raft.cluster_id.clone(),
            member_name: format!("member-{index}"),
            incarnation: 0,
            tls: None,
            rpc_timeout_ms: 2_000,
            conn_read_timeout_ms: self.read_timeout_ms,
            dial_timeout_ms: DIAL_TIMEOUT_MS,
        }));
        let node = RaftNode::new(
            raft,
            Arc::clone(&transport) as Arc<dyn Transport>,
            TermStore::new(self.scratch.state(index)),
            StateMachine::new(
                index as u64,
                CursorAllocator::new(index as u64).expect("a lane"),
            ),
            Arc::new(Registry::new(RegistryStore::new())),
            self.timing,
        )
        .expect("a term file only this member has written");
        transport.set_incarnation(node.incarnation());
        node
    }

    /// Close member `index` and start it again: an empty log, over its own
    /// term file, as a restart leaves it.
    async fn restart(&mut self, index: usize) {
        self.nodes[index].close().await;
        self.nodes[index] = self.member(index);
        self.nodes[index].start().await.expect("restarts");
    }

    /// The one leader, read once.
    async fn leader(&self) -> usize {
        let mut found = None;
        within(Duration::from_secs(10), || {
            let leaders: Vec<usize> = (0..3)
                .filter(|&index| self.nodes[index].role() == Role::Leader)
                .collect();
            found = match leaders[..] {
                [one] => Some(one),
                _ => None,
            };
            found.is_some()
        })
        .await;
        found.expect("a leader")
    }

    async fn close(self) {
        for node in &self.nodes {
            node.close().await;
        }
    }
}

/// Restart a member empty behind a link held to `rate` bytes/s, and wait:
/// `None` if it caught up within `limit`, otherwise what it did instead.
async fn catch_up_by_snapshot(
    chunk: usize,
    rate: u64,
    claims: u64,
    limit: Duration,
    read_timeout_ms: u64,
) -> Option<String> {
    let timing = RaftTiming {
        heartbeat_ms: 20,
        election_min_ms: 150,
        election_max_ms: 300,
        compaction_threshold: 8,
        snapshot_chunk: chunk,
        ..RaftTiming::default()
    };
    let mut cluster = Cluster::start(timing, read_timeout_ms).await;
    let leader = cluster.leader().await;
    let node = Arc::clone(&cluster.nodes[leader]);
    let proposals = (1..=claims).map(|sequence| {
        let node = Arc::clone(&node);
        async move { node.propose(claim(leader as u64, sequence)).await }
    });
    for outcome in futures_util::future::join_all(proposals).await {
        outcome.expect("commits");
    }
    let committed = node.commit_index();
    assert!(
        within(Duration::from_secs(10), || cluster
            .nodes
            .iter()
            .all(|member| member.last_applied() >= committed))
        .await,
        "the followers never applied the log",
    );
    let (_, bytes) = node.snapshot_held().expect("the leader never compacted");
    assert!(
        bytes > 2 * chunk,
        "a snapshot of {bytes} bytes is under three chunks of {chunk}: it proves nothing about a \
         transfer",
    );

    let follower = (leader + 1) % 3;
    cluster.relays[&(leader, follower)]
        .rate
        .store(rate, Ordering::SeqCst);
    cluster.restart(follower).await;
    let target = node.commit_index();
    let restarted = Arc::clone(&cluster.nodes[follower]);
    let caught_up = within(limit, || restarted.last_applied() >= target).await;
    let applied = restarted.last_applied();
    cluster.close().await;
    (!caught_up).then(|| {
        format!(
            "{limit:?} after restarting behind a {} KiB/s link it had applied {applied} of \
             {target}: a {bytes}-byte snapshot in {chunk}-byte chunks, each {} ms across, never \
             arrived",
            rate / 1024,
            chunk as u64 * 1000 / rate,
        )
    })
}

#[tokio::test(flavor = "multi_thread")]
async fn chunks_slower_than_election_min_complete() {
    // 4 KiB at 16 KiB/s: 250 ms a chunk, against a 150 ms `election_min`.
    // About 2 s of transfer; the stall never passed its first chunk.
    let failure = catch_up_by_snapshot(
        4096,
        16 * 1024,
        1500,
        Duration::from_secs(15),
        CONN_READ_TIMEOUT_MS,
    )
    .await;
    assert!(failure.is_none(), "{}", failure.unwrap_or_default());
}

#[tokio::test(flavor = "multi_thread")]
async fn chunks_slower_than_the_read_deadline_complete() {
    // 32 KiB at 24 KiB/s: 1.3 s a chunk, against a 500 ms read deadline -- and
    // against `election_min` as well, so both fixes are needed. About 4 s of
    // transfer.
    let failure =
        catch_up_by_snapshot(32 * 1024, 24 * 1024, 5000, Duration::from_secs(25), 500).await;
    assert!(failure.is_none(), "{}", failure.unwrap_or_default());
}

/// A member behind by more than one frame's worth of entries catches up.
///
/// The leader's window was bounded by entry count alone
/// (`max_entries_per_append`), and a window of nine 2 MiB entries made a frame
/// above the 16 MiB cap: the frame was never written -- silently, an empty
/// buffer in its place -- and once the outstanding append's pause expired the
/// same window was tried again, for ever. A member behind by that much never
/// caught up, and the chaos soak could not see it, because its network carries
/// messages without encoding them. Measured over real sockets (part 21 of the
/// fix record). The window is now bounded by bytes as well
/// (`max_append_bytes`), an entry larger than the bound travelling alone, as
/// etcd's `MaxSizePerMsg`.
///
/// Real transports on loopback: the leader commits nine 2 MiB claims, then one
/// member restarts empty and must be caught up by replication -- nothing was
/// compacted, so no snapshot can stand in for the window.
#[tokio::test(flavor = "multi_thread")]
async fn a_member_behind_by_more_than_the_frame_cap_catches_up() {
    let timing = RaftTiming {
        heartbeat_ms: 20,
        election_min_ms: 150,
        election_max_ms: 300,
        ..RaftTiming::default()
    };
    let mut cluster = Cluster::start(timing, CONN_READ_TIMEOUT_MS).await;
    let leader = cluster.leader().await;
    let follower = (leader + 1) % 3;
    let node = Arc::clone(&cluster.nodes[leader]);
    // One at a time, as the Python twin: each committed before the next.
    for sequence in 1..=9u64 {
        node.propose(sized_claim(leader as u64, sequence, 2 << 20))
            .await
            .expect("commits");
    }
    let target = node.commit_index();

    cluster.restart(follower).await;
    let restarted = Arc::clone(&cluster.nodes[follower]);
    let caught_up = within(Duration::from_secs(20), || {
        restarted.commit_index() >= target
    })
    .await;
    let report = format!(
        "20 s after restarting behind {target} entries, nine of them 2 MiB, the member had \
         committed {} (its log ends at {}); the leader's next_index for it: {:?}",
        restarted.commit_index(),
        restarted.last_log_index(),
        node.peer_next_index(follower as u64),
    );
    cluster.close().await;
    assert!(caught_up, "{report}");
}
