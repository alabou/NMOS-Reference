// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Leases: the whole of distributed garbage collection.
//!
//! One lease per Node, with every key belonging to that Node -- its own record,
//! its Devices, their Sources/Flows/Senders/Receivers, and all the ID claims --
//! attached to it. A heartbeat renews the lease; silence lets it expire; etcd
//! then removes the entire subtree atomically on every member at once. No
//! registry runs a collection pass, and no registry can disagree with another
//! about whether a Node is alive.
//!
//! # Why nothing is written per heartbeat
//!
//! The obvious implementation writes a health key on each beat. The legacy
//! dRDS did exactly that (`/health_nodes/<id>`) and every member watched it, so
//! 100 Nodes beating at the 5 s default produced 100 Raft writes per second
//! fanning out to 500 watch events per second across a 5-member cluster -- to
//! record liveness the lease already records, and records *more* reliably,
//! since a lease cannot be renewed by a member that has lost quorum.
//!
//! Renewal here writes nothing to the keyspace. It is a lease refresh, which is
//! cheap and does not wake a single watcher. That is the largest single
//! efficiency difference between this design and the reference it replaces.
//!
//! # Renewal shape
//!
//! etcd exposes renewal only as a bidirectional stream, but one renewal is
//! logically a unary call, so [`EtcdLease::keepalive_once`] opens a stream,
//! sends once, reads once and closes. That is what etcd's own client library
//! does for `KeepAliveOnce`, and on an established HTTP/2 channel it is one
//! round trip with no new connection.
//!
//! A persistent multiplexed keepalive stream shared by every Node would save
//! that stream setup. It is deliberately *not* done here: it adds
//! response-routing and reconnect state, and whether it is worth that is a
//! question for the heartbeat benchmark, not for a guess made while writing
//! the client.

use std::time::Duration;

use crate::channel::{SharedPool, StreamMethod, UnaryMethod};
use crate::errors::{EtcdError, Result};
use crate::generated::etcdserverpb as pb;

const GRANT: UnaryMethod = UnaryMethod::new("/etcdserverpb.Lease/LeaseGrant");
const REVOKE: UnaryMethod = UnaryMethod::new("/etcdserverpb.Lease/LeaseRevoke");
const TIME_TO_LIVE: UnaryMethod = UnaryMethod::new("/etcdserverpb.Lease/LeaseTimeToLive");
const KEEP_ALIVE: StreamMethod = StreamMethod::new("/etcdserverpb.Lease/LeaseKeepAlive");

/// A granted lease.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Lease {
    /// The lease id keys attach to.
    pub id: i64,
    /// Seconds etcd actually granted.
    ///
    /// May exceed the requested TTL -- etcd enforces a minimum. Callers must
    /// renew against *this*, not against what they asked for.
    pub ttl: i64,
}

/// What etcd currently knows about a lease.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LeaseStatus {
    /// The lease id.
    pub id: i64,
    /// The TTL it was granted with.
    pub granted_ttl: i64,
    /// Seconds left before expiry. Negative when the lease is already gone.
    pub remaining_ttl: i64,
    /// Keys attached to the lease, when they were requested.
    ///
    /// Used by the test suite to assert that a Node's whole subtree really is
    /// on one lease -- the property the entire GC design rests on.
    pub keys: Vec<Vec<u8>>,
}

impl LeaseStatus {
    /// Whether the lease has not yet expired.
    #[must_use]
    pub const fn alive(&self) -> bool {
        self.remaining_ttl >= 0
    }
}

/// Lease operations over a channel pool.
#[derive(Debug, Clone)]
pub struct EtcdLease {
    pool: SharedPool,
}

impl EtcdLease {
    /// Wrap a pool.
    #[must_use]
    pub const fn new(pool: SharedPool) -> Self {
        Self { pool }
    }

    /// Create a lease.
    ///
    /// The registry passes `ceil(--garbageCollectionInterval)` -- 12 s by
    /// default, the interval of `Behaviour - Registration.md:47`, and
    /// deliberately not the 15 s the legacy dRDS used against the same 12 s
    /// registry interval, which left a collected Node alive in etcd for
    /// several seconds after the registry had already dropped it.
    ///
    /// # Errors
    ///
    /// `EtcdError::LeaseNotFound` when etcd reports a grant failure in-band,
    /// which it does rather than as an RPC error.
    pub async fn grant(&self, ttl_seconds: i64, timeout: Option<Duration>) -> Result<Lease> {
        let request = pb::LeaseGrantRequest {
            ttl: ttl_seconds,
            id: 0,
        };
        let response: pb::LeaseGrantResponse = self.pool.call(GRANT, request, timeout).await?;
        // etcd reports a grant failure in-band rather than as an RPC error.
        if !response.error.is_empty() {
            return Err(EtcdError::LeaseNotFound(format!(
                "lease grant failed: {}",
                response.error,
            )));
        }
        Ok(Lease {
            id: response.id,
            ttl: response.ttl,
        })
    }

    /// Renew a lease. Returns the new TTL in seconds.
    ///
    /// # Errors
    ///
    /// `EtcdError::LeaseNotFound` when the lease has expired or been revoked.
    /// **This is authoritative, not transient**: the Node is gone as far as the
    /// cluster is concerned, so the heartbeat handler answers 404 and the Node
    /// re-registers everything in order, exactly as
    /// `Behaviour - Registration.md:112-114` prescribes.
    pub async fn keepalive_once(&self, lease_id: i64, timeout: Option<Duration>) -> Result<i64> {
        let request = pb::LeaseKeepAliveRequest { id: lease_id };
        let response: pb::LeaseKeepAliveResponse = self
            .pool
            .call_stream_once(KEEP_ALIVE, request, timeout)
            .await?;
        // A renewal for a dead lease is not an RPC error -- etcd answers with
        // TTL 0. Treating that as success would keep a Node the cluster has
        // already collected alive in the local view forever.
        if response.ttl <= 0 {
            return Err(EtcdError::LeaseNotFound(format!(
                "lease {lease_id:x} has expired or been revoked",
            )));
        }
        Ok(response.ttl)
    }

    /// Inspect a lease without renewing it.
    ///
    /// Used by the debug `GET /health/nodes/{id}` route, which must not refresh
    /// anything: a diagnostic read that silently kept a Node alive would mask
    /// exactly the garbage-collection problem someone would be using it to
    /// investigate.
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified.
    pub async fn time_to_live(
        &self,
        lease_id: i64,
        with_keys: bool,
        timeout: Option<Duration>,
    ) -> Result<LeaseStatus> {
        let request = pb::LeaseTimeToLiveRequest {
            id: lease_id,
            keys: with_keys,
        };
        let response: pb::LeaseTimeToLiveResponse =
            self.pool.call(TIME_TO_LIVE, request, timeout).await?;
        Ok(LeaseStatus {
            id: response.id,
            granted_ttl: response.granted_ttl,
            remaining_ttl: response.ttl,
            keys: response.keys.into_iter().map(|key| key.to_vec()).collect(),
        })
    }

    /// Revoke a lease, deleting every key attached to it.
    ///
    /// Idempotent by design: revoking an already-gone lease is success, not an
    /// error. Node deletion revokes the emptied lease best-effort *after* the
    /// subtree transaction, and racing that against natural expiry is entirely
    /// normal -- both outcomes leave the cluster in the state the caller wanted.
    ///
    /// # Errors
    ///
    /// Whatever the cluster answered, classified, except a missing lease.
    pub async fn revoke(&self, lease_id: i64, timeout: Option<Duration>) -> Result<()> {
        let request = pb::LeaseRevokeRequest { id: lease_id };
        match self
            .pool
            .call::<_, pb::LeaseRevokeResponse>(REVOKE, request, timeout)
            .await
        {
            Ok(_) | Err(EtcdError::LeaseNotFound(_)) => Ok(()),
            Err(other) => Err(other),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_lease_is_alive_until_its_remaining_ttl_goes_negative() {
        // Zero is alive: etcd reports the final second as 0, not as gone.
        let status = |remaining| LeaseStatus {
            id: 1,
            granted_ttl: 12,
            remaining_ttl: remaining,
            keys: Vec::new(),
        };
        assert!(status(12).alive());
        assert!(status(0).alive());
        assert!(!status(-1).alive());
    }

    #[test]
    fn the_capitalised_proto_fields_land_where_they_are_meant_to() {
        // `ID` and `TTL` in the proto become `id` and `ttl` in prost, and the
        // two sit next to each other on every lease message. Naming them here
        // is what catches a swap, which would renew a lease id of 12 for
        // 777 seconds and look entirely plausible in a log.
        let request = pb::LeaseGrantRequest { ttl: 12, id: 0 };
        assert_eq!(request.ttl, 12);
        assert_eq!(request.id, 0);

        let response = pb::LeaseTimeToLiveResponse {
            id: 777,
            ttl: 5,
            granted_ttl: 12,
            ..Default::default()
        };
        let status = LeaseStatus {
            id: response.id,
            granted_ttl: response.granted_ttl,
            remaining_ttl: response.ttl,
            keys: Vec::new(),
        };
        assert_eq!(status.id, 777);
        assert_eq!(status.granted_ttl, 12);
        assert_eq!(status.remaining_ttl, 5);
    }
}
