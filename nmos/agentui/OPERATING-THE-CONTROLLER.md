# Operating the Controller UI — what an agent needs to know first

Domain knowledge for driving the embedded NMOS Controller. None of it is
discoverable from the markup: the UI renders every control as available and lets
the server refuse the ones that are out of order, so an agent that clicks in the
wrong sequence gets a plausible-looking failure with no hint that *ordering* was
the problem.

Read this before writing a scenario. Everything here is either cited to source or
explicitly labelled as operator guidance.

---

## 1. Two ways to reach the configure page

Pick the shorter one when you can.

**Sender-only — no receiver needed.** Use this whenever the goal is to inspect or
constrain a sender. Nothing about configuring a sender requires a compatible
receiver to exist.

```
Senders → tick a sender → [Show capabilities] → pick a constraint set → [Configure]
```

**Routing — required to activate a receiver.** Only take this path when a receiver
is actually involved, because it is longer and can dead-end when no sender is
compatible.

```
Receivers → tick a receiver → [Find compatible senders]
          → tick a sender   → [Show capabilities]
          → pick a constraint set → [Configure]
```

**Receiver capabilities on their own — no sender needed either.** To inspect what a
receiver *accepts*, without routing anything, use the receivers page's **secondary**
submit rather than the primary one. It overrides the form action via
`formaction="/controller/receivers/view-caps"` (`receivers.html:64`), so it skips the
compatible-sender intersection entirely:

```python
session.open_receivers()
session.clear_selection()
session.select_resource(resource_id=…)
session.submit_selection(secondary=True)   # -> receivers/view-caps
session.read_constraint_sets()
```

Worth reaching for when "find compatible senders" comes back empty: an empty
intersection tells you nothing about the receiver itself, whereas this shows its
capabilities directly.

*Source: operator guidance. The route table in `nmos/controller/app.py` allows all
three, and `senders_configure.html` renders without any receiver context.*

---

## 2. Ordering rules — the part that bites

The three master toggles on a configure page are **toggles, not commands**. Each
press flips state, so "press Constrain" means *un*constrain if it is already on.
Read `aria-pressed` before pressing, and press only when the current state differs
from what you want.

### Rule A — a sender must be **inactive** before its constraints can change

Constraining or unconstraining an active sender is refused with **423 Locked**.

```
HTTP 423: { "code": 423, "error": "Locked",
            "debug": "cannot delete active constraints of an active Sender" }
```

*Verified in source:* `nmos/api/handlers_compat.py:189` rejects **setting**
constraints and `:255` rejects **deleting** them, both when
`sender.Subscription.Active` is true. The docstring at `:152` states it outright:
*"423 if the Sender is currently active (locked)"*.

So the working order is **deactivate → constrain → reactivate**, never
constrain-while-running.

### Rule B — a sender must be **active** before its receiver can be activated

Activate the sender first, then the receiver.

*Source: operator guidance.* I did **not** find an explicit API-level guard for
this, so expect the failure mode to be an ineffective or failed activation rather
than a clean refusal like Rule A's 423 — a receiver has no live transport or SDP
to lock onto until its sender is transmitting. Treat a receiver activation that
"succeeds" against an inactive sender with suspicion.

### Rule C — privacy and node ownership need **everything** inactive

The Privacy Encryption parameters (protocol, mode, ECDH curve) and the exclusivity
switch that takes ownership of the Nodes can only be changed while **neither the
sender nor the receiver is active**. The reason is substantive rather than a UI
nicety: PEP parameters and the exclusive key are only mixed into the encryption key
*at activation*, so changing them on a running stream would have no effect on it.

*Verified in source:* `privacy_section.html:159` sets
`lock_all = privacy_view.any_active`, and that single flag disables all four
controls (`:171`, `:186`, `:204`, `:226`). Note **any_active** — one active resource
in the selection locks the lot, not just the one that is running.

The controls carry their own explanation, which the driver reports verbatim:

> *"Deactivate the selection to change — PEP parameters are only applied at
> activation"*
>
> *"Deactivate the selection to change — the exclusive key is only mixed into the
> encryption key at activation"*

### A stale-reason trap on the privacy controls

Once you deactivate through the UI, the privacy controls are re-enabled by
JavaScript **without their `title` being rewritten**. `_reconcilePrivacyLock` in
`controller.js` sets `el.disabled`, the explanatory note's `hidden`, and
`data-privacy-locked` — but never touches the title, which the server rendered from
the locked branch at page load.

So an **enabled** privacy control can still be carrying *"Deactivate the selection
to change…"*. Trust the affordance, not the reason:

- **authoritative:** the control's `disabled` state, the locked note's visibility,
  and `#privacy-form[data-privacy-locked]` — all three are reconciled live;
- **possibly stale:** the `title`, until the next full page load.

Only quote a reason when the control is genuinely refused. The driver's
`BlockedControl.reason` is safe by construction, since it is only ever raised for a
control that *is* blocked.

### The full sequence

Privacy comes early, because Rule C is the strictest precondition of the three.

```
1. Deactivate the sender AND the receiver   (Rules A and C)
2. Configure privacy / take ownership       (Rule C: needs everything inactive)
3. Constrain the sender                     (Rule A: needs the sender inactive)
4. Activate the sender                      (Rule B: must precede the receiver)
5. Activate the receiver
```

Each skipped step has its own distinct symptom: step 1 missing gives a 423 at
step 3, or a `BlockedControl` at step 2; swapping 4 and 5 gives a receiver
activation with nothing to receive.

---

## 3. Symptom → cause

Reach for this when a step fails and the message alone is not enough.

| What you see | What it means |
|---|---|
| `423 Locked — cannot set/delete active constraints of an active Sender` | Rule A. Deactivate the sender first. |
| Receiver activation fails or appears to do nothing | Rule B. Activate its sender first. |
| A toggle press did the *opposite* of what you wanted | Toggles flip state. Check `aria-pressed` before pressing. |
| Result cell reads `active`/`idle` instead of `OK (200)` | The status stream overwrote the outcome. The driver reports this as `results_overwritten_by_status_stream`; the action still happened, but this run cannot say what it returned. |
| `BlockedControl` on the exclusivity switch or a privacy dropdown, reason *"Deactivate the selection to change…"* | Rule C. Deactivate **both** the sender and the receiver — one active resource locks all four privacy controls. |
| `Find compatible senders` yields an empty list | The chosen receiver has no compatible sender. Not an error — take the sender-only path instead. |
| A native alert on submit | The page's own selection guard. The count of selected resources does not match what the next page needs. |
| Every parameter offers exactly one value | You picked a **native** constraint set. Native sets pin one value per parameter; choose a non-native one for flexibility. |
| A parameter widget is disabled | Normal. The device does not expose that adjustment through IS-11 — transport capabilities are commonly read-only. Check `p.editable` before insisting. |
| An action is refused on every attempt for one device | Check that device's circle: `WRITES_BLOCKED` or `READS_BLOCKED` means the Controller is not authorised, and the icon's `title` says why. |
| A detail page shows stale values | Those pages do not poll by design. Click **Refresh**. |
| Unconstrain did not change the configuration | Correct. It deletes the active constraints; the device keeps whatever it was doing. |
| Reset did not restore what you expected | Reset discards *your local edits* and reloads, so you get the server's current view — not a set of defaults, despite the tooltip's wording. |

---

## 4. Selecting: circle for a group, square for one resource

Each row of a list page has **two** selection controls, and they mean different
things:

| Control | Shape on screen | Selects | Markup |
|---|---|---|---|
| Group radio | **circle**, left-most column | the whole natural group at once | `input[type=radio][name="_group"][data-ids="<csv of members>"]` |
| Member checkbox | **square**, next column | that one sender or receiver | `input.member-check[data-ids="<uuid>"]` |

Clicking the circle selects every member of that group. You can then narrow the
selection by clicking individual **squares** to deselect the members you do not
want — so "group, minus a couple" is expressed as one circle click followed by a
few square clicks, not by ticking members one at a time.

```python
session.clear_selection()                             # always first, see §15
session.select_group(member_id=some_member_uuid)      # the circle
session.deselect_resource(resource_id=unwanted_uuid)  # a square, to narrow it
session.read_selection()                              # confirm what will submit
```

Two behaviours to watch for, both of which the driver surfaces rather than hides:

- **Selection is confined to one group.** Ticking a member of a *different* group
  silently unticks everything outside it, with no change event. `select_resource()`
  returns `dropped_ids` naming what the page removed, and journals a warning when
  it is non-empty — otherwise a scenario can submit a set it never chose while the
  hidden field reports it perfectly happily.
- **The hidden fields are the truth.** What actually submits is whatever is in
  `#sender_ids` / `#receiver_ids` / `#selection_mode`, not what you believe you
  clicked. `read_selection()` reads those as rendered, which is why it is worth
  calling before a submit that matters.

## 5. Green means "this is what the stream is doing now"

Green text is the UI telling you which option corresponds to the **current
flow/stream**, and it appears in two places:

- on a **capabilities** page, a green *constraint set name* is the set matching the
  sender's current flow;
- on a **configure** page, a green *value* is the currently-active one for that
  parameter.

That makes green the fastest way to answer "what is this sender actually doing
right now?" without opening the transport file or the flow resource.

Underneath it is a single CSS class, `flow-match`, rendered green
(`#198754`, semi-bold) via `.cs-label.flow-match`, `option.flow-match`,
`.param-single.flow-match`, and `.param-flow-value` — see
`nmos/controller/static/controller.css:373-379`.

The driver exposes it as data rather than colour, since an agent cannot read a
screenshot's pixels reliably:

```python
sets = session.read_constraint_sets()
current = [cs for cs in sets if cs.flow_match]          # the green-named set

params = session.read_parameters(sender_id=…)
for p in params:
    p.flow_matched_options    # the green values among this parameter's options
```

Note this marking is **live**: `applyFlowValues` in `controller.js` adds and removes
`flow-match` as status frames arrive, so a set that is green now may not be after an
activation. Read it when you need it rather than caching it.

## 6. The grey buttons are the NMOS information surface

Most of what you would otherwise go to the Node API for is one click away on the
senders/receivers list page, in the small grey button group on each row
(`btn-outline-secondary`, `partials/device_block.html:139-158`). They are only
rendered when the page is in inspect mode.

| Button | Shows | `RowAction` |
|---|---|---|
| `transport` | live transport parameters — **and the SDP, one click deeper** | `TRANSPORT` |
| `flow` | the Flow resource | `FLOW` |
| `sender` / `receiver` | the IS-04 resource as JSON (label is the *kind*) | `RESOURCE` |
| `device` | the owning Device | `DEVICE` |
| `is-11` | live IS-11 stream-compatibility status | `IS11` |

```python
session.open_row_action(resource_id=…, action=RowAction.TRANSPORT)
session.open_sdp()                 # the SDP link, from the transport page
```

Two things that are easy to get wrong here:

- **The `flow` button is greyed out for an unsubscribed receiver.** A receiver only
  has a flow to show once it is subscribing to a sender, so until then the server
  renders a `<span class="btn disabled">` carrying exactly that explanation:
  *"Receiver is not subscribed to a sender"*. The driver reports it as
  `BlockedControl(rendered_as=SPAN)` with that reason — and it is the case the
  `blocked-controls` scenario demonstrates.
- **The SDP is not on the transport page itself**, it is a *"Show SDP transport
  file"* link on it, rendered only when `has_sdp` is true
  (`transport_detail.html:16-19`). So `open_sdp()` raising `ControlAbsent` means
  this resource has no SDP, not that something is broken.

Locate these by **href suffix, never by label**: the `resource` button's text is the
kind (`sender`/`receiver`), and the IS-11 button reads `is-11` while its path
segment is `is11`.

---

## 7. Configuring parameters: native vs non-native, and read-only

### Native constraint sets pin one value each

A **native** constraint set allows exactly **one value per parameter** — so
selecting it gives you no choice to make. To configure a sender with any
flexibility, pick a **non-native** constraint set, which offers multiple values per
parameter and therefore something to choose between.

Practical consequence for a scenario: if `read_parameters()` comes back with every
widget pinned to a single value, that is very likely a native set doing exactly what
it should, not a UI failure. Look at the other sets before concluding anything.

```python
sets = session.read_constraint_sets()
# A set whose widgets offer one value each is native; check the alternatives when
# the scenario needs room to choose.
params = session.read_parameters(sender_id=…)
choosable = [p for p in params if len(p.options) > 1 and p.editable]
```

### Read-only parameters are normal

Some parameters on the configure page cannot be changed, and that is expected
rather than a fault. It follows from the device's own capabilities and how much
flexibility it exposes through IS-11. **Transport capabilities are commonly
read-only.**

So a `BlockedControl` on a parameter widget is usually information, not an obstacle:
the device is telling you it does not offer that adjustment. The driver reports it
with whatever reason the server supplied and keeps going:

```python
for p in session.read_parameters():
    if not p.editable:
        ...   # p.affordance, p.reason -- expected for transport caps
```

Do not write a scenario that insists on setting a specific parameter without first
checking `p.editable`.

### One state column or two, depending how you arrived

The configure page shows live active/idle state per resource, and **how many
columns you get depends on the path in**:

| Reached from | Shows |
|---|---|
| Receivers → compatible senders → caps → configure | active/idle for **both** the sender and the receiver |
| Senders → caps → configure | active/idle for the **sender only** |

Both are updated live by the status stream. In the markup they are different
attributes, which is why the driver takes a side:

```python
session.read_results()                       # data-result-for          (sender)
session.read_results(receiver_side=True)      # data-result-for-receiver (receiver)
```

Two practical consequences:

- Asking for the receiver side on a page reached from Senders finds nothing —
  correctly, since no receiver is involved in that route.
- The receiver-side cells are the reliable way to learn **which** receivers an
  action actually touched. A demo that instead guessed by matching resource labels
  ended up waiting on a receiver it had never activated, timing out, and reporting
  a working route as unconfirmed.

Because these cells are live, they are also where an action's outcome gets
overwritten — see §12 on capturing results before the next status frame lands.

### What Reset actually does

The Reset button **discards your local edits and reloads the page**, so what you get
back is whatever the server currently renders — the current settings.

*Verified in source:* `controller.js:1090-1092` calls
`_clearPersistedForSenders(...)` then `window.location.reload()`. The edits it
discards are the ones held in `localStorage`, which is also why edits survive a
navigation away and back until Reset is pressed.

⚠️ **Wording discrepancy worth knowing:** the button's own tooltip
(`receivers_configure.html:100`) says *"Discard your edits for the senders on this
page and revert to the constraint-set defaults"*. Mechanically it is a reload, so
what appears is the server's current view rather than anything defaulted. Trust the
mechanism; the tooltip is looser than the behaviour.

---

## 8. The lock and the circle beside each node serial

Every device block on the senders/receivers pages carries its node serial and two
small icons. They answer "can the Controller even talk to this device, and how
safely" — and both hold their explanation in a `title`
(`partials/device_block.html:56-71`).

**The padlock — transport security:**

| Icon | Class | Meaning |
|---|---|---|
| 🔒 closed | `lock-tls-secure` | every control on the device is reached over `https://` |
| 🔓 open | `lock-tls-insecure` | at least one control is plain HTTP |

**The circle — whether the Controller is authorised:**

| Icon | Class | `DeviceAccess` | Meaning |
|---|---|---|---|
| 🟢 check-circle | `bi-check-circle-fill` | `AUTHORIZED` | can read *and* write |
| 🟠 triangle | `text-warning` | `WRITES_BLOCKED` | reads fine, writes refused |
| 🔴 triangle | `text-danger` | `READS_BLOCKED` | cannot read; the whole block also gets `device-inaccessible` |

Read these **before** blaming a failed action on the driver: a device the Controller
cannot write to refuses everything, and the interface has been saying so all along.

```python
session.open_senders()
for dev in session.read_devices():
    dev.serial, dev.tls_secure, dev.access, dev.access_reason
    dev.address, dev.transports, dev.inaccessible
```

---

## 9. Refresh, and why these pages hold still

The NMOS detail pages (`transport`, `flow`, `resource`, `is-11`, `monitor`, `sdp`,
`source`) carry a **Refresh** link, and they deliberately **do not poll**. What you
are looking at is a stable snapshot taken when the page loaded — which is precisely
what makes it usable for comparing before against after. Nothing shifts underneath
you until you ask.

```python
session.press_refresh()    # pull the latest values from the Node and Registry
```

Do not confuse it with **Reset** on the configure page (§7). Refresh fetches newer
server-side *data*; Reset discards your local *edits*. Different pages, different
buttons, different meanings.

Note the contrast with the list pages, which *are* live: their badges and traffic
lights are updated by the status stream without any interaction.

---

## 10. Unconstrain leaves the device as it is

Pressing an already-on **Constrain** toggle sends an *unconstrain*, which **deletes
the sender's active constraints**. That does not reconfigure anything — it removes
the restriction, leaving the device free to keep its current configuration or move
to any other its capabilities allow.

For the nmos-reference node specifically, it **keeps its current settings**.

So unconstraining is not a way to reset a sender to some baseline. If a scenario
wants a known configuration it has to constrain to one, not unconstrain and hope.
And because Rule A applies, the sender must be inactive either way.

---

## 11. Cross-node routes and the reverse-direction buttons

When two independent nodes are running and the sender and receiver of a route live
on **different** nodes, the configure page grows a row of buttons at the bottom for
setting up the companion return paths — **USB** and **talk-back audio**.

The part that is genuinely counter-intuitive:

> The USB sender and the talk-back sender live on the node where the **audio and
> video receivers** are — and vice versa.

Which follows once stated: a return path runs the other way, so its sender sits at
the end where the forward path's receivers sit. Expect to find the reverse sender on
the node you were thinking of as the "receiving" side.

These buttons are the **shape-shifters** described in §14: an `<a href>` when the
companion pair can be resolved, and a `<button disabled>` carrying a `title`
explaining why not when it cannot. One selector finds either, and the driver follows
the tag:

```python
session.open_reverse_direction(group="…")   # navigates, or raises BlockedControl
```

A single-node setup will not show them, so a scenario that needs them must check
rather than assume. Two things make them absent for undramatic reasons:

- both resources are on the same node, so there is no cross-node return path;
- the node was started with a config that has no USB — `config10_nousb` on
  SNX00001, for instance, against `config10` on SNX00002.

---

## 12. Reading status, and where the detail lives

On a list page each resource shows an **active/idle badge** plus four small dots
(`link`, `sync`, `conn`, `media`). The badge is not decoration — **it is a link**.
Clicking it opens the detailed status monitor for that sender or receiver, which is
where the per-facet reasons live when a resource is unhealthy.

In the driver that is a row action like any other:

```python
session.open_row_action(resource_id=…, action=RowAction.MONITOR)
```

It is deliberately located differently from the rest, because the markup puts it
in the status column rather than the `.row-actions` group:

```
a.status-badge-link[href$="/monitor"]        # not  .row-actions a[…]
```

*Source: `partials/device_block.html:167-169`, where the badge is wrapped in an
anchor to `/{kind}s/{id}/monitor` — but only when the page is in inspect mode.*

### The traffic lights

The four dots are a three-colour traffic light per facet, plus grey for
"not monitored". Exact values from `controller.css:295-307`:

| Colour | Class | Meaning | `Health` member |
|---|---|---|---|
| 🟢 green `#2e7d32` | `is-healthy` | facet is fine | `HEALTHY` |
| 🟠 orange `#f59f00` | `is-partially-healthy` | degraded but working | `PARTIALLY_HEALTHY` |
| 🔴 red `#d93025` | `is-unhealthy` | facet is failing | `UNHEALTHY` |
| ⚪ grey `#ced4da` | `is-inactive` / `is-not-used` | idle, or no monitor published at all | `INACTIVE` / `NOT_USED` |

In the driver:

```python
status = session.read_status(resource_id)
status.overall              # Health, from the badge
status.facets["link"]       # Health per facet: link, sync, conn, media
```

### After activating, go back to the list page

This is the natural verification loop, and it matters to the driver as much as to a
person: **the badges and traffic lights only exist on the senders/receivers list
pages**, not on the configure page. So the sequence worth following after an
activation is

```
configure → activate → back to Senders (or Receivers) → watch the traffic lights
                                                      → click the badge for detail
```

The driver's `await_live_status_change()` picks whichever liveness marker the
current page actually has — the badge's health classes on a list page, or a result
cell's `data-live-active` on a configure page. The list page is the better place to
wait, because that is where the per-facet detail appears and where clicking through
to the monitor is possible.

Two things to know before trusting a badge:

- **Colour and text are rewritten by the status stream.** The `is-*` health class
  and the `active`/`idle` text are both server-rendered at page load *and* updated
  live, so their presence proves nothing about liveness. Only a change against the
  page-load baseline does — which is what `await_live_status_change()` measures.
- **Grey (`is-not-used`) is not the same as idle.** It means the device publishes
  no BCP-008 monitor at all, so there is no health to report; `is-inactive` means
  monitored and genuinely idle.

## 13. What the driver already does for you

`nmos/agentui/` encodes the parts that can be encoded:

- `read_toggles()` inspects all three toggles — affordance, reason, and
  `aria-pressed` — **without pressing any**. Use it to decide direction, and to
  demonstrate a refusal without writing anything.
- `press_toggle()` captures each resource's outcome the instant the action
  finishes, before the status stream can overwrite it, and raises `ActionFailed`
  carrying the server's own message (including the 423 text).
- `BlockedControl` carries the server's `title` verbatim as `reason`, plus
  `rendered_as` naming which gating idiom fired.
- Every step is journaled with a screenshot, the controls examined before acting,
  and the named signal awaited.

What it does **not** do is reorder your actions for you. A scenario that wants a
working route has to sequence it, because the driver's job is to act only as an
operator could — and an operator has to know this too.

---

## 14. Gating idioms in this UI

Three ways the server says no, plus one shape-shifter. Worth knowing because they
are not interchangeable and the driver keeps them distinct.

| Rendering | Meaning | Where |
|---|---|---|
| `<button disabled title="…">` | Policy disabled a live control; reason in `title` | configure toggles |
| `<span class="btn disabled">` | Action does not apply to this row — a `<span>` cannot carry `disabled` | row actions |
| Absent from the DOM | Not applicable at all | — |
| `<button disabled>` ↔ `<a href>` | Reverse-direction links change **tag** with state | `receivers_configure.html` |

One composite control is split across two elements: the exclusivity switch is a
Bootstrap `custom-switch` whose `<input>` holds `checked`/`disabled` but is
invisible by design, while the wrapping `<label>` is the visible click target
**and the only place the `title` reason exists**. Read gating from the input and
appearance from the label; classifying either alone gets it wrong.

---

## 15. Rig state is sticky

The scenarios perform **no teardown** by deliberate choice, so a run starts from
wherever the last one left off. Two consequences:

- A scenario must be written to work from any starting state — check
  `aria-pressed` rather than assuming a toggle is off.
- Selection is remembered in `sessionStorage` and parameter edits in
  `localStorage`, so call `clear_selection()` on arrival at any list page.
  Without it, a run can submit resources a *previous* run selected while looking
  entirely deliberate.
