# Atomic State Changes — architecture difference from the Go reference

**Status:** important / affects error-recovery design. Read before touching the
IS-11 constraint-force path (`force_active_constraints`,
`update_sender_to_compliant_flow`, `force_flow_properties_compatibility`,
`update_flow_to_compliant`) or the resource cascade
(`update_source` / `update_flow`).

NMOS "Atomic State Changes": when a Flow's or Source's content changes, the
resource is given a **new UUID** (and bumped version). Referencing resources
(senders' `flow_id`, child flows' `parents`) are repointed to the new id, and
the old id is garbage-collected after the heartbeat period. The point — quoting
the Go reference (`nmosNode.go`, `updateFlowNoMutex`):

> in order to have clean transitions in the registry when information changes we
> change the id of the Flow such that **from the registry it is not possible to
> observe invalid intermediate views of the resources** … [it] prevents Senders
> and Flows referencing the Flow from representing invalid states of the system.

So the *new-id-on-change* rule is the mechanism that keeps registry observers
from ever seeing a half-updated graph (e.g. a sender pointing at a flow whose
content has changed but whose dependents haven't caught up).

## The difference

### Go reference — cascade is INTRINSIC to the update (atomic, per-flow)

`updateFlowToCompliantFlow` writes the forced properties and, when the change
warrants a new id (e.g. a coded↔raw class change), calls
`updateFlowNoMutex(flowId, WithFlow…Flavor())`, which **in one call**: applies
the change → mints a new UUID → bumps the version → repoints every referencing
resource. `forceActiveConstraints` runs this **per flow** — the trunk *and* each
mux sub-flow. There is never a window where a flow's content has changed but its
id / version / references have not: each flow transition is self-contained and
publishable on its own.

### Python (this implementation) — TWO-PHASE: in-place write, then deferred cascade

1. **Phase 1 — write forced properties IN PLACE.**
   `force_flow_properties_compatibility` computes the compliant CapSet, then
   `update_flow_to_compliant` → `update_raw_audio_flow` / `update_coded_audio_flow`
   / … mutate the **live flow value object's fields directly**. No new UUID, no
   version bump, no cascade. This happens for the trunk and **every** mux
   sub-flow during the sub-flow forcing loop.

2. **Phase 2 — cascade, deferred to the end.** Only after *all* mutations are
   written does `force_active_constraints` run the Atomic-State-Change cascade,
   by calling the CRUD `update_source(SourceUpdate())` / `update_flow(FlowUpdate())`
   **with an empty update** purely to trigger "re-ID + version-bump + reference
   fix-up" (the cascade machinery lives only in those CRUD methods). The cascade
   is deferred so the sub-flow forcing loop does not chase parent ids that change
   mid-loop.

The empty-update call is therefore an *idiom for "cascade now"*, not a real
edit — the content was already written in Phase 1.

## Why this matters — error recovery

The two-phase design trades the Go model's per-flow atomicity for the
"defer the cascade" convenience, and that has real consequences:

1. **No rollback.** Phase 1 overwrites the live flow/source objects in place.
   There is no transaction and the previous content is gone. If anything fails
   between Phase 1 and Phase 2 (or partway through Phase 2), the node is left in
   a **half-updated state** — new content under old ids — with no way to revert.

2. **Transient inconsistent registry views are possible.** Between the in-place
   write and the (batched, end-of-operation) cascade+`publish()`, the node's
   internal graph has content/id mismatches. This is precisely the situation the
   Go new-id-per-change rule is designed to make unobservable.

3. **Silent staleness if a flow is not cascaded.** The registry push is
   **version-gated** (`registry.py`: *only resources whose version changed are
   re-POSTed*). A Phase-1 in-place change with no Phase-2 cascade keeps the old
   id **and** version, so the change is **never published** — no error, just
   permanent divergence between the node's state and the registry.

   This is exactly the bug that motivated this note: a mux **sub-flow**
   media-type force (e.g. AAC→L24) was written in Phase 1 but the cascade only
   covered the **trunk**, so the sub-flows kept their id+version and never
   reached the registry. The node showed L24; the registry (and the controller's
   capabilities/green highlight) stayed on the stale AAC. Fixed by cascading
   **each** sub-flow in `force_active_constraints` (regression:
   `test_mp2t.py::test_uuid_cascade_on_subflows`).

## Guidance

- Any new property that the force path can mutate **must** be covered by a
  Phase-2 cascade, or it will silently fail to publish (version-gated). When in
  doubt, assert the resource's id/version actually changed after the force.
- Treat the force→cascade sequence as **not** crash-safe. If robust partial-
  failure recovery is ever required, the principled fix is to converge toward
  the Go model: make the cascade **intrinsic to the write** (route forced writes
  through `update_flow` / `update_source` so each flow transition is atomic and
  self-publishing), dropping the empty-update trigger. That removes both the
  inconsistent-window and the silent-staleness failure modes — at the cost of
  unifying the CapSet-based `update_flow_to_compliant` with the field-based CRUD
  `update_flow`, and cascading sub-flows bottom-up to avoid mid-loop stale ids.
