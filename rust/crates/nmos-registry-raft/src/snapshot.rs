// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Snapshots: capturing a consistent registry image without stalling the loop.
//!
//! Port of `nmos/raft/snapshot.py`.
//!
//! # Why a snapshot is needed at all
//!
//! The log is compacted, or it grows forever. Compaction means a follower that
//! falls behind the leader's oldest retained entry can no longer be caught up
//! by replication -- the entries it needs are gone -- and must be handed the
//! *state* instead. The two are halves of one mechanism: a log that is
//! compacted without a snapshot transfer to fall back on is a log that can
//! strand a member permanently.
//!
//! # The hard part: the store is alive
//!
//! Apply mutates records **in place**. So neither a shallow copy of the store
//! nor a leisurely walk of it produces an image of any single moment: by the
//! time the walk reaches the last resource, the first may have been updated,
//! and the result is a state that never existed.
//!
//! Stopping the world would work and is not available -- a registry that froze
//! for the length of a snapshot would stall every registration and, worse, the
//! heartbeat timer.
//!
//! **Copy-on-write, captured from apply.** While a capture is open, apply hands
//! this module every record it is *about to* mutate, before mutating it. The
//! capture keeps that pre-image if it has not already got one. Serialisation
//! then walks the live store in chunks, yielding between them, and emits the
//! captured pre-image wherever one exists and the live record otherwise. The
//! result is exactly the state at the pinned index.
//!
//! The cost on the hot path is one map lookup per mutated resource, plus one
//! serialisation the first time each is touched -- and only while a capture is
//! open, which is rare. The cost of the alternative is a registry that pauses.
//!
//! # Why it lives here and not in the store
//!
//! Apply already knows which resources an operation touches. Pushing a hook
//! into the store would spread snapshot awareness across the one module whose
//! invariants are most worth keeping narrow, to learn something the caller
//! already knew.

use std::collections::{BTreeMap, BTreeSet};

use nmos_registry_core::body::Body;
use nmos_registry_core::cursor::TaiCursor;
use nmos_registry_core::resource::RegisteredResource;
use nmos_registry_core::resource_type::ResourceType;
use nmos_registry_core::store::RegistryStore;

use crate::errors::RaftProtocolError;
use crate::ownership::OwnershipTable;
use crate::wire::{Reader, Writer};

/// Bumped only if the snapshot's shape changes.
pub const SNAPSHOT_VERSION: u64 = 1;

/// How many resources are serialised between yields.
///
/// Small enough that the runtime is never held for long, large enough that the
/// yield overhead is noise against the work.
pub const CHUNK_RESOURCES: usize = 256;

/// Identifies a record within a snapshot.
///
/// `(type, id)` and not the id alone: ids are unique per type, not globally,
/// and two resources of different types may share one.
type Key = (ResourceType, String);

/// One record, with `body.text` verbatim.
///
/// Verbatim because the registry's guarantee is that what a client registered
/// is what every member serves, byte for byte. A snapshot that re-encoded
/// bodies would break that on exactly the members that were caught up by one,
/// and the difference would only ever show up as two members disagreeing about
/// a vendor extension.
#[must_use]
pub fn encode_resource(resource: &RegisteredResource) -> Vec<u8> {
    Writer::new()
        .string(1, resource.resource_type.singular())
        .string(2, &resource.id)
        .string(3, &resource.version)
        .uint(4, resource.created.seconds)
        .uint(5, resource.created.nanoseconds)
        .uint(6, resource.updated.seconds)
        .uint(7, resource.updated.nanoseconds)
        .bool(8, resource.extant)
        // Clamped at zero, as the Python's `max(0, health)` does: the field is
        // an unsigned varint, and a negative health is a record that has never
        // been heard from rather than one from before the epoch.
        .uint(9, resource.health().max(0).unsigned_abs())
        .string(10, resource.parent_id.as_deref().unwrap_or(""))
        .string(11, resource.body.text())
        .take()
}

/// Read one record back.
///
/// # Errors
///
/// [`RaftProtocolError`] if the payload is malformed or names a resource type
/// this build does not know.
pub fn decode_resource(payload: &[u8]) -> Result<RegisteredResource, RaftProtocolError> {
    let mut type_name = String::new();
    let mut resource_id = String::new();
    let mut version = String::new();
    let mut parent_id = String::new();
    let mut body_text = String::new();
    let (mut created_s, mut created_ns) = (0, 0);
    let (mut updated_s, mut updated_ns) = (0, 0);
    let mut health = 0;
    let mut extant = true;

    let mut reader = Reader::new(payload);
    while let Some((number, wire)) = reader.next_field()? {
        match number {
            1 => type_name = reader.string()?,
            2 => resource_id = reader.string()?,
            3 => version = reader.string()?,
            4 => created_s = reader.uint()?,
            5 => created_ns = reader.uint()?,
            6 => updated_s = reader.uint()?,
            7 => updated_ns = reader.uint()?,
            8 => extant = reader.bool()?,
            9 => health = reader.uint()?,
            10 => parent_id = reader.string()?,
            11 => body_text = reader.string()?,
            _ => reader.skip(wire)?,
        }
    }

    let Some(resource_type) = ResourceType::from_singular(&type_name) else {
        return Err(RaftProtocolError(format!(
            "snapshot names unknown resource type '{type_name}'"
        )));
    };

    let resource = RegisteredResource::new(
        resource_type,
        resource_id,
        Body::new(body_text),
        version,
        // Struct literals, not `TaiCursor::new`: a nanosecond field at or above
        // one second is pattern-valid and the Python does not normalise it, so
        // normalising here would give this member a different ordering index
        // from every peer for the same resource.
        TaiCursor {
            seconds: created_s,
            nanoseconds: created_ns,
        },
        TaiCursor {
            seconds: updated_s,
            nanoseconds: updated_ns,
        },
        if parent_id.is_empty() {
            None
        } else {
            Some(parent_id)
        },
    );
    resource.set_health(i64::try_from(health).unwrap_or(i64::MAX));
    let mut resource = resource;
    resource.extant = extant;
    Ok(resource)
}

/// What a snapshot covers.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SnapshotMeta {
    /// The last log index the snapshot includes.
    pub last_index: u64,
    /// The term of that entry.
    pub last_term: u64,
    /// How many resources it carries.
    pub resources: usize,
}

/// Pre-images of records mutated since the capture opened.
///
/// Open for the duration of one serialisation and no longer: every open capture
/// adds a lookup to the apply path, and holds pre-images alive that would
/// otherwise be freed.
#[derive(Debug)]
pub struct SnapshotCapture {
    /// The log index this image is pinned to.
    pub index: u64,
    /// The term of that entry.
    pub term: u64,
    /// The ownership table as it stood, already encoded.
    pub ownership: Vec<u8>,
    /// `BTreeMap`, so `orphans` comes out in one order on every member.
    pre: BTreeMap<Key, Vec<u8>>,
    /// Resources that did not exist at the pinned index.
    ///
    /// A record created after the capture opened must NOT appear in it, or the
    /// snapshot describes a future the index it claims had not reached.
    gone: BTreeSet<Key>,
}

impl SnapshotCapture {
    fn new(index: u64, term: u64, ownership: Vec<u8>) -> Self {
        Self {
            index,
            term,
            ownership,
            pre: BTreeMap::new(),
            gone: BTreeSet::new(),
        }
    }

    /// Record a pre-image, if this resource has not been captured yet.
    ///
    /// Called from apply *before* the mutation. Idempotent, so a resource
    /// updated repeatedly during one capture keeps its earliest state -- which
    /// is the state at the pinned index.
    pub fn capture(&mut self, resource: &RegisteredResource) {
        let key = (resource.resource_type, resource.id.clone());
        if self.pre.contains_key(&key) || self.gone.contains(&key) {
            return;
        }
        self.pre.insert(key, encode_resource(resource));
    }

    /// Note a resource that did not exist when the capture opened.
    pub fn capture_created(&mut self, resource_type: ResourceType, resource_id: &str) {
        let key = (resource_type, resource_id.to_owned());
        if !self.pre.contains_key(&key) {
            self.gone.insert(key);
        }
    }

    /// The bytes this resource had at the pinned index, or `None` to skip it.
    #[must_use]
    pub fn image_of(&self, resource: &RegisteredResource) -> Option<Vec<u8>> {
        let key = (resource.resource_type, resource.id.clone());
        if self.gone.contains(&key) {
            return None;
        }
        Some(
            self.pre
                .get(&key)
                .cloned()
                .unwrap_or_else(|| encode_resource(resource)),
        )
    }

    /// Captured pre-images whose resource the live walk did not reach.
    ///
    /// Deleted, in other words. Ordered by `(type name, id)` rather than by the
    /// enum's discriminant so the order is the same one the Python's sort
    /// produces, and therefore identical on every member.
    #[must_use]
    pub fn orphans(&self, live: &BTreeSet<Key>) -> Vec<Vec<u8>> {
        let mut found: Vec<(&str, &str, &Vec<u8>)> = self
            .pre
            .iter()
            .filter(|&(key, _)| !live.contains(key))
            .map(|(key, image)| (key.0.singular(), key.1.as_str(), image))
            .collect();
        found.sort_unstable_by(|a, b| (a.0, a.1).cmp(&(b.0, b.1)));
        found
            .into_iter()
            .map(|(_, _, image)| image.clone())
            .collect()
    }

    /// How many pre-images this capture is holding.
    #[must_use]
    pub fn held(&self) -> usize {
        self.pre.len()
    }
}

/// A capture was requested while one was already open, or finished twice.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CaptureError(pub String);

impl std::fmt::Display for CaptureError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for CaptureError {}

/// Takes and installs snapshots for one member.
///
/// # Divergence from the Python, deliberate
///
/// The Python's `SnapshotStore` holds a reference to the live `RegistryStore`
/// and walks it from inside `finish`. Here the store lives behind the
/// registry's `RwLock` and the walk must not hold that lock across a yield --
/// which is the whole point of the chunking. So `finish` takes the records the
/// caller has already collected under a read lock, and this type holds only the
/// capture. The atomicity argument is unchanged; what moves is *who* walks.
#[derive(Debug, Default)]
pub struct SnapshotStore {
    capture: Option<SnapshotCapture>,
}

impl SnapshotStore {
    /// A store with no capture open.
    #[must_use]
    pub fn new() -> Self {
        Self::default()
    }

    /// The open capture, if any. The state machine consults this per mutation.
    #[must_use]
    pub const fn capture(&self) -> Option<&SnapshotCapture> {
        self.capture.as_ref()
    }

    /// The open capture, for the state machine to record a pre-image into.
    pub const fn capture_mut(&mut self) -> Option<&mut SnapshotCapture> {
        self.capture.as_mut()
    }

    /// Open a capture pinned at `index`.
    ///
    /// # Errors
    ///
    /// [`CaptureError`] if one is already open. Two concurrent captures would
    /// each see only the mutations that arrived after it opened, so both would
    /// describe a state that never existed.
    pub fn begin(
        &mut self,
        index: u64,
        term: u64,
        ownership: &OwnershipTable,
    ) -> Result<(), CaptureError> {
        if self.capture.is_some() {
            return Err(CaptureError(
                "a snapshot capture is already open".to_owned(),
            ));
        }
        self.capture = Some(SnapshotCapture::new(index, term, ownership.encode()));
        Ok(())
    }

    /// Serialise the pinned image from `live`, which the caller walked.
    ///
    /// `live` is every extant resource, already encoded *or* substituted with
    /// its pre-image by [`SnapshotCapture::image_of`], in the fixed order
    /// [`walk_order`] defines. Resources that were **deleted** while the
    /// capture was open are no longer in the walk but existed at the pinned
    /// index, so their pre-images are appended: without that, a member caught
    /// up during a cascading delete would be missing every resource that
    /// cascade removed -- silently, and only on that member.
    ///
    /// # Errors
    ///
    /// [`CaptureError`] if no capture is open.
    pub fn finish(
        &mut self,
        mut records: Vec<Vec<u8>>,
        live: &BTreeSet<Key>,
    ) -> Result<Vec<u8>, CaptureError> {
        let Some(capture) = self.capture.take() else {
            return Err(CaptureError("no snapshot capture is open".to_owned()));
        };

        records.extend(capture.orphans(live));

        let mut writer = Writer::new()
            .uint(1, SNAPSHOT_VERSION)
            .uint(2, capture.index)
            .uint(3, capture.term)
            .uint(4, records.len() as u64)
            .bytes(5, &capture.ownership);
        for record in &records {
            writer = writer.bytes(6, record);
        }
        Ok(writer.take())
    }

    /// Drop an open capture without producing a snapshot.
    pub fn abandon(&mut self) {
        self.capture = None;
    }
}

/// Every extant resource, in the fixed order a snapshot serialises.
///
/// **Tombstones are deliberately excluded**, matching what the etcd backend's
/// preload produces -- there, deleted resources are gone from the keyspace
/// entirely, so a member that preloads has none either.
///
/// The consequence, stated rather than buried: a member caught up by a snapshot
/// holds no tombstones, so for up to one forget interval its type index is
/// narrower than its peers'. It would accept a re-registration of a
/// recently-deleted id under a *different* type that the others refuse. The
/// window is bounded by the replicated forget operation -- which every member
/// applies at the same log index, and which is a harmless no-op on a member
/// that never had the tombstone.
///
/// Sorted, so two members with equal stores produce byte-identical snapshots,
/// which is what makes one comparable or checksummable at all.
pub fn walk_order(store: &RegistryStore) -> Vec<(ResourceType, String)> {
    let mut order = Vec::new();
    for resource_type in ResourceType::ALL {
        let mut ids: Vec<String> = store
            .iter_extant(resource_type)
            .map(|resource| resource.id.clone())
            .collect();
        ids.sort_unstable();
        order.extend(ids.into_iter().map(|id| (resource_type, id)));
    }
    order
}

/// Encode one chunk of the walk, substituting pre-images where they exist.
///
/// The caller holds a read lock for the duration of *one chunk* and drops it
/// between chunks, which is where the Python yields. That the walk is therefore
/// **not atomic** is not a flaw -- it is the whole reason copy-on-write exists:
/// anything mutated between chunks was handed to the capture before it changed,
/// and anything deleted comes back through [`SnapshotCapture::orphans`].
///
/// A key whose resource is gone by the time this chunk runs is skipped and,
/// deliberately, *not* added to `live`: `live` means "the walk reached it", and
/// marking a deleted resource as reached would drop its pre-image from the
/// orphans and lose it from the snapshot entirely.
pub fn collect_chunk(
    store: &RegistryStore,
    capture: &SnapshotCapture,
    keys: &[Key],
    live: &mut BTreeSet<Key>,
) -> Vec<Vec<u8>> {
    let mut records = Vec::new();
    for key in keys {
        let Some(resource) = store.get(key.0, &key.1) else {
            continue;
        };
        live.insert(key.clone());
        if let Some(image) = capture.image_of(resource) {
            records.push(image);
        }
    }
    records
}

/// Walk the whole store in one go, for callers with no runtime to yield to.
///
/// Used by the tests and by a single-member cluster, where there is no other
/// writer to starve. A real member drives [`collect_chunk`] in
/// [`CHUNK_RESOURCES`]-sized slices instead.
pub fn collect_all(
    store: &RegistryStore,
    capture: &SnapshotCapture,
) -> (Vec<Vec<u8>>, BTreeSet<Key>) {
    let mut live = BTreeSet::new();
    let records = collect_chunk(store, capture, &walk_order(store), &mut live);
    (records, live)
}

/// Parse a snapshot into the pieces an installer needs.
///
/// # Errors
///
/// [`RaftProtocolError`] if the payload is malformed, carries a version this
/// build does not understand, or claims a different number of resources than it
/// carries -- which is a truncated transfer that still parsed, and installing
/// it would leave this member silently missing resources its peers hold.
pub fn decode_snapshot(
    payload: &[u8],
) -> Result<(SnapshotMeta, OwnershipTable, Vec<RegisteredResource>), RaftProtocolError> {
    let mut version = 0;
    let mut index = 0;
    let mut term = 0;
    let mut count = 0;
    let mut ownership_blob: Vec<u8> = Vec::new();
    let mut records = Vec::new();

    let mut reader = Reader::new(payload);
    while let Some((number, wire)) = reader.next_field()? {
        match number {
            1 => version = reader.uint()?,
            2 => index = reader.uint()?,
            3 => term = reader.uint()?,
            4 => count = reader.uint()?,
            5 => ownership_blob = reader.bytes()?.to_vec(),
            6 => {
                let record = decode_resource(reader.bytes()?)?;
                records.push(record);
            }
            _ => reader.skip(wire)?,
        }
    }

    if version != SNAPSHOT_VERSION {
        return Err(RaftProtocolError(format!(
            "snapshot version {version}, this member understands {SNAPSHOT_VERSION}",
        )));
    }
    if count != records.len() as u64 {
        return Err(RaftProtocolError(format!(
            "snapshot claims {count} resources and carries {}",
            records.len(),
        )));
    }

    Ok((
        SnapshotMeta {
            last_index: index,
            last_term: term,
            resources: records.len(),
        },
        OwnershipTable::decode(&ownership_blob)?,
        records,
    ))
}

/// Build a fresh store from a snapshot's records.
///
/// Off to the side, deliberately: the caller swaps it in once it is complete,
/// so Query never sees a half-loaded store. A member that served an empty view
/// for the length of an install would look, to a Controller, exactly like a
/// member whose registry had been wiped.
///
/// Restored through `prepare` + `apply_committed` with the cursors and health
/// passed explicitly -- the same path the state machine uses, and for the same
/// reason. Letting the store allocate would re-stamp cursors from this member's
/// own clock, so a member caught up by a snapshot would page differently from
/// every peer.
///
/// # Errors
///
/// [`RaftProtocolError`] if a record failed validation, or a child arrived
/// without its parent. Both mean the snapshot does not describe a state the
/// cluster was ever in, and installing part of it would leave this member
/// quietly serving a subset.
pub fn install(
    records: Vec<RegisteredResource>,
    gc_interval: i64,
    forget_interval: i64,
) -> Result<RegistryStore, RaftProtocolError> {
    let mut store = RegistryStore::with_intervals(gc_interval, forget_interval);

    // Parents first: the store maintains a parent/child index, and a child
    // whose parent is absent is refused outright.
    let mut ordered = records;
    ordered.sort_by(|a, b| depth_key(a).cmp(&depth_key(b)));

    for resource in ordered {
        let prepared = store
            .prepare(resource.resource_type, resource.body.data())
            .map_err(|failure| {
                RaftProtocolError(format!(
                    "snapshot record {} {} was refused: {}",
                    resource.resource_type.singular(),
                    resource.id,
                    failure.error.as_str(),
                ))
            })?;
        store.apply_committed(
            &prepared,
            resource.body.clone(),
            Some(resource.created),
            Some(resource.updated),
            Some(resource.health()),
        );
    }
    Ok(store)
}

fn depth_key(resource: &RegisteredResource) -> (u8, &str) {
    let depth = match resource.resource_type {
        ResourceType::Node => 0,
        ResourceType::Device => 1,
        _ => 2,
    };
    (depth, &resource.id)
}
