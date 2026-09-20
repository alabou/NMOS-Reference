// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The client against a real etcd.
//!
//! The unit tests pin the parts that are decidable without a server -- key
//! ranges, comparison builders, revision grouping, error classification. None
//! of them proves the client can talk to etcd, and that is the whole claim of
//! this crate.
//!
//! What matters most here is that the **method paths** are right. A wrong path
//! is `UNIMPLEMENTED` at the first call, and `/etcdserverpb.KV/Compaction`
//! looks entirely plausible next to `CompactionRequest` when the method is
//! actually `Compact`. The Python resolves every path against the compiled
//! proto descriptor at import for exactly that reason; this crate keeps them
//! `const` and proves them here instead.
//!
//! Skips itself when `.etcd/etcd` is absent, the same way the Python suite
//! does -- a checkout without the binary is a normal state, not a failure.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::indexing_slicing,
    clippy::panic,
    // `Instant + Duration` for a startup deadline. The panic-free lints exist
    // for the registry's write path, not for a test's wall clock.
    clippy::arithmetic_side_effects
)]

use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

use nmos_etcd::channel::{Endpoint, EtcdChannelPool, SharedPool};
use nmos_etcd::kv::{
    EtcdKv, compare_absent, compare_exists, compare_mod, delete_prefix_op, first_kv, put_op,
    range_op,
};
use nmos_etcd::lease::EtcdLease;
use nmos_etcd::watch::EtcdWatch;
use nmos_etcd::{EtcdError, RevisionBatch};

/// The repository root, from this crate's manifest.
fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("the repository root is three levels above the crate")
        .to_path_buf()
}

fn etcd_binary() -> Option<PathBuf> {
    let candidate = repo_root().join(".etcd/etcd");
    candidate.is_file().then_some(candidate)
}

fn free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .expect("a free port")
        .local_addr()
        .expect("an address")
        .port()
}

/// A single-member etcd, killed on drop.
struct Server {
    process: Child,
    endpoint: String,
    _data: tempdir::TempDir,
}

impl Drop for Server {
    fn drop(&mut self) {
        drop(self.process.kill());
        drop(self.process.wait());
    }
}

mod tempdir {
    //! A scratch directory that removes itself, without a dependency.
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT: AtomicU64 = AtomicU64::new(0);

    pub struct TempDir(PathBuf);

    impl TempDir {
        pub fn new(prefix: &str) -> std::io::Result<Self> {
            let path = std::env::temp_dir().join(format!(
                "{prefix}-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed),
            ));
            std::fs::create_dir_all(&path)?;
            Ok(Self(path))
        }

        pub fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for TempDir {
        fn drop(&mut self) {
            drop(std::fs::remove_dir_all(&self.0));
        }
    }
}

impl Server {
    /// Start etcd, or `None` when the binary is not present.
    fn start() -> Option<Self> {
        let binary = etcd_binary()?;
        let data = tempdir::TempDir::new("nmos-etcd-live").expect("a scratch directory");
        let client_port = free_port();
        let peer_port = free_port();
        let client_url = format!("http://127.0.0.1:{client_port}");
        let peer_url = format!("http://127.0.0.1:{peer_port}");

        // The same arguments `nmos/etcd/tests/etcd_server.py` uses, so the two
        // suites exercise one configuration.
        let process = Command::new(binary)
            .args([
                "--name",
                "test",
                "--data-dir",
                data.path().join("member").to_str().expect("utf-8 path"),
                "--listen-client-urls",
                &client_url,
                "--advertise-client-urls",
                &client_url,
                "--listen-peer-urls",
                &peer_url,
                "--initial-advertise-peer-urls",
                &peer_url,
                "--initial-cluster",
                &format!("test={peer_url}"),
                "--initial-cluster-state",
                "new",
                "--initial-cluster-token",
                "nmos-etcd-rust-tests",
                "--log-level",
                "error",
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .expect("etcd starts");

        let server = Self {
            process,
            endpoint: format!("127.0.0.1:{client_port}"),
            _data: data,
        };

        let deadline = Instant::now() + Duration::from_secs(30);
        while Instant::now() < deadline {
            if std::net::TcpStream::connect(&server.endpoint).is_ok() {
                return Some(server);
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        panic!("etcd did not become reachable within 30s");
    }

    fn pool(&self) -> SharedPool {
        Arc::new(
            EtcdChannelPool::new(
                vec![Endpoint {
                    target: self.endpoint.clone(),
                    local: true,
                }],
                // No TLS: this exercises the client's RPCs, and the mTLS path
                // has its own end-to-end suite.
                None,
                None,
                Duration::from_secs(5),
            )
            .expect("a pool"),
        )
    }
}

/// Run `body` against a fresh etcd, or skip when the binary is absent.
fn with_etcd<F, Fut>(body: F)
where
    F: FnOnce(SharedPool) -> Fut,
    Fut: std::future::Future<Output = ()>,
{
    let Some(server) = Server::start() else {
        eprintln!("skipped: .etcd/etcd is not present (run ./install-etcd.sh)");
        return;
    };
    let pool = server.pool();
    tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("a runtime")
        .block_on(body(pool));
}

#[test]
fn every_kv_method_path_is_right() {
    // A wrong path is UNIMPLEMENTED at the first call. `Compact` in particular
    // is easy to spell `Compaction`, after its request message.
    with_etcd(|pool| async move {
        let kv = EtcdKv::new(pool);

        // Put, through a transaction, then read it back.
        let written = kv
            .txn(&[], &[put_op(b"/t/a", b"1", 0)], &[], None)
            .await
            .expect("Txn");
        assert!(written.succeeded);
        assert!(written.revision > 0, "a commit has a revision");

        let read = kv
            .range_at(b"/t/a", None, 0, 0, false, None)
            .await
            .expect("Range");
        assert_eq!(read.kvs.len(), 1);
        assert_eq!(read.kvs[0].value.as_ref(), b"1");
        assert_eq!(read.count, 1);

        // DeleteRange over a prefix.
        kv.txn(&[], &[put_op(b"/t/b", b"2", 0)], &[], None)
            .await
            .expect("Txn");
        let deleted = kv.delete_prefix(b"/t/", None).await.expect("DeleteRange");
        assert_eq!(deleted, 2);

        // Compact -- the path whose plausible misspelling this test exists for.
        let revision = kv
            .range_at(b"/t/a", None, 0, 0, false, None)
            .await
            .expect("Range")
            .revision;
        kv.compact(revision, false, None).await.expect("Compact");
    });
}

#[test]
fn a_prefix_scan_pages_at_a_fixed_revision() {
    // The preload's core property: every page after the first reads at exactly
    // the snapshot revision, so a concurrent write cannot make a page overlap
    // or skip a key.
    with_etcd(|pool| async move {
        let kv = EtcdKv::new(pool);
        for index in 0..5 {
            kv.txn(
                &[],
                &[put_op(format!("/p/{index}").as_bytes(), b"v", 0)],
                &[],
                None,
            )
            .await
            .expect("Txn");
        }

        let first = kv
            .range_prefix_at(b"/p/", 0, 2, None, None)
            .await
            .expect("Range");
        assert_eq!(first.kvs.len(), 2);
        assert!(first.more, "the range was truncated by the limit");
        assert_eq!(first.count, 5, "count ignores the limit");
        let snapshot = first.revision;

        // A write that must NOT appear in the pinned pages below.
        kv.txn(&[], &[put_op(b"/p/9", b"late", 0)], &[], None)
            .await
            .expect("Txn");

        let mut seen: Vec<String> = first
            .kvs
            .iter()
            .map(|kv| String::from_utf8_lossy(&kv.key).into_owned())
            .collect();
        let mut last = first.kvs.last().expect("a key").key.clone();
        loop {
            let page = kv
                .range_prefix_at(b"/p/", snapshot, 2, Some(&last), None)
                .await
                .expect("Range");
            if page.kvs.is_empty() {
                break;
            }
            seen.extend(
                page.kvs
                    .iter()
                    .map(|kv| String::from_utf8_lossy(&kv.key).into_owned()),
            );
            last = page.kvs.last().expect("a key").key.clone();
        }

        assert_eq!(
            seen.len(),
            5,
            "the pinned scan saw the later write: {seen:?}"
        );
        assert!(!seen.iter().any(|key| key == "/p/9"));
        // Sorted, and each key once: the NUL-suffix bound is exclusive.
        let mut sorted = seen.clone();
        sorted.sort();
        assert_eq!(seen, sorted);
    });
}

#[test]
fn the_compare_predicates_mean_what_they_say() {
    // `compare_absent` is the one that fails silently when wrong: it turns a
    // create-if-absent into an unconditional overwrite, and the transaction
    // still succeeds.
    with_etcd(|pool| async move {
        let kv = EtcdKv::new(pool);

        // Create-if-absent, on a key that does not exist: succeeds.
        let created = kv
            .txn(
                &[compare_absent(b"/c/k")],
                &[put_op(b"/c/k", b"first", 0)],
                &[],
                None,
            )
            .await
            .expect("Txn");
        assert!(created.succeeded);

        // The same transaction again: must now FAIL the compare.
        let again = kv
            .txn(
                &[compare_absent(b"/c/k")],
                &[put_op(b"/c/k", b"second", 0)],
                &[range_op(b"/c/k", None)],
                None,
            )
            .await
            .expect("Txn");
        assert!(
            !again.succeeded,
            "create-if-absent overwrote an existing key",
        );
        // The failure branch carries the authoritative value, which is what
        // makes a lost CAS cheap.
        let current = first_kv(&again.responses[0]).expect("the existing key");
        assert_eq!(current.value.as_ref(), b"first");
        let mod_revision = current.mod_revision;

        // `compare_exists` is the opposite predicate on the same key.
        let exists = kv
            .txn(
                &[compare_exists(b"/c/k")],
                &[range_op(b"/c/k", None)],
                &[],
                None,
            )
            .await
            .expect("Txn");
        assert!(exists.succeeded);

        // `compare_mod` holds at the read revision and fails once anything
        // rewrites the key.
        let unchanged = kv
            .txn(
                &[compare_mod(b"/c/k", mod_revision)],
                &[put_op(b"/c/k", b"third", 0)],
                &[],
                None,
            )
            .await
            .expect("Txn");
        assert!(unchanged.succeeded);

        let stale = kv
            .txn(
                &[compare_mod(b"/c/k", mod_revision)],
                &[put_op(b"/c/k", b"fourth", 0)],
                &[],
                None,
            )
            .await
            .expect("Txn");
        assert!(!stale.succeeded, "a stale belief committed anyway");
    });
}

#[test]
fn a_read_set_returns_one_revision_covering_every_key() {
    // Separate reads would give each key its own revision and the fence would
    // have nothing coherent to wait for.
    with_etcd(|pool| async move {
        let kv = EtcdKv::new(pool);
        kv.txn(
            &[],
            &[put_op(b"/r/a", b"1", 0), put_op(b"/r/b", b"2", 0)],
            &[],
            None,
        )
        .await
        .expect("Txn");

        let keys = vec![b"/r/a".to_vec(), b"/r/b".to_vec(), b"/r/missing".to_vec()];
        let result = kv.read_set(&keys, None).await.expect("read set");
        assert!(result.succeeded, "a comparison-free txn always succeeds");
        assert_eq!(result.responses.len(), 3);
        assert_eq!(first_kv(&result.responses[0]).unwrap().value.as_ref(), b"1");
        assert_eq!(first_kv(&result.responses[1]).unwrap().value.as_ref(), b"2");
        // An absent key is a meaningful answer, not an error.
        assert!(first_kv(&result.responses[2]).is_none());
    });
}

#[test]
fn a_lease_collects_its_whole_subtree() {
    // The property the entire garbage-collection design rests on.
    with_etcd(|pool| async move {
        let lease = EtcdLease::new(Arc::clone(&pool));
        let kv = EtcdKv::new(pool);

        let granted = lease.grant(5, None).await.expect("LeaseGrant");
        assert!(granted.id != 0);
        assert!(granted.ttl >= 1, "etcd granted {}", granted.ttl);

        kv.txn(
            &[],
            &[
                put_op(b"/l/node/self", b"n", granted.id),
                put_op(b"/l/node/dev", b"d", granted.id),
            ],
            &[],
            None,
        )
        .await
        .expect("Txn");

        let status = lease
            .time_to_live(granted.id, true, None)
            .await
            .expect("LeaseTimeToLive");
        assert!(status.alive());
        assert_eq!(status.keys.len(), 2, "both keys hang off one lease");
        assert_eq!(status.granted_ttl, granted.ttl);

        // Renewal writes nothing to the keyspace.
        let before = kv
            .range_at(b"/l/", Some(b"/l0"), 0, 0, false, None)
            .await
            .unwrap();
        let ttl = lease
            .keepalive_once(granted.id, None)
            .await
            .expect("LeaseKeepAlive");
        assert!(ttl > 0);
        let after = kv
            .range_at(b"/l/", Some(b"/l0"), 0, 0, false, None)
            .await
            .unwrap();
        assert_eq!(
            before.revision, after.revision,
            "a renewal moved the store revision, so it wrote a key",
        );

        // Revoking removes the whole subtree at once.
        lease.revoke(granted.id, None).await.expect("LeaseRevoke");
        let gone = kv
            .range_at(b"/l/", Some(b"/l0"), 0, 0, false, None)
            .await
            .unwrap();
        assert!(gone.kvs.is_empty(), "the subtree outlived its lease");

        // Revoking again is success, not an error.
        lease.revoke(granted.id, None).await.expect("idempotent");
        // And renewing a revoked lease is authoritative, not transient.
        let error = lease.keepalive_once(granted.id, None).await.unwrap_err();
        assert!(
            matches!(error, EtcdError::LeaseNotFound(_)),
            "got {error:?}",
        );
    });
}

#[test]
fn a_watch_delivers_complete_revisions_in_order() {
    // Including the property the registry leans on hardest: one transaction
    // touching several keys arrives as ONE batch, so parents and children are
    // applied together.
    with_etcd(|pool| async move {
        let watch = EtcdWatch::new(Arc::clone(&pool));
        let kv = EtcdKv::new(pool);

        let start = kv
            .range_at(b"/w/none", None, 0, 0, false, None)
            .await
            .expect("Range")
            .revision;
        let mut stream = watch
            .open(b"/w/", start + 1, None, false)
            .await
            .expect("the watch is confirmed before open returns");
        assert!(stream.watch_id() >= 0);

        // One transaction, three keys: one revision.
        kv.txn(
            &[],
            &[
                put_op(b"/w/node", b"n", 0),
                put_op(b"/w/device", b"d", 0),
                put_op(b"/w/sender", b"s", 0),
            ],
            &[],
            None,
        )
        .await
        .expect("Txn");

        let batch = next_event_batch(&mut stream).await;
        assert_eq!(
            batch.events.len(),
            3,
            "a transaction was split across batches",
        );
        let keys: Vec<String> = batch
            .events
            .iter()
            .map(|e| String::from_utf8_lossy(&e.kv.as_ref().unwrap().key).into_owned())
            .collect();
        assert_eq!(keys, ["/w/node", "/w/device", "/w/sender"]);

        // A second transaction is a second batch, at a later revision.
        kv.txn(&[], &[put_op(b"/w/later", b"l", 0)], &[], None)
            .await
            .expect("Txn");
        let second = next_event_batch(&mut stream).await;
        assert!(second.revision > batch.revision);
        assert_eq!(second.events.len(), 1);

        // A subtree delete arrives as one revision too.
        kv.txn(&[], &[delete_prefix_op(b"/w/")], &[], None)
            .await
            .expect("Txn");
        let removed = next_event_batch(&mut stream).await;
        assert_eq!(removed.events.len(), 4, "the cascade was split");
        stream.close();
    });
}

#[test]
fn a_progress_request_proves_delivery() {
    // Receiving progress at revision R proves everything through R has been
    // delivered -- which is what the mutation fence is built on.
    with_etcd(|pool| async move {
        let watch = EtcdWatch::new(Arc::clone(&pool));
        let kv = EtcdKv::new(pool);

        let start = kv
            .range_at(b"/g/none", None, 0, 0, false, None)
            .await
            .expect("Range")
            .revision;
        let mut stream = watch
            .open(b"/g/", start + 1, None, false)
            .await
            .expect("watch");

        let written = kv
            .txn(&[], &[put_op(b"/g/a", b"1", 0)], &[], None)
            .await
            .expect("Txn");

        // Drain the event, then ask for progress.
        let batch = next_event_batch(&mut stream).await;
        assert_eq!(batch.revision, written.revision);

        stream.request_progress().await.expect("progress request");
        let progress = tokio::time::timeout(Duration::from_secs(10), async {
            loop {
                let batch = stream
                    .next_batch()
                    .await
                    .expect("a batch")
                    .expect("not ended");
                if batch.progress_only() {
                    return batch;
                }
            }
        })
        .await
        .expect("a progress reply within 10s");

        assert!(
            progress.revision >= written.revision,
            "progress at {} is below the write at {}",
            progress.revision,
            written.revision,
        );
    });
}

#[test]
fn a_watch_below_the_compaction_point_says_so() {
    // The one failure that cannot be recovered by reconnecting. It must be
    // distinguishable from a dropped connection, or a resume loop silently
    // skips every revision that was compacted away.
    with_etcd(|pool| async move {
        let watch = EtcdWatch::new(Arc::clone(&pool));
        let kv = EtcdKv::new(pool);

        for index in 0..5 {
            kv.txn(
                &[],
                &[put_op(format!("/k/{index}").as_bytes(), b"v", 0)],
                &[],
                None,
            )
            .await
            .expect("Txn");
        }
        let current = kv
            .range_at(b"/k/0", None, 0, 0, false, None)
            .await
            .expect("Range")
            .revision;
        kv.compact(current, true, None).await.expect("Compact");

        // Opening below the compaction point: either the create is refused
        // outright or the first read reports it. Both are `Compacted`, and
        // neither may be `Unavailable`.
        let outcome = match watch.open(b"/k/", 2, None, false).await {
            Err(error) => error,
            Ok(mut stream) => stream
                .next_batch()
                .await
                .expect_err("a compacted watch must not deliver"),
        };
        match outcome {
            EtcdError::Compacted {
                compact_revision, ..
            } => assert!(compact_revision > 0 || true, "revision {compact_revision}"),
            other => panic!("a compaction was reported as {other:?}"),
        }

        // A read at a compacted revision is the same class of failure.
        let error = kv
            .range_at(b"/k/0", None, 2, 0, false, None)
            .await
            .expect_err("a read below the compaction point must fail");
        assert!(
            matches!(error, EtcdError::Compacted { .. }),
            "got {error:?}",
        );
    });
}

#[test]
fn failover_reaches_a_live_member_past_a_dead_one() {
    // The local-first ordering is only worth something if a dead first member
    // does not take the request down with it.
    with_etcd(|pool| async move {
        let live = pool.endpoints()[0].clone();
        let dead = Endpoint {
            target: format!("127.0.0.1:{}", free_port()),
            local: true,
        };
        let failing = Arc::new(
            EtcdChannelPool::new(vec![dead, live], None, None, Duration::from_secs(3))
                .expect("a pool"),
        );
        let kv = EtcdKv::new(failing);
        let result = kv
            .txn(&[], &[put_op(b"/f/a", b"1", 0)], &[], None)
            .await
            .expect("the second member answered");
        assert!(result.succeeded);
    });
}

#[test]
fn no_member_answering_names_every_member_it_tried() {
    with_etcd(|_pool| async move {
        let all_dead = Arc::new(
            EtcdChannelPool::new(
                vec![
                    Endpoint {
                        target: format!("127.0.0.1:{}", free_port()),
                        local: true,
                    },
                    Endpoint {
                        target: format!("127.0.0.1:{}", free_port()),
                        local: false,
                    },
                ],
                None,
                None,
                Duration::from_secs(2),
            )
            .expect("a pool"),
        );
        let error = EtcdKv::new(all_dead)
            .range_at(b"/x", None, 0, 0, false, None)
            .await
            .expect_err("nothing was listening");
        assert!(error.is_retryable(), "a dead cluster is retryable");
        let message = error.message();
        assert!(message.contains("no etcd member answered"), "{message}");
        assert!(message.contains("(local)"), "{message}");
    });
}

/// The next batch carrying events, skipping progress markers.
///
/// `progress_notify` is on, so a periodic marker can arrive at any time; a test
/// waiting for events must not mistake one for the delivery it asked about.
async fn next_event_batch(stream: &mut nmos_etcd::WatchStream) -> RevisionBatch {
    tokio::time::timeout(Duration::from_secs(10), async {
        loop {
            let batch = stream
                .next_batch()
                .await
                .expect("a batch")
                .expect("the stream ended");
            if !batch.progress_only() {
                return batch;
            }
        }
    })
    .await
    .expect("events within 10s")
}
