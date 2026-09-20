// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The KV surface: revision-pinned reads, read sets, CAS transactions, deletes.
//!
//! This is the vocabulary the registry backend is written in. Four operations
//! carry the whole design:
//!
//! [`EtcdKv::range_prefix_at`]
//!   the fixed-revision preload. Every page after the first reads at *exactly*
//!   the snapshot revision, so a concurrent write cannot make a page overlap or
//!   skip a key.
//! [`EtcdKv::read_set`]
//!   the pre-validation read fence. One linearizable transaction covering
//!   target, parent, Node and ID claim, returning both the values and the
//!   revisions the following CAS must compare against.
//! [`EtcdKv::txn`]
//!   every mutation. The compare set is what enforces correctness; the fast
//!   path of the registry backend relies on being able to submit a CAS built
//!   from *believed* revisions and have a stale belief fail the compare rather
//!   than commit something wrong.
//! [`EtcdKv::delete_prefix`]
//!   the subtree cascade.
//!
//! Comparisons and operations are built with the small helpers at the bottom
//! rather than by assembling protobuf by hand at each call site. That is not
//! decoration: [`compare_absent`] in particular encodes the one non-obvious
//! etcd idiom this design leans on -- "this key does not exist" is expressed as
//! *`create_revision == 0`*, not as a missing-key check -- and getting it wrong
//! silently turns a create-if-absent into an unconditional overwrite.
//!
//! Protobuf messages are returned as-is for bulk data ([`KeyValue`]) rather
//! than copied into project types. They are fully typed already, every `bytes`
//! field is a `Bytes` that shares the decoded buffer rather than copying it,
//! and a preload of several thousand resources should not pay for an extra
//! object per key.

use std::time::Duration;

use bytes::Bytes;

use crate::channel::UnaryMethod;
use crate::errors::Result;
use crate::generated::etcdserverpb as pb;
use crate::generated::mvccpb::KeyValue;

/// Declared once, as `const`. A wrong path is `UNIMPLEMENTED` at the first
/// call, which the client's own suite reaches for every one of these.
const RANGE: UnaryMethod = UnaryMethod::new("/etcdserverpb.KV/Range");
const TXN: UnaryMethod = UnaryMethod::new("/etcdserverpb.KV/Txn");
const DELETE_RANGE: UnaryMethod = UnaryMethod::new("/etcdserverpb.KV/DeleteRange");
/// The method is `Compact`, **not** `Compaction` -- the request message is
/// `CompactionRequest`, which makes the wrong spelling look entirely plausible.
/// This is the exact hazard the Python resolves through the proto descriptor.
const COMPACT: UnaryMethod = UnaryMethod::new("/etcdserverpb.KV/Compact");

/// "Read the whole keyspace" is spelled with a single NUL for both ends.
const ALL_KEYS: &[u8] = b"\0";

// ---------------------------------------------------------------------------
// Key ranges
// ---------------------------------------------------------------------------

/// The exclusive upper bound covering every key under `prefix`.
///
/// etcd has no prefix operator; a prefix scan is a range whose end is the
/// prefix with its last non-`0xFF` byte incremented. Implemented here rather
/// than borrowed so the one edge case is explicit: a prefix that is entirely
/// `0xFF` (or empty) has no such successor, and the range must instead run to
/// the end of the keyspace, which etcd spells as a single NUL byte.
#[must_use]
pub fn prefix_range_end(prefix: &[u8]) -> Vec<u8> {
    let mut end = prefix.to_vec();
    // Walked with `iter_mut().rev()` rather than by index: the panic-free
    // lints this workspace denies forbid `end[index]`, and reaching for an
    // allowance here would be spending the guarantee on something that does
    // not need it.
    for (offset, byte) in end.iter_mut().enumerate().rev() {
        if *byte < 0xFF {
            *byte = byte.saturating_add(1);
            let keep = offset.saturating_add(1);
            end.truncate(keep);
            return end;
        }
    }
    ALL_KEYS.to_vec()
}

// ---------------------------------------------------------------------------
// Results
// ---------------------------------------------------------------------------

/// One page of a range read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RangeResult {
    /// The keys read.
    pub kvs: Vec<KeyValue>,
    /// Store revision the response was served at.
    ///
    /// On the first preload page this becomes the snapshot revision every
    /// later page is pinned to.
    pub revision: i64,
    /// True when the range was truncated by `limit`; page again from the last
    /// key plus a NUL byte.
    pub more: bool,
    /// Total keys in the range, ignoring `limit`.
    pub count: i64,
}

/// Outcome of a transaction.
///
/// `PartialEq` but not `Eq`: `ResponseOp` carries every response shape etcd
/// can return, and prost does not derive `Eq` for it.
#[derive(Debug, Clone, PartialEq)]
pub struct TxnResult {
    /// True when every comparison held and the success branch ran.
    pub succeeded: bool,
    /// Revision the transaction committed at -- the revision the post-commit
    /// application fence waits for.
    ///
    /// Also set when `succeeded` is false, in which case it is simply the
    /// revision the failed compare was evaluated at.
    pub revision: i64,
    /// Results of whichever branch ran.
    ///
    /// The failure branch is what makes a lost CAS cheap: it carries the
    /// authoritative values, so a retry needs no second round trip.
    pub responses: Vec<pb::ResponseOp>,
}

// ---------------------------------------------------------------------------
// KV client
// ---------------------------------------------------------------------------

/// Typed KV operations over a channel pool.
#[derive(Debug, Clone)]
pub struct EtcdKv {
    pool: crate::channel::SharedPool,
}

impl EtcdKv {
    /// Wrap a pool.
    #[must_use]
    pub const fn new(pool: crate::channel::SharedPool) -> Self {
        Self { pool }
    }

    /// Read a key or range.
    ///
    /// Always linearizable: `serializable` is left false so the read goes
    /// through the leader. A serializable read is faster and would be tempting
    /// for Query, but Query never reads etcd at all -- every read here is
    /// either building the authoritative snapshot or feeding a fence, and both
    /// are exactly the places where a stale local read would be wrong.
    ///
    /// `revision` pins the read to a store revision; zero reads the latest,
    /// and a revision that has been compacted away fails with
    /// [`crate::EtcdError::Compacted`].
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified.
    pub async fn range_at(
        &self,
        key: &[u8],
        range_end: Option<&[u8]>,
        revision: i64,
        limit: i64,
        sort_by_key: bool,
        timeout: Option<Duration>,
    ) -> Result<RangeResult> {
        let mut request = pb::RangeRequest {
            key: Bytes::copy_from_slice(key),
            range_end: range_end.map_or_else(Bytes::new, Bytes::copy_from_slice),
            revision,
            limit,
            serializable: false,
            ..Default::default()
        };
        if sort_by_key {
            request.sort_order = pb::range_request::SortOrder::Ascend as i32;
            request.sort_target = pb::range_request::SortTarget::Key as i32;
        }

        let response: pb::RangeResponse = self.pool.call(RANGE, request, timeout).await?;
        Ok(RangeResult {
            kvs: response.kvs,
            revision: response.header.map_or(0, |header| header.revision),
            more: response.more,
            count: response.count,
        })
    }

    /// Read one sorted page of everything under `prefix`.
    ///
    /// `start_after` resumes paging above a key: the caller passes the last key
    /// of the previous page, and a NUL byte is appended here to make the bound
    /// exclusive. That is the standard etcd paging idiom, and it avoids reading
    /// the boundary key into the snapshot twice.
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified.
    pub async fn range_prefix_at(
        &self,
        prefix: &[u8],
        revision: i64,
        limit: i64,
        start_after: Option<&[u8]>,
        timeout: Option<Duration>,
    ) -> Result<RangeResult> {
        let start;
        let key = match start_after {
            None => prefix,
            Some(last) => {
                start = [last, b"\0"].concat();
                &start
            }
        };
        self.range_at(
            key,
            Some(&prefix_range_end(prefix)),
            revision,
            limit,
            true,
            timeout,
        )
        .await
    }

    /// Read several keys atomically at one revision.
    ///
    /// A transaction with no comparisons: the success branch always runs, so
    /// this is a multi-key linearizable read that returns a single revision
    /// covering all of them. Issuing separate reads instead would give each key
    /// its own revision, and the fence would have nothing coherent to wait for.
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified.
    pub async fn read_set(&self, keys: &[Vec<u8>], timeout: Option<Duration>) -> Result<TxnResult> {
        let success: Vec<pb::RequestOp> = keys.iter().map(|key| range_op(key, None)).collect();
        self.txn(&[], &success, &[], timeout).await
    }

    /// Run a compare-and-swap transaction.
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified.
    pub async fn txn(
        &self,
        compare: &[pb::Compare],
        success: &[pb::RequestOp],
        failure: &[pb::RequestOp],
        timeout: Option<Duration>,
    ) -> Result<TxnResult> {
        let request = pb::TxnRequest {
            compare: compare.to_vec(),
            success: success.to_vec(),
            failure: failure.to_vec(),
        };
        let response: pb::TxnResponse = self.pool.call(TXN, request, timeout).await?;
        Ok(TxnResult {
            succeeded: response.succeeded,
            revision: response.header.map_or(0, |header| header.revision),
            responses: response.responses,
        })
    }

    /// Delete every key under `prefix`. Returns how many were removed.
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified.
    pub async fn delete_prefix(&self, prefix: &[u8], timeout: Option<Duration>) -> Result<i64> {
        let request = pb::DeleteRangeRequest {
            key: Bytes::copy_from_slice(prefix),
            range_end: Bytes::from(prefix_range_end(prefix)),
            prev_kv: false,
        };
        let response: pb::DeleteRangeResponse =
            self.pool.call(DELETE_RANGE, request, timeout).await?;
        Ok(response.deleted)
    }

    /// Discard history below `revision`.
    ///
    /// Not used on any registry path -- the registry is a *victim* of
    /// compaction, not a driver of it, and reacts by resnapshotting. Provided
    /// because the test suite has to be able to force the compaction-recovery
    /// path deliberately rather than wait for a real one.
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified.
    pub async fn compact(
        &self,
        revision: i64,
        physical: bool,
        timeout: Option<Duration>,
    ) -> Result<()> {
        let request = pb::CompactionRequest { revision, physical };
        let _: pb::CompactionResponse = self.pool.call(COMPACT, request, timeout).await?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Comparison and operation builders
// ---------------------------------------------------------------------------

/// "This key was last modified at exactly `mod_revision`".
///
/// The workhorse of the CAS path: it fails if anyone changed the key since it
/// was read or since the local watch last applied it, which is what makes a
/// speculative transaction safe to submit from a possibly-stale belief.
#[must_use]
pub fn compare_mod(key: &[u8], mod_revision: i64) -> pb::Compare {
    pb::Compare {
        result: pb::compare::CompareResult::Equal as i32,
        target: pb::compare::CompareTarget::Mod as i32,
        key: Bytes::copy_from_slice(key),
        range_end: Bytes::new(),
        target_union: Some(pb::compare::TargetUnion::ModRevision(mod_revision)),
    }
}

/// "This key was created at exactly `create_revision`".
///
/// Creation revision rather than modification revision on purpose: a parent
/// being *updated* concurrently must not invalidate a child's registration,
/// but a parent being deleted and recreated must, and only the creation
/// revision distinguishes those.
#[must_use]
pub fn compare_create(key: &[u8], create_revision: i64) -> pb::Compare {
    pb::Compare {
        result: pb::compare::CompareResult::Equal as i32,
        target: pb::compare::CompareTarget::Create as i32,
        key: Bytes::copy_from_slice(key),
        range_end: Bytes::new(),
        target_union: Some(pb::compare::TargetUnion::CreateRevision(create_revision)),
    }
}

/// "This key exists", however many times it has been rewritten.
///
/// Used for parent and Node keys on the registration path. Existence rather
/// than a specific revision is the right predicate there: a parent being
/// *updated* concurrently must not invalidate a child's registration, and a
/// parent being deleted and recreated is indistinguishable from a re-register,
/// which the store already treats as valid.
///
/// The dangerous case -- a parent deleted while a child is being written -- is
/// covered by the child's own compare instead: a Node delete ranges over the
/// whole subtree, so the child key goes with it and its [`compare_mod`] fails.
#[must_use]
pub fn compare_exists(key: &[u8]) -> pb::Compare {
    pb::Compare {
        result: pb::compare::CompareResult::Greater as i32,
        target: pb::compare::CompareTarget::Create as i32,
        key: Bytes::copy_from_slice(key),
        range_end: Bytes::new(),
        target_union: Some(pb::compare::TargetUnion::CreateRevision(0)),
    }
}

/// "This key does not exist".
///
/// etcd has no absence predicate; absence is *`create_revision == 0`*, because
/// a key that has never existed has no creation revision. This is the idiom
/// behind create-if-absent and behind reclaiming a stale ID claim only once the
/// resource it points at is really gone.
#[must_use]
pub fn compare_absent(key: &[u8]) -> pb::Compare {
    compare_create(key, 0)
}

/// Write a key, optionally attached to a lease.
///
/// Attaching to a Node's lease is the whole of distributed garbage collection:
/// when the Node stops heartbeating, etcd removes every key on that lease
/// without anyone running a collection pass.
#[must_use]
pub fn put_op(key: &[u8], value: &[u8], lease: i64) -> pb::RequestOp {
    pb::RequestOp {
        request: Some(pb::request_op::Request::RequestPut(pb::PutRequest {
            key: Bytes::copy_from_slice(key),
            value: Bytes::copy_from_slice(value),
            lease,
            ..Default::default()
        })),
    }
}

/// Read a key or range inside a transaction.
#[must_use]
pub fn range_op(key: &[u8], range_end: Option<&[u8]>) -> pb::RequestOp {
    pb::RequestOp {
        request: Some(pb::request_op::Request::RequestRange(pb::RangeRequest {
            key: Bytes::copy_from_slice(key),
            range_end: range_end.map_or_else(Bytes::new, Bytes::copy_from_slice),
            ..Default::default()
        })),
    }
}

/// Delete a key or range inside a transaction.
#[must_use]
pub fn delete_op(key: &[u8], range_end: Option<&[u8]>) -> pb::RequestOp {
    pb::RequestOp {
        request: Some(pb::request_op::Request::RequestDeleteRange(
            pb::DeleteRangeRequest {
                key: Bytes::copy_from_slice(key),
                range_end: range_end.map_or_else(Bytes::new, Bytes::copy_from_slice),
                prev_kv: false,
            },
        )),
    }
}

/// Delete a whole subtree inside a transaction.
#[must_use]
pub fn delete_prefix_op(prefix: &[u8]) -> pb::RequestOp {
    delete_op(prefix, Some(&prefix_range_end(prefix)))
}

/// The single `KeyValue` from a range response op, or `None` if absent.
///
/// Reading a transaction's results positionally is easy to get subtly wrong --
/// an empty `kvs` means the key does not exist, which is a meaningful answer
/// and not an error -- so the unwrapping lives here instead of at every call
/// site.
#[must_use]
pub fn first_kv(response: &pb::ResponseOp) -> Option<&KeyValue> {
    match response.response.as_ref()? {
        pb::response_op::Response::ResponseRange(range) => range.kvs.first(),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_prefix_scan_ends_at_the_incremented_last_byte() {
        assert_eq!(prefix_range_end(b"/nmos/nodes/"), b"/nmos/nodes0".to_vec());
        assert_eq!(prefix_range_end(b"a"), b"b".to_vec());
        // The increment truncates: everything after the bumped byte is gone,
        // because `ab` -> `ac` already excludes every key under `ab`.
        assert_eq!(prefix_range_end(b"ab"), b"ac".to_vec());
    }

    #[test]
    fn a_prefix_with_no_successor_runs_to_the_end_of_the_keyspace() {
        // The one edge case, and the reason this is written out rather than
        // borrowed: there is no key above `0xFF...`, so the range must be
        // open-ended, which etcd spells as a single NUL.
        assert_eq!(prefix_range_end(b"\xff"), b"\0".to_vec());
        assert_eq!(prefix_range_end(b"\xff\xff"), b"\0".to_vec());
        assert_eq!(prefix_range_end(b""), b"\0".to_vec());
    }

    #[test]
    fn a_trailing_ff_is_carried_rather_than_wrapped() {
        // `a\xff` has successor `b`, not `a\x00`: the last byte that *can* be
        // incremented is, and the tail is dropped.
        assert_eq!(prefix_range_end(b"a\xff"), b"b".to_vec());
        assert_eq!(prefix_range_end(b"a\xff\xff"), b"b".to_vec());
    }

    #[test]
    fn absence_is_a_creation_revision_of_zero() {
        // The non-obvious idiom the whole create-if-absent path rests on.
        // Getting this wrong turns a create into an unconditional overwrite,
        // and nothing about the resulting transaction looks wrong.
        let compare = compare_absent(b"/nmos/ids/n1");
        assert_eq!(compare.result, pb::compare::CompareResult::Equal as i32);
        assert_eq!(compare.target, pb::compare::CompareTarget::Create as i32);
        assert_eq!(
            compare.target_union,
            Some(pb::compare::TargetUnion::CreateRevision(0)),
        );
    }

    #[test]
    fn existence_is_a_creation_revision_above_zero() {
        // `GREATER`, where absence is `EQUAL`. One enum apart, and the two
        // predicates are opposites.
        let compare = compare_exists(b"/nmos/nodes/n1/self");
        assert_eq!(compare.result, pb::compare::CompareResult::Greater as i32);
        assert_eq!(compare.target, pb::compare::CompareTarget::Create as i32);
        assert_eq!(
            compare.target_union,
            Some(pb::compare::TargetUnion::CreateRevision(0)),
        );
    }

    #[test]
    fn the_two_revision_compares_target_different_fields() {
        // `mod` fails when anything rewrites the key; `create` survives a
        // rewrite and fails only on delete-and-recreate. Swapping them
        // compiles and is wrong in a way no test of the happy path notices.
        let modified = compare_mod(b"k", 7);
        assert_eq!(modified.target, pb::compare::CompareTarget::Mod as i32);
        assert_eq!(
            modified.target_union,
            Some(pb::compare::TargetUnion::ModRevision(7)),
        );

        let created = compare_create(b"k", 7);
        assert_eq!(created.target, pb::compare::CompareTarget::Create as i32);
        assert_eq!(
            created.target_union,
            Some(pb::compare::TargetUnion::CreateRevision(7)),
        );
    }

    #[test]
    fn a_put_carries_its_lease() {
        // The lease is the whole of distributed garbage collection; a put that
        // lost it leaves a key nothing will ever collect.
        let op = put_op(b"k", b"v", 0x0BAD_C0DE);
        let Some(pb::request_op::Request::RequestPut(put)) = op.request else {
            panic!("not a put");
        };
        assert_eq!(put.lease, 0x0BAD_C0DE);
        assert_eq!(put.key.as_ref(), b"k");
        assert_eq!(put.value.as_ref(), b"v");
    }

    #[test]
    fn a_subtree_delete_ranges_over_the_prefix() {
        let op = delete_prefix_op(b"/nmos/nodes/n1/");
        let Some(pb::request_op::Request::RequestDeleteRange(delete)) = op.request else {
            panic!("not a delete");
        };
        assert_eq!(delete.key.as_ref(), b"/nmos/nodes/n1/");
        assert_eq!(delete.range_end.as_ref(), b"/nmos/nodes/n10");
    }

    #[test]
    fn an_absent_key_reads_as_none_rather_than_an_error() {
        // A transaction's range op returning nothing is a meaningful answer --
        // "this key does not exist" -- and every call site would otherwise
        // have to know that.
        let empty = pb::ResponseOp {
            response: Some(pb::response_op::Response::ResponseRange(
                pb::RangeResponse::default(),
            )),
        };
        assert!(first_kv(&empty).is_none());

        let present = pb::ResponseOp {
            response: Some(pb::response_op::Response::ResponseRange(
                pb::RangeResponse {
                    kvs: vec![KeyValue {
                        key: Bytes::from_static(b"k"),
                        mod_revision: 5,
                        ..Default::default()
                    }],
                    ..Default::default()
                },
            )),
        };
        assert_eq!(first_kv(&present).map(|kv| kv.mod_revision), Some(5));

        // A response op of the wrong kind is not a key that exists.
        let wrong = pb::ResponseOp {
            response: Some(pb::response_op::Response::ResponsePut(
                pb::PutResponse::default(),
            )),
        };
        assert!(first_kv(&wrong).is_none());
        assert!(first_kv(&pb::ResponseOp { response: None }).is_none());
    }
}
