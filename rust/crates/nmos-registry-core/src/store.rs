// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The in-memory resource store behind both the Registration and Query APIs.
//!
//! This owns every piece of registry state that outlives a request: the
//! resources, the parent/child graph, the registry-assigned paging cursors,
//! per-resource health, and the two-stage deletion lifecycle.
//!
//! # Four things worth knowing before changing anything here
//!
//! **The store validates; the handlers translate.** All five of the
//! 400-yielding conditions in `Behaviour - Registration.md:98-104` are decided
//! here and returned as a [`RegistrationError`]. Handlers map those to HTTP.
//! Keeping the decisions in one place is what stops "which layer checks
//! referential integrity?" from having two answers.
//!
//! **Health is inherited, not per-resource.** Only Nodes heartbeat, but
//! [`RegistryStore::heartbeat`] refreshes the Node *and every descendant*,
//! exactly as nmos-cpp's `set_resource_health` does. Garbage collection then
//! expires anything whose health has fallen behind, and the cascade falls out
//! for free.
//!
//! **Deletion is two-stage.** [`RegistryStore::delete`] and garbage collection
//! mark resources *non-extant* rather than dropping them; forgetting drops them
//! once the forget interval has elapsed. The intermediate state is what lets a
//! removal grain carry the resource's final content, and what keeps paging
//! cursors monotonic across a delete.
//!
//! **Deciding and applying are separate, everywhere it matters.**
//! `prepare`/`apply_committed` and `forgettable`/`forget` are each split for
//! the same reason: a distributed backend validates against local state,
//! commits, and applies later -- and every member must reach the same answer.
//! That is also why the clock is read in the deciding half and never in the
//! applying half.
//!
//! # Nothing here awaits
//!
//! Not by convention: this crate has no runtime to await on. See the crate
//! docs for why that is the concurrency model rather than an accident.

use std::collections::{BTreeSet, HashMap};

use crate::body::Body;
use crate::cursor::{TAI_UTC_OFFSET, TaiCursor};
use crate::event::{RegistrationError, ResourceEvent};
use crate::index::CursorIndex;
use crate::per::{PerOrder, PerType};
use crate::resource::{Order, RegisteredResource, ResourceId};
use crate::resource_type::ResourceType;

/// Seconds of heartbeat silence after which a Node and its sub-resources are
/// collected. `Behaviour - Registration.md:47`.
pub const DEFAULT_GC_INTERVAL: i64 = 12;

/// Seconds a non-extant resource is retained before being dropped entirely.
pub const DEFAULT_FORGET_INTERVAL: i64 = 60;

/// The current time in whole TAI seconds -- the unit health is measured in.
///
/// One-second resolution deliberately: the heartbeat interval is 5 s and the
/// collection interval 12 s (`Behaviour - Registration.md:45,47`), so
/// sub-second precision carries no information, and integer seconds is also
/// what the `health` string on the wire must be
/// (`registrationapi-health-response.json`: `^[0-9]+$`).
#[must_use]
pub fn health_now() -> i64 {
    let cursor = TaiCursor::now();
    i64::try_from(cursor.seconds).unwrap_or(i64::MAX)
}

/// Why a registration was refused, and the detail for the 400 body.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RegistrationFailure {
    /// Which documented condition was hit.
    pub error: RegistrationError,
    /// Human-readable explanation, placed in the response body.
    pub detail: String,
}

impl RegistrationFailure {
    fn new(error: RegistrationError, detail: impl Into<String>) -> Self {
        Self {
            error,
            detail: detail.into(),
        }
    }
}

/// A registration that has passed validation but has not been applied.
///
/// The gap between deciding and applying is what a distributed backend needs:
/// it validates against the local store, commits, and only then applies -- and
/// what it carries across those steps is exactly this.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedRegistration {
    /// The type being registered.
    pub resource_type: ResourceType,
    /// The resource's id.
    pub resource_id: ResourceId,
    /// The resource's own `version` attribute.
    pub version: String,
    /// This type's parent-key value, or `None` for a Node.
    pub parent_id: Option<ResourceId>,
    /// The 201-vs-200 answer of `Behaviour - Registration.md:25`, decided here
    /// rather than inferred later from whether the store happened to hold the
    /// id.
    pub creates: bool,
    /// Whether this replaces a non-extant record, which is a *create* for
    /// protocol purposes and needs extra cleanup when applied.
    pub reviving: bool,
}

/// What applying a registration produced.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Applied {
    /// True for 201, false for 200.
    pub created: bool,
    /// The grain events to publish.
    pub events: Vec<ResourceEvent>,
}

/// The counters behind the periodic status line.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RegistryStatistics {
    /// Every extant resource across all eight kinds, subscriptions and grains
    /// included.
    pub total: usize,
    /// Extant resources per type.
    pub per_type: PerType<usize>,
    /// Open subscriptions, supplied by the caller.
    pub subscriptions: usize,
    /// Buffered grains, supplied by the caller.
    pub grains: usize,
    /// The newest update cursor across all extant resources.
    pub most_recent_update: TaiCursor,
    /// The minimum health over extant resources, or the current health when
    /// there is nothing to minimise over.
    pub least_health: i64,
    /// Resources that are tombstoned but not yet forgotten. Reported alongside
    /// rather than deducted, matching nmos-cpp.
    pub non_extant: usize,
}

impl RegistryStatistics {
    /// The eight per-kind counters, in nmos-cpp's fixed order.
    ///
    /// The six resource types in IS-04 registration dependency order, then
    /// subscriptions, then grains. The order is part of the rendered line, so
    /// it is not an implementation detail.
    fn counters(&self) -> impl Iterator<Item = (&'static str, usize)> + use<'_> {
        self.per_type
            .iter()
            .map(|(kind, count)| (kind.plural(), *count))
            .chain([
                ("subscriptions", self.subscriptions),
                ("grains", self.grains),
            ])
    }

    /// Render the counters exactly as nmos-cpp does.
    ///
    /// `"<total> resources (<n> nodes, ..., <n> grains), most recent update:
    /// <ver>, least health: <h>, <n> non-extant resources"`. The caller
    /// prefixes `"At <now>, the registry contains "`.
    ///
    /// Reproduced rather than reinvented so the two registries' logs can be
    /// read side by side when diagnosing a registration problem -- which is
    /// also why nmos-cpp emits it from its POST handler as well as its expiry
    /// thread.
    #[must_use]
    pub fn render(&self) -> String {
        let parts = self
            .counters()
            .map(|(label, count)| format!("{count} {label}"))
            .collect::<Vec<_>>()
            .join(", ");
        format!(
            "{} resources ({parts}), most recent update: {}, \
             least health: {}, {} non-extant resources",
            self.total, self.most_recent_update, self.least_health, self.non_extant,
        )
    }
}

/// The registry's resource database.
#[derive(Debug)]
pub struct RegistryStore {
    /// Resources bucketed by type, indexed by `ResourceType as usize`.
    ///
    /// Both extant and non-extant records live here; `extant` on the record is
    /// the discriminator, and every query path must honour it.
    by_type: PerType<HashMap<ResourceId, RegisteredResource>>,
    /// id -> type, so an id collision across types is detected without
    /// scanning all six buckets (`Behaviour - Registration.md:101`).
    type_of: HashMap<ResourceId, ResourceType>,
    /// parent id -> child ids, so a cascade and a recursive health refresh are
    /// O(subtree) rather than O(registry).
    ///
    /// A `BTreeSet` rather than a `HashSet`, which makes sibling order
    /// deterministic everywhere it is walked. Python needs that only in
    /// `subtree`, and sorts there explicitly with the note that a `set`'s
    /// "iteration order differs between members" -- the erase path has the same
    /// exposure and does not sort, so its grain order varies between runs.
    /// Ordering the container gives both paths the property for free.
    children: HashMap<ResourceId, BTreeSet<ResourceId>>,
    /// The last cursor handed out per type, enforcing "no duplicate
    /// creation/update timestamps within a type" (`APIs - Query
    /// Parameters.md:17`).
    last_cursor: PerType<Option<TaiCursor>>,
    /// The cursor-ordered indexes, one per type per order. See [`crate::index`].
    indexes: PerType<PerOrder<CursorIndex>>,
    gc_interval: i64,
    forget_interval: i64,
}

impl Default for RegistryStore {
    fn default() -> Self {
        Self::new()
    }
}

impl RegistryStore {
    /// A store with the documented default intervals.
    #[must_use]
    pub fn new() -> Self {
        Self::with_intervals(DEFAULT_GC_INTERVAL, DEFAULT_FORGET_INTERVAL)
    }

    /// A store with explicit intervals, in seconds.
    #[must_use]
    pub fn with_intervals(gc_interval: i64, forget_interval: i64) -> Self {
        Self {
            by_type: PerType::default(),
            type_of: HashMap::new(),
            children: HashMap::new(),
            last_cursor: PerType::default(),
            indexes: PerType::default(),
            gc_interval,
            forget_interval,
        }
    }

    /// Seconds of silence after which a Node is collected.
    #[must_use]
    pub const fn gc_interval(&self) -> i64 {
        self.gc_interval
    }

    /// Seconds a tombstone is retained.
    #[must_use]
    pub const fn forget_interval(&self) -> i64 {
        self.forget_interval
    }

    // -- cursor allocation ----------------------------------------------

    /// Allocate a strictly-increasing cursor for a type.
    ///
    /// Wall-clock normally supplies it, but two registrations of the same type
    /// within one clock tick would otherwise share a cursor -- and a client
    /// paging with `paging.since=<that cursor>` would then skip whichever
    /// record sorted second. Falling forward by one nanosecond on collision
    /// keeps cursors unique and monotonic per type.
    fn allocate_cursor(&mut self, resource_type: ResourceType) -> TaiCursor {
        let mut cursor = TaiCursor::now();
        if let Some(previous) = *self.last_cursor.get(resource_type)
            && cursor <= previous
        {
            cursor = previous.next();
        }
        *self.last_cursor.get_mut(resource_type) = Some(cursor);
        cursor
    }

    /// Allocate a cursor without applying anything.
    ///
    /// A distributed backend needs the cursor *before* the write, because it
    /// goes into the envelope the backend stores and so has to be
    /// authoritative. Uniqueness within a type still comes from the same
    /// allocator, which is what keeps paging from skipping a record.
    pub fn next_cursor(&mut self, resource_type: ResourceType) -> TaiCursor {
        self.allocate_cursor(resource_type)
    }

    // -- lookup ----------------------------------------------------------

    /// Fetch one resource.
    ///
    /// Non-extant resources are hidden: to every API client a deleted resource
    /// is simply gone, and only the lifecycle machinery has a reason to see the
    /// tombstoned record -- for which there is [`Self::get_including_tombstoned`].
    #[must_use]
    pub fn get(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Option<&RegisteredResource> {
        self.by_type
            .get(resource_type)
            .get(resource_id)
            .filter(|resource| resource.extant)
    }

    /// Fetch one resource, tombstoned or not.
    #[must_use]
    pub fn get_including_tombstoned(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Option<&RegisteredResource> {
        self.by_type.get(resource_type).get(resource_id)
    }

    /// Fetch an extant resource by id, whatever its type.
    #[must_use]
    pub fn find_any(&self, resource_id: &str) -> Option<&RegisteredResource> {
        let resource_type = *self.type_of.get(resource_id)?;
        self.get(resource_type, resource_id)
    }

    /// The live resources of one type, in no particular order.
    pub fn iter_extant(
        &self,
        resource_type: ResourceType,
    ) -> impl Iterator<Item = &RegisteredResource> {
        self.by_type
            .get(resource_type)
            .values()
            .filter(|resource| resource.extant)
    }

    /// How many live resources of one type there are.
    #[must_use]
    pub fn count_extant(&self, resource_type: ResourceType) -> usize {
        self.iter_extant(resource_type).count()
    }

    /// The live resources of one type, ascending by the `order` cursor.
    ///
    /// This is what makes a Query page without sorting: the caller filters this
    /// stream, and a filtered subsequence of a sorted sequence is still sorted.
    /// Ties break on resource id, so two registries that received the same
    /// resources in different orders still page identically.
    pub fn iter_ordered(
        &self,
        resource_type: ResourceType,
        order: Order,
    ) -> impl DoubleEndedIterator<Item = &RegisteredResource> {
        let bucket = self.by_type.get(resource_type);
        self.index(resource_type, order)
            .iter()
            .filter_map(move |(_, id)| bucket.get(id))
            .filter(|resource| resource.extant)
    }

    /// The live resources of one type within a paging window, ascending.
    ///
    /// The window is `(since, until]`, per `QueryAPI.raml:29,33`.
    pub fn iter_window(
        &self,
        resource_type: ResourceType,
        order: Order,
        since: Option<TaiCursor>,
        until: Option<TaiCursor>,
    ) -> impl DoubleEndedIterator<Item = &RegisteredResource> {
        let bucket = self.by_type.get(resource_type);
        self.index(resource_type, order)
            .range(since, until)
            .filter_map(move |(_, id)| bucket.get(id))
            .filter(|resource| resource.extant)
    }

    /// The cursor-ordered index for one type and order.
    #[must_use]
    pub fn index(&self, resource_type: ResourceType, order: Order) -> &CursorIndex {
        self.indexes.get(resource_type).get(order)
    }

    // -- registration ----------------------------------------------------

    /// Validate a registration against current state, without mutating.
    ///
    /// # Errors
    ///
    /// One of the five documented 400 conditions, with the detail for the
    /// response body.
    pub fn prepare(
        &self,
        resource_type: ResourceType,
        raw: &serde_json::Value,
    ) -> Result<PreparedRegistration, RegistrationFailure> {
        let resource_id = raw
            .get("id")
            .and_then(serde_json::Value::as_str)
            .filter(|id| !id.is_empty())
            .ok_or_else(|| {
                RegistrationFailure::new(
                    RegistrationError::Schema,
                    "resource has no 'id' attribute",
                )
            })?;

        let version = raw
            .get("version")
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| {
                RegistrationFailure::new(
                    RegistrationError::Schema,
                    "resource has no 'version' attribute",
                )
            })?;

        // :101 -- the id must not already name a different type.
        if let Some(existing) = self.type_of.get(resource_id)
            && *existing != resource_type
        {
            return Err(RegistrationFailure::new(
                RegistrationError::IdTypeConflict,
                format!(
                    "id {resource_id} is already registered as a {existing}, \
                     cannot re-register as a {resource_type}"
                ),
            ));
        }

        let parent_id = parent_id_of(resource_type, raw);

        // :104 -- the parent must exist and be of the right type.
        self.check_parent(resource_type, parent_id.as_deref())?;

        let previous = self.by_type.get(resource_type).get(resource_id);
        // A non-extant record is treated as absent for registration purposes:
        // re-registering an id that was deleted or collected is a *create*, and
        // must answer 201 so the Node's own state machine stays in step.
        let reviving = previous.is_some_and(|p| !p.extant);

        if let Some(previous) = previous.filter(|p| p.extant) {
            self.check_update(previous, version, parent_id.as_deref())?;
        }

        Ok(PreparedRegistration {
            resource_type,
            resource_id: resource_id.to_owned(),
            version: version.to_owned(),
            parent_id,
            creates: previous.is_none() || reviving,
            reviving,
        })
    }

    /// Apply a registration that has already been validated.
    ///
    /// `created`, `updated` and `health` are authoritative values from a
    /// distributed backend; `None` means allocate or stamp locally. Supplying
    /// them is what makes every member page identically.
    pub fn apply_committed(
        &mut self,
        prepared: &PreparedRegistration,
        body: Body,
        created: Option<TaiCursor>,
        updated: Option<TaiCursor>,
        health: Option<i64>,
    ) -> Applied {
        let resource_type = prepared.resource_type;
        let resource_id = prepared.resource_id.clone();

        let now_health = health.unwrap_or_else(health_now);
        let cursor = match updated {
            Some(cursor) => cursor,
            None => self.allocate_cursor(resource_type),
        };
        // Keep the per-type high-water mark ahead of any authoritative cursor
        // handed in, so a later locally allocated cursor cannot collide with
        // one that already came from the backend.
        let previous_high = *self.last_cursor.get(resource_type);
        if previous_high.is_none_or(|previous| cursor > previous) {
            *self.last_cursor.get_mut(resource_type) = Some(cursor);
        }

        let previous = self.by_type.get(resource_type).get(&resource_id);
        let pre_parent = previous.and_then(|p| p.parent_id.clone());
        let is_update = previous.is_some() && !prepared.reviving;

        if is_update {
            // The old cursor has to be read before the record is touched: it is
            // the key the index entry currently occupies, and once the record
            // carries the new one it is unrecoverable. See `index::reposition`.
            let (pre_body, old_updated) = {
                let Some(existing) = self.by_type.get_mut(resource_type).get_mut(&resource_id)
                else {
                    // Unreachable: `previous` was just observed. Handled rather
                    // than unwrapped because this crate denies panics on the
                    // write path.
                    return Applied {
                        created: false,
                        events: Vec::new(),
                    };
                };
                let pre_body = existing.body.clone();
                let old_updated = existing.updated;
                existing.body = body;
                existing.version = prepared.version.clone();
                existing.updated = cursor;
                existing.parent_id = prepared.parent_id.clone();
                existing.set_health(now_health);
                (pre_body, old_updated)
            };

            self.reparent(
                &resource_id,
                pre_parent.as_deref(),
                prepared.parent_id.as_deref(),
            );
            // `created` did not move, so only the update index reorders.
            self.indexes
                .get_mut(resource_type)
                .get_mut(Order::Updated)
                .reposition(old_updated, cursor, &resource_id);

            let events = self
                .by_type
                .get(resource_type)
                .get(&resource_id)
                .map(|resource| vec![ResourceEvent::modified(pre_body, resource)])
                .unwrap_or_default();
            return Applied {
                created: false,
                events,
            };
        }

        // A revive replaces the record in place, so any index entry it still
        // holds is at the *old* cursors and must go before the new ones land.
        if prepared.reviving
            && let Some(stale) = self.by_type.get(resource_type).get(&resource_id)
        {
            let (stale_created, stale_updated) = (stale.created, stale.updated);
            self.indexes
                .get_mut(resource_type)
                .get_mut(Order::Created)
                .remove(stale_created, &resource_id);
            self.indexes
                .get_mut(resource_type)
                .get_mut(Order::Updated)
                .remove(stale_updated, &resource_id);
        }

        let resource = RegisteredResource::new(
            resource_type,
            resource_id.clone(),
            body,
            prepared.version.clone(),
            created.unwrap_or(cursor),
            cursor,
            prepared.parent_id.clone(),
        );
        resource.set_health(now_health);
        let created_cursor = resource.created;

        self.by_type
            .get_mut(resource_type)
            .insert(resource_id.clone(), resource);
        self.type_of.insert(resource_id.clone(), resource_type);
        self.reparent(
            &resource_id,
            pre_parent.as_deref(),
            prepared.parent_id.as_deref(),
        );

        // A revived id may still be listed as the parent of resources erased
        // alongside it, and the fresh record should not inherit them.
        //
        // Defensive rather than load-bearing. A later cascade would not
        // resurrect those children -- `erase_subtree` tests `extant` before
        // recursing -- and the entry does not leak either, because
        // `forget_record` removes a resource from its parent's set as it drops
        // it. Measured: removing this leaves every test in both implementations
        // passing, including the differential fuzz and its graph check.
        //
        // Kept because inheriting a stale child list is meaningless in any
        // case. Recorded because the next reader deserves to know which of the
        // two it is.
        if prepared.reviving {
            self.children.remove(&resource_id);
        }

        self.indexes
            .get_mut(resource_type)
            .get_mut(Order::Created)
            .insert(created_cursor, resource_id.clone());
        self.indexes
            .get_mut(resource_type)
            .get_mut(Order::Updated)
            .insert(cursor, resource_id.clone());

        let events = self
            .by_type
            .get(resource_type)
            .get(&resource_id)
            .map(|resource| vec![ResourceEvent::added(resource)])
            .unwrap_or_default();
        Applied {
            created: true,
            events,
        }
    }

    /// Validate and apply in one step, which is what a standalone registry does.
    ///
    /// # Errors
    ///
    /// Whatever [`Self::prepare`] would return.
    pub fn insert_or_update(
        &mut self,
        resource_type: ResourceType,
        body: Body,
    ) -> Result<Applied, RegistrationFailure> {
        let prepared = self.prepare(resource_type, body.data())?;
        Ok(self.apply_committed(&prepared, body, None, None, None))
    }

    /// Referential integrity: `Behaviour - Registration.md:55, :104`.
    ///
    /// "In order to permit garbage collection, resources MUST only be accepted
    /// by a Registration API where the registry already has a record of the
    /// corresponding parent resource." The AMWA mock registry skips this
    /// entirely; without it a Sender can outlive every Node and never be
    /// collected.
    fn check_parent(
        &self,
        resource_type: ResourceType,
        parent_id: Option<&str>,
    ) -> Result<(), RegistrationFailure> {
        let Some(expected) = resource_type.parent_type() else {
            return Ok(());
        };
        let key = resource_type.parent_key().unwrap_or("parent");

        let Some(parent_id) = parent_id else {
            return Err(RegistrationFailure::new(
                RegistrationError::Schema,
                format!("{resource_type} is missing its '{key}' attribute"),
            ));
        };

        let actual_type = self.type_of.get(parent_id).copied();
        let parent = actual_type.and_then(|t| self.by_type.get(t).get(parent_id));
        match parent {
            None => Err(RegistrationFailure::new(
                RegistrationError::ParentMissing,
                format!("parent {expected} {parent_id} is not registered"),
            )),
            Some(parent) if !parent.extant => Err(RegistrationFailure::new(
                RegistrationError::ParentMissing,
                format!("parent {expected} {parent_id} is not registered"),
            )),
            Some(_) if actual_type != Some(expected) => {
                let named = actual_type.map_or("unknown".to_owned(), |t| t.to_string());
                Err(RegistrationFailure::new(
                    RegistrationError::ParentMissing,
                    format!("{key} {parent_id} names a {named}, expected a {expected}"),
                ))
            }
            Some(_) => Ok(()),
        }
    }

    /// The two update-only 400 conditions.
    ///
    /// `:102` the version must not go backwards, and `:103` a parent id must
    /// not be modified by an update. Re-POSTing an **unchanged** version is
    /// explicitly not an error: a Node that re-registers after a failed
    /// heartbeat replays its resources verbatim, and rejecting that would break
    /// the documented recovery path at `:114`.
    fn check_update(
        &self,
        previous: &RegisteredResource,
        version: &str,
        parent_id: Option<&str>,
    ) -> Result<(), RegistrationFailure> {
        let Some(new_cursor) = TaiCursor::parse(version) else {
            return Err(RegistrationFailure::new(
                RegistrationError::Schema,
                format!("version '{version}' is not '<seconds>:<nanoseconds>'"),
            ));
        };
        if let Some(old_cursor) = previous.version_cursor()
            && new_cursor < old_cursor
        {
            return Err(RegistrationFailure::new(
                RegistrationError::VersionRegression,
                format!(
                    "version {version} is earlier than the registered version {}",
                    previous.version,
                ),
            ));
        }
        if let Some(existing_parent) = previous.parent_id.as_deref()
            && parent_id != Some(existing_parent)
        {
            let key = previous.resource_type.parent_key().unwrap_or("parent");
            let named = parent_id.unwrap_or("None");
            return Err(RegistrationFailure::new(
                RegistrationError::ParentChanged,
                format!("{key} cannot be modified by an update ({existing_parent} -> {named})"),
            ));
        }
        Ok(())
    }

    /// Maintain the parent -> children index.
    fn reparent(&mut self, resource_id: &str, pre: Option<&str>, new: Option<&str>) {
        if let Some(pre) = pre
            && let Some(siblings) = self.children.get_mut(pre)
        {
            siblings.remove(resource_id);
        }
        if let Some(new) = new {
            self.children
                .entry(new.to_owned())
                .or_default()
                .insert(resource_id.to_owned());
        }
    }

    // -- deletion --------------------------------------------------------

    /// Delete a resource and, cascading, all of its descendants.
    ///
    /// `Behaviour - Registration.md:68` -- "Where a DELETE is issued against a
    /// parent resource, all child resources MUST be removed from the registry
    /// immediately" -- and `:74`, which requires cleanup even when a Node
    /// unregisters out of order. The AMWA mock removes only the addressed
    /// resource, leaving orphans nothing will ever collect.
    ///
    /// Returns the removal events, **deepest descendant first**, or `None` if
    /// the resource was not registered, in which case the caller answers 404.
    pub fn delete(
        &mut self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Option<Vec<ResourceEvent>> {
        self.get(resource_type, resource_id)?;
        Some(self.erase_subtree(resource_type, resource_id))
    }

    /// Mark a resource and its descendants non-extant, depth-first.
    ///
    /// Children are erased before their parent so a client replaying the events
    /// in order never sees a parent disappear while its children are still
    /// present -- the mirror image of the registration ordering rule at
    /// `Behaviour - Registration.md:57-64`.
    fn erase_subtree(
        &mut self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Vec<ResourceEvent> {
        let mut events = Vec::new();
        let child_ids: Vec<ResourceId> = self
            .children
            .get(resource_id)
            .map(|set| set.iter().cloned().collect())
            .unwrap_or_default();
        for child_id in child_ids {
            if let Some(child_type) = self.type_of.get(&child_id).copied()
                && self
                    .by_type
                    .get(child_type)
                    .get(&child_id)
                    .is_some_and(|c| c.extant)
            {
                events.extend(self.erase_subtree(child_type, &child_id));
            }
        }
        let now = health_now();
        if let Some(resource) = self.by_type.get_mut(resource_type).get_mut(resource_id) {
            events.push(ResourceEvent::removed(resource));
            resource.extant = false;
            resource.set_health(now);
        }
        events
    }

    /// Mark a single resource non-extant, **without** cascading.
    ///
    /// The distributed counterpart of [`Self::delete`]. There the cascade has
    /// already happened in the backend -- deleting a Node ranges over its whole
    /// subtree -- and the watch delivers a separate event for every key that
    /// went, all within one revision. Cascading again locally would erase
    /// descendants a second time and emit duplicate removal grains.
    ///
    /// The caller applies a revision's removals descendants-first, so a
    /// subscriber never sees a parent go while its children remain.
    ///
    /// `None` when the resource is absent or already non-extant, which is
    /// normal on a watch replay after reconnection.
    pub fn remove_one(
        &mut self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Option<ResourceEvent> {
        let now = health_now();
        let resource = self.by_type.get_mut(resource_type).get_mut(resource_id)?;
        if !resource.extant {
            return None;
        }
        let event = ResourceEvent::removed(resource);
        resource.extant = false;
        resource.set_health(now);
        Some(event)
    }

    /// A resource and every descendant, deepest first, in a fixed order.
    ///
    /// Mirrors the erase order: children before their parent, so a caller
    /// acting on the list never removes a parent while its children remain.
    ///
    /// Exists for the distributed backends, which have to *see* what a cascade
    /// is about to remove before it removes it -- a snapshot taken while a Node
    /// is being deleted must still describe the resources that existed at the
    /// index it claims.
    #[must_use]
    pub fn subtree(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
    ) -> Vec<(ResourceType, ResourceId)> {
        if self.get(resource_type, resource_id).is_none() {
            return Vec::new();
        }
        let mut collected = Vec::new();
        self.walk_subtree(resource_type, resource_id, &mut collected);
        collected
    }

    fn walk_subtree(
        &self,
        resource_type: ResourceType,
        resource_id: &str,
        collected: &mut Vec<(ResourceType, ResourceId)>,
    ) {
        if let Some(children) = self.children.get(resource_id) {
            for child_id in children {
                if let Some(child) = self.find_any(child_id) {
                    let child_type = child.resource_type;
                    self.walk_subtree(child_type, child_id, collected);
                }
            }
        }
        collected.push((resource_type, resource_id.to_owned()));
    }

    /// Drop a non-extant resource entirely.
    fn forget_record(&mut self, resource_type: ResourceType, resource_id: &str) {
        let removed = self.by_type.get_mut(resource_type).remove(resource_id);
        if let Some(resource) = removed {
            for order in Order::ALL {
                let cursor = match order {
                    Order::Created => resource.created,
                    Order::Updated => resource.updated,
                };
                self.indexes
                    .get_mut(resource_type)
                    .get_mut(order)
                    .remove(cursor, resource_id);
            }
            if self.type_of.get(resource_id) == Some(&resource_type) {
                self.type_of.remove(resource_id);
            }
            self.children.remove(resource_id);
            if let Some(parent) = resource.parent_id.as_deref()
                && let Some(siblings) = self.children.get_mut(parent)
            {
                siblings.remove(resource_id);
            }
        }
    }

    /// Which tombstones have outlived the forget interval, in a fixed order.
    ///
    /// A pure query: it decides what stage two *would* drop without dropping
    /// anything, so the decision can be made in one place and applied in
    /// another. That split is what the distributed backends need -- one member
    /// decides, every member applies the same list -- and it is why the clock is
    /// read here and never in [`Self::forget`].
    ///
    /// Sorted by `(type, id)` so two members handed the same store produce
    /// identical lists.
    #[must_use]
    pub fn forgettable(&self, now: Option<i64>) -> Vec<(ResourceType, ResourceId)> {
        let moment = now.unwrap_or_else(health_now);
        let forget_before = moment.saturating_sub(self.forget_interval);
        let mut victims: Vec<(ResourceType, ResourceId)> = ResourceType::ALL
            .into_iter()
            .flat_map(|resource_type| {
                self.by_type
                    .get(resource_type)
                    .values()
                    .filter(move |resource| !resource.extant && resource.health() < forget_before)
                    .map(move |resource| (resource_type, resource.id.clone()))
            })
            .collect();
        victims.sort_by(|a, b| a.0.singular().cmp(b.0.singular()).then(a.1.cmp(&b.1)));
        victims
    }

    /// Drop one tombstoned resource entirely. Returns whether it was there.
    ///
    /// Reads no clock and makes no policy decision -- [`Self::forgettable`]
    /// does that -- so applying a list of victims produces the same store on
    /// every member regardless of when they apply it.
    ///
    /// Refuses to drop a resource that is still extant: stage two exists to
    /// clear records stage one already retired, and dropping a live resource
    /// here would erase it without the removal grain its subscribers are owed.
    pub fn forget(&mut self, resource_type: ResourceType, resource_id: &str) -> bool {
        let present = self
            .by_type
            .get(resource_type)
            .get(resource_id)
            .is_some_and(|resource| !resource.extant);
        if present {
            self.forget_record(resource_type, resource_id);
        }
        present
    }

    // -- health and garbage collection -----------------------------------

    /// Record a heartbeat for a Node. Returns the new health, or `None`.
    ///
    /// `None` means the Node is not registered and the caller answers 404 --
    /// `Behaviour - Registration.md:112-114`, on which the Node must
    /// re-register all of its resources in order.
    ///
    /// # Ordering
    ///
    /// The refresh walks **children first and the Node last**. That is not
    /// tidiness: garbage collection decides expiry on the *Node's* health and
    /// then cascades, so a collector that observes a fresh Node is thereby
    /// guaranteed its descendants were already refreshed. Refreshing the Node
    /// first would let a concurrent collector see a fresh Node over a
    /// half-refreshed subtree and expire live resources.
    /// Takes `&self`, not `&mut self`, and that is the entire divergence.
    /// Every operation below is a shared read plus an atomic store, so the
    /// highest-rate writer in the system runs under the **read** lock,
    /// concurrently with every other reader. Widening this to `&mut self`
    /// compiles and quietly puts ~3,500 exclusive acquisitions a second back
    /// into the critical section at AMWA scale, with nothing failing to say so.
    pub fn heartbeat(&self, node_id: &str) -> Option<i64> {
        self.get(ResourceType::Node, node_id)?;
        let health = health_now();
        self.set_health_recursive(node_id, health);
        Some(health)
    }

    fn set_health_recursive(&self, resource_id: &str, health: i64) {
        if let Some(children) = self.children.get(resource_id) {
            for child_id in children {
                self.set_health_recursive(child_id, health);
            }
        }
        if let Some(resource_type) = self.type_of.get(resource_id).copied()
            && let Some(resource) = self.by_type.get(resource_type).get(resource_id)
        {
            resource.set_health(health);
        }
    }

    /// The current health of a Node, or `None` if it is not registered.
    #[must_use]
    pub fn node_health(&self, node_id: &str) -> Option<i64> {
        self.get(ResourceType::Node, node_id)
            .map(RegisteredResource::health)
    }

    /// Which Nodes have fallen silent past the collection interval.
    ///
    /// A pure query, for the same reason [`Self::forgettable`] is one -- and
    /// additionally so that collection can *decide* under a read lock and take
    /// the exclusive lock only to apply. Sorted for reproducibility.
    #[must_use]
    pub fn expirable(&self, now: Option<i64>) -> Vec<ResourceId> {
        let moment = now.unwrap_or_else(health_now);
        let expire_before = moment.saturating_sub(self.gc_interval);
        let mut victims: Vec<ResourceId> = self
            .iter_extant(ResourceType::Node)
            .filter(|node| node.health() < expire_before)
            .map(|node| node.id.clone())
            .collect();
        victims.sort();
        victims
    }

    /// Expire silent Nodes and forget long-dead records.
    ///
    /// `Behaviour - Registration.md:51`: "If heartbeats fail over a period
    /// greater than the garbage collection interval, both the Node and all
    /// registered sub-resources SHOULD be removed from the registry
    /// automatically."
    ///
    /// Because a heartbeat refreshes descendants too, expiry is decided per
    /// resource on health alone; erasing the Node then cascades over a subtree
    /// whose members were all going to expire in the same tick anyway.
    ///
    /// Victims are re-validated against the same captured `now` before being
    /// erased, so a heartbeat arriving between the decision and the application
    /// saves the Node rather than losing a race with it.
    pub fn collect_garbage(&mut self) -> Vec<ResourceEvent> {
        let now = health_now();
        let expire_before = now.saturating_sub(self.gc_interval);
        let mut events = Vec::new();

        for node_id in self.expirable(Some(now)) {
            let still_expired = self
                .get(ResourceType::Node, &node_id)
                .is_some_and(|node| node.health() < expire_before);
            if still_expired {
                events.extend(self.erase_subtree(ResourceType::Node, &node_id));
            }
        }

        // Stage two, routed through the same pure query the distributed
        // backends use, so standalone and distributed share one answer to
        // "which records are past saving". The two stages are independently
        // suppressible, and a backend that disables expiry must not lose
        // forgetting with it.
        for (resource_type, resource_id) in self.forgettable(Some(now)) {
            self.forget(resource_type, &resource_id);
        }

        events
    }

    // -- statistics ------------------------------------------------------

    /// Snapshot the counters behind the periodic status line.
    ///
    /// Mirrors nmos-cpp's `put_resources_statistics`: `total` is every extant
    /// resource across all eight kinds, the per-type counts are extant-only,
    /// and `non_extant` is reported alongside rather than deducted.
    #[must_use]
    pub fn statistics(&self, subscriptions: usize, grains: usize) -> RegistryStatistics {
        let mut per_type: PerType<usize> = PerType::default();
        let mut non_extant: usize = 0;
        let mut most_recent = TaiCursor::MIN;
        let mut least_health: Option<i64> = None;

        for resource_type in ResourceType::ALL {
            let mut live: usize = 0;
            for resource in self.by_type.get(resource_type).values() {
                if resource.extant {
                    live = live.saturating_add(1);
                    if resource.updated > most_recent {
                        most_recent = resource.updated;
                    }
                    let health = resource.health();
                    if least_health.is_none_or(|least| health < least) {
                        least_health = Some(health);
                    }
                } else {
                    non_extant = non_extant.saturating_add(1);
                }
            }
            *per_type.get_mut(resource_type) = live;
        }

        let total = per_type
            .values()
            .sum::<usize>()
            .saturating_add(subscriptions)
            .saturating_add(grains);

        RegistryStatistics {
            total,
            per_type,
            subscriptions,
            grains,
            most_recent_update: most_recent,
            least_health: least_health.unwrap_or_else(health_now),
            non_extant,
        }
    }

    /// Check that the parent/child graph names only resources that exist.
    ///
    /// A separate question from the indexes, and it catches a different kind of
    /// mistake: an id left in `children` after its resource was forgotten is
    /// not a wrong answer, it is a **leak**. Every walk skips it -- the erase
    /// and subtree paths both test `extant` before recursing -- so nothing
    /// misbehaves and the entry simply accumulates, for the life of the
    /// process, in a registry that repeatedly deletes and re-registers.
    ///
    /// That invisibility is why this exists. Removing the cleanup on revive was
    /// measured against the whole suite of both implementations -- 527 Python
    /// tests and every Rust test -- and **nothing failed**. A defect no test
    /// can see is one that gets refactored away as redundant.
    ///
    /// # Errors
    ///
    /// A description of the first dangling reference found.
    pub fn check_children(&self) -> Result<(), String> {
        for (parent, children) in &self.children {
            for child in children {
                let Some(child_type) = self.type_of.get(child) else {
                    return Err(format!(
                        "children[{parent}] names {child}, which the store does \
                         not hold at all",
                    ));
                };
                if !self.by_type.get(*child_type).contains_key(child) {
                    return Err(format!(
                        "children[{parent}] names {child}, which its own type \
                         bucket does not hold",
                    ));
                }
            }
            if !children.is_empty() && !self.type_of.contains_key(parent) {
                return Err(format!(
                    "children[{parent}] has {} entries but {parent} is not in \
                     the store",
                    children.len(),
                ));
            }
        }
        Ok(())
    }

    /// Check that the indexes agree with the buckets.
    ///
    /// The `BTreeSet` indexes hold the paging invariant by construction, but
    /// nothing stops a mutation path from failing to update one of them -- and
    /// the symptom is a resource that pages twice or not at all, which no
    /// ordinary assertion would catch. Tests call this after every operation.
    ///
    /// # Errors
    ///
    /// A description of the first disagreement found.
    pub fn check_indexes(&self) -> Result<(), String> {
        for resource_type in ResourceType::ALL {
            let bucket = self.by_type.get(resource_type);
            for order in Order::ALL {
                let index = self.index(resource_type, order);
                if index.len() != bucket.len() {
                    return Err(format!(
                        "{resource_type}/{}: index holds {} entries, bucket holds {}",
                        order.wire(),
                        index.len(),
                        bucket.len(),
                    ));
                }
                for (cursor, id) in index.iter() {
                    let Some(resource) = bucket.get(id) else {
                        return Err(format!(
                            "{resource_type}/{}: index names {id}, which the bucket \
                             does not hold",
                            order.wire(),
                        ));
                    };
                    let expected = match order {
                        Order::Created => resource.created,
                        Order::Updated => resource.updated,
                    };
                    if cursor != expected {
                        return Err(format!(
                            "{resource_type}/{}: {id} is indexed at {cursor} but the \
                             record says {expected}",
                            order.wire(),
                        ));
                    }
                }
            }
        }
        Ok(())
    }
}

/// Read this type's parent-reference attribute out of the raw JSON.
fn parent_id_of(resource_type: ResourceType, raw: &serde_json::Value) -> Option<ResourceId> {
    let key = resource_type.parent_key()?;
    raw.get(key)
        .and_then(serde_json::Value::as_str)
        .map(str::to_owned)
}

/// TAI seconds from a POSIX instant, for tests and callers stamping health.
#[must_use]
pub fn tai_seconds_from_posix(posix: i64) -> i64 {
    posix.saturating_add(TAI_UTC_OFFSET)
}
