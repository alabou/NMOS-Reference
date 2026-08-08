// NMOS Controller — browser-side helpers.
// SSE carries status badge updates on listing pages; action buttons call
// the JSON proxy endpoints via a thin fetch wrapper. The browser itself
// does not authenticate with NMOS API tokens — admin access is gated by
// a login form that issues an HMAC-signed session cookie (see
// ``nmos/controller/auth.py``). The fetch wrapper does NOT attach any
// Authorization header; it relies on the browser's same-origin cookie
// jar to carry the session back to the controller.

(function () {
  "use strict";

  const PREFIX = "/controller";
  // Bump on every JS change so a console beacon + a CSS/JS cache-bust
  // both confirm the running version in one step.
  const CONTROLLER_JS_VERSION = "43";

  const controller = {};
  window.controller = controller;
  controller.version = CONTROLLER_JS_VERSION;

  // ------------------------------------------------------------------
  // Debug tracing (client side).
  //
  // The server sets ``<html data-debug="1">`` when ``--debug-in-depth``
  // is active. When that flag is on we:
  //   * generate a short hex id per outbound fetch and send it as
  //     ``X-Trace-Id`` so every outbound / inbound event in the
  //     server log correlates to one browser action;
  //   * install capture-phase listeners that POST click / change /
  //     submit events to ``/api/debug/client-event``. The posts
  //     carry the trace id the fetch IS about to use so the
  //     "click → fetch → server → remote" chain grep cleanly.
  // When the flag is off every helper below is a no-op and nothing
  // goes over the wire.
  // ------------------------------------------------------------------

  const DEBUG_ENABLED = (
    document.documentElement.getAttribute("data-debug") === "1"
  );
  // Most recent trace id minted by ``nextTraceId`` — stamped on
  // click/change events so a click and its resulting fetch share
  // one id. Regenerated on every fetch so two independent fetches
  // never collide.
  let _lastTraceId = "";

  function _hex12() {
    // 48 bits of entropy — same length as the server's
    // ``DebugTrace.new_trace_id``.
    const buf = new Uint8Array(6);
    (window.crypto || window.msCrypto).getRandomValues(buf);
    return Array.from(buf)
      .map(b => b.toString(16).padStart(2, "0"))
      .join("");
  }

  controller.nextTraceId = () => {
    if (!DEBUG_ENABLED) return "";
    _lastTraceId = _hex12();
    return _lastTraceId;
  };

  controller.logClientEvent = (kind, fields) => {
    if (!DEBUG_ENABLED) return;
    try {
      const body = JSON.stringify(Object.assign(
        { kind: kind, trace_id: _lastTraceId || undefined },
        fields || {},
      ));
      // ``keepalive: true`` lets the POST survive an immediate
      // navigation (important for submit events that navigate away).
      fetch(`${PREFIX}/api/debug/client-event`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body,
        keepalive: true,
      }).catch(() => { /* debug must never affect user flow */ });
    } catch (_e) { /* swallow — best-effort instrumentation */ }
  };

  if (DEBUG_ENABLED) {
    const _capture = (evt) => {
      const tgt = evt.target;
      if (!tgt || !(tgt instanceof Element)) return;
      // Stamp a fresh trace id on every user interaction so the next
      // fetch the page issues will carry it — unless the interaction
      // is just a modifier key or a passive move.
      if (evt.type === "click" || evt.type === "submit"
          || evt.type === "change") {
        controller.nextTraceId();
      }
      const fields = {
        event: evt.type,
        tag: tgt.tagName || "",
        id: tgt.id || "",
        name: tgt.getAttribute ? (tgt.getAttribute("name") || "") : "",
        text: (tgt.textContent || "").trim().slice(0, 80),
        path: window.location.pathname,
      };
      controller.logClientEvent("ui", fields);
    };
    // Capture phase so we see the event before any handler
    // stopPropagation()'s it. Passive where we can.
    document.addEventListener("click", _capture, true);
    document.addEventListener("submit", _capture, true);
    document.addEventListener("change", _capture, true);

    // ── Page-load timing instrumentation (removed) ─────────────────────
    // To diagnose a slow page transition, re-add a window "load" listener
    // here that posts the Navigation Timing breakdown to the debug log via
    // controller.logClientEvent("perf", {...}). It pins a slow load to a
    // PHASE — straight from the server log, no devtools needed:
    //   stalled  = nav.connectStart  - nav.startTime    // socket pool / queueing
    //   connect  = nav.connectEnd    - nav.connectStart
    //   ttfb     = nav.responseStart - nav.requestStart  // server
    //   response = nav.responseEnd   - nav.responseStart // download
    //   dom      = nav.domComplete   - nav.responseEnd   // render + scripts
    //   total    = nav.loadEventEnd  - nav.startTime
    // where ``const nav = performance.getEntriesByType("navigation")[0]``.
    // For a slow LCP, also log the slow SUBRESOURCES (e.g. a render-blocking
    // CDN asset): performance.getEntriesByType("resource") filtered to
    // ``r.duration > 1000`` → logClientEvent("perf_res", {url, dur_ms, kind}).
    // (A high "stalled" phase points at HTTP/1.1 socket-pool exhaustion —
    // see the EventSource socket-release wiring in initStatusStream.)
  }

  // ------------------------------------------------------------------
  // Navigation-form re-submit guard
  // ------------------------------------------------------------------
  //
  // Every selection / "Continue to configuration" button submits a GET
  // that NAVIGATES to the next page. Browsers cancel an in-flight
  // navigation when a new one starts, so rapidly re-clicking the button
  // makes each click abort the previous navigation before it commits —
  // the page never advances (it was never slow: a single click always
  // works; only the mashing cancels it). The log shows the symptom
  // exactly: N clicks but only the final navigation's GET ever reaches
  // the server. Lock the form on its first real submit and drop further
  // submits until it navigates (or a short safety timeout re-enables it).
  //
  // A blocked submit (HTML5 validation, or an onclick guard like
  // ``submitSelection`` returning false) never fires the ``submit``
  // event, so this only locks forms that are genuinely navigating.
  let _navLocked = new WeakSet();
  const _unlockForm = (form) => {
    _navLocked.delete(form);
    form.querySelectorAll('button[type="submit"], input[type="submit"]')
      .forEach(b => { b.disabled = false; b.classList.remove("is-submitting"); });
  };
  document.addEventListener("submit", (evt) => {
    const form = evt.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (_navLocked.has(form)) { evt.preventDefault(); return; }
    _navLocked.add(form);
    // Dim the submit controls on the NEXT tick — after the navigation has
    // begun — so disabling never cancels the submit we just allowed.
    setTimeout(() => {
      form.querySelectorAll('button[type="submit"], input[type="submit"]')
        .forEach(b => { b.disabled = true; b.classList.add("is-submitting"); });
    }, 0);
    // Self-heal: if no navigation actually happened (an error page with
    // no URL change, an expired-session 401, …) re-enable after a few
    // seconds so the button is never left permanently dead.
    setTimeout(() => _unlockForm(form), 4000);
  }, false);
  window.addEventListener("pageshow", (evt) => {
    // bfcache back/forward restores the page with JS state intact — reset
    // the lock so a navigated-back form submits again.
    if (evt.persisted) {
      _navLocked = new WeakSet();
      document.querySelectorAll("button.is-submitting, input.is-submitting")
        .forEach(b => { b.disabled = false; b.classList.remove("is-submitting"); });
    }
  });

  // Beacon — visible on the browser console when the file is loaded.
  // If you tweak selection / caps behaviour and the old symptoms
  // persist, check this number; a mismatch with what's written here
  // is a stale-cache flag.
  try {
    // eslint-disable-next-line no-console
    console.log(`[nmos-controller.js v${CONTROLLER_JS_VERSION}] loaded`);
  } catch (_e) { /* older browsers may strip console — ignore */ }

  // ------------------------------------------------------------------
  // Fetch wrapper: JSON content-type shim only. The admin Basic-auth
  // credentials are resupplied by the browser automatically on every
  // request against the protected paths.
  // ------------------------------------------------------------------

  controller.apiFetch = (url, opts) => {
    opts = opts || {};
    const headers = new Headers(opts.headers || {});
    if (opts.body && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    // When debug tracing is active, tag every fetch with a trace id so
    // the server-side middleware picks it up and every outbound call
    // that fetch triggers inherits the same id. Reuses the id stamped
    // by the capture-phase listener when the fetch is a direct
    // consequence of a click/submit; otherwise mints a fresh one.
    if (DEBUG_ENABLED && !headers.has("X-Trace-Id")) {
      const tid = _lastTraceId || controller.nextTraceId();
      headers.set("X-Trace-Id", tid);
    }
    opts.headers = headers;
    return fetch(url, opts);
  };

  // ------------------------------------------------------------------
  // Selection model
  // ------------------------------------------------------------------
  //
  // Group selectors are radios (``name="_group"``) — mutually
  // exclusive across the form. Individual selectors are checkboxes
  // (``class="member-check"``) — multiple are selectable.
  //
  // Cascade rules (per user spec):
  //   1. Selecting a group de-selects every other group (native radio).
  //   2. Selecting a group auto-checks all members of that group.
  //   3. De-selecting a group (by choosing a different group, or by
  //      the auto-recompute below) un-checks the members of the group
  //      being de-selected.
  //   4. Individual member checkboxes are independently togglable.
  //   5. Un-checking an individual member whose group was selected
  //      de-selects the group radio (no group is "on" unless ALL its
  //      members are on AND no members outside it are on).
  //   6. Checking individual members such that exactly one group has
  //      all its members on (and no extras) re-selects that group's
  //      radio.

  function _groupMemberIds(radio) {
    return (radio.getAttribute("data-ids") || "")
      .split(",")
      .map(s => s.trim())
      .filter(Boolean);
  }

  function _setCheckboxesChecked(form, ids, state) {
    const wanted = new Set(ids);
    form.querySelectorAll("input.member-check").forEach(cb => {
      if (wanted.has(cb.getAttribute("data-ids"))) cb.checked = state;
    });
  }

  function _uncheckAllMembers(form) {
    form.querySelectorAll("input.member-check").forEach(cb => {
      cb.checked = false;
    });
  }

  function _recomputeGroupRadios(form) {
    // A group radio is "on" iff every member of that group is checked
    // AND no member outside the group is checked. Otherwise off.
    const checkedMemberIds = new Set(
      Array.from(form.querySelectorAll("input.member-check:checked"))
        .map(cb => cb.getAttribute("data-ids")),
    );
    form.querySelectorAll('input[name="_group"]').forEach(radio => {
      const members = _groupMemberIds(radio);
      const memberSet = new Set(members);
      const allMembersChecked = members.every(id => checkedMemberIds.has(id));
      let outsidersChecked = false;
      for (const id of checkedMemberIds) {
        if (!memberSet.has(id)) { outsidersChecked = true; break; }
      }
      radio.checked = allMembersChecked && members.length > 0 && !outsidersChecked;
    });
  }

  function _confineSelectionToOneGroup(form, clickedCb) {
    // Selections are scoped to a single natural group. Each
    // ``.group-tbody`` wraps one group (and its ungrouped fallback is
    // its own tbody too). If the operator checks a member in a
    // different tbody than already-checked members, every checkbox
    // outside this member's tbody is auto-cleared.
    const tbody = clickedCb.closest('.group-tbody');
    if (!tbody) return;
    form.querySelectorAll('input.member-check:checked').forEach(other => {
      if (other === clickedCb) return;
      if (!tbody.contains(other)) other.checked = false;
    });
  }

  // ------------------------------------------------------------------
  // Selection memory (sessionStorage)
  // ------------------------------------------------------------------
  //
  // The Senders / Receivers list pages remember the operator's last
  // selection so it is restored when they navigate back, for the
  // lifetime of the browser session (cleared on close). Opt-in via
  // ``initSelection(formId, {remember: true})``. The compatible-senders
  // page does NOT opt in — it keeps its server-side subscribed-sender
  // pre-select untouched.

  function _selectionKey(formId) {
    return `nmos.selection.${formId}`;
  }

  function _findByDataIds(form, selector, dataIds) {
    return Array.from(form.querySelectorAll(selector))
      .find(el => el.getAttribute("data-ids") === dataIds) || null;
  }

  function _saveSelection(form, formId) {
    try {
      const groupRadio = form.querySelector('input[name="_group"]:checked');
      const members = Array.from(form.querySelectorAll("input.member-check:checked"))
        .map(cb => cb.getAttribute("data-ids"));
      sessionStorage.setItem(_selectionKey(formId), JSON.stringify({
        group: groupRadio ? groupRadio.getAttribute("data-ids") : null,
        members: members,
      }));
    } catch (e) {
      // sessionStorage may be unavailable (private mode / disabled) — the
      // feature degrades to "no memory" rather than throwing.
    }
  }

  function _restoreSelection(form, formId) {
    let saved;
    try {
      const raw = sessionStorage.getItem(_selectionKey(formId));
      if (!raw) return;
      saved = JSON.parse(raw);
    } catch (e) {
      return;
    }
    if (!saved) return;
    let restored = false;
    // A saved group selection takes priority: if that group radio still
    // exists, select it and its members (mirrors the radio-change cascade).
    if (saved.group) {
      const radio = _findByDataIds(form, 'input[name="_group"]', saved.group);
      if (radio) {
        radio.checked = true;
        _uncheckAllMembers(form);
        _setCheckboxesChecked(form, _groupMemberIds(radio), true);
        restored = true;
      }
    }
    // Otherwise restore the individual members that still exist (ids that
    // have since left the registry are simply skipped).
    if (!restored && Array.isArray(saved.members)) {
      saved.members.forEach(id => {
        const cb = _findByDataIds(form, "input.member-check", id);
        if (cb) { cb.checked = true; restored = true; }
      });
    }
    if (restored) _recomputeGroupRadios(form);
  }

  controller.initSelection = (formId, opts) => {
    const form = document.getElementById(formId);
    if (!form) {
      console.warn(`[nmos-controller] initSelection: form '${formId}' not found`);
      return;
    }
    const remember = !!(opts && opts.remember);

    const groupRadios = form.querySelectorAll('input[name="_group"]');
    const memberChecks = form.querySelectorAll("input.member-check");
    console.log(
      `[nmos-controller] initSelection '${formId}': `
      + `${groupRadios.length} group radios, `
      + `${memberChecks.length} member checkboxes`,
    );

    // Group radio clicked → rules 1, 2, 3.
    groupRadios.forEach(radio => {
      radio.addEventListener("change", () => {
        console.log("[nmos-controller] group radio changed:",
                    radio.getAttribute("data-ids"), "checked=", radio.checked);
        if (!radio.checked) return;
        const ids = _groupMemberIds(radio);
        _uncheckAllMembers(form);
        _setCheckboxesChecked(form, ids, true);
      });
    });

    // Individual checkbox toggled → rules 4, 5, 6 + confine to one
    // group (individuals from multiple groups are disallowed).
    memberChecks.forEach(cb => {
      cb.addEventListener("change", () => {
        console.log("[nmos-controller] member-check changed:",
                    cb.getAttribute("data-ids"), "checked=", cb.checked);
        if (cb.checked) _confineSelectionToOneGroup(form, cb);
        _recomputeGroupRadios(form);
      });
    });

    if (remember) {
      // Persist on any user-driven selection change. The cascade helpers
      // above mutate other checkboxes programmatically (no change event),
      // so this form-level listener fires once per real interaction —
      // after those handlers run — capturing the final selection state.
      form.addEventListener("change", () => _saveSelection(form, formId));
      // Restore the last session selection. These list pages carry no
      // server-side pre-select, so this is their only selection source;
      // restoring sets checkboxes programmatically (no change event), so
      // it does not clobber the saved state.
      _restoreSelection(form, formId);
    }
  };

  // ------------------------------------------------------------------
  // Caps page: clicking a constraint-set row both selects it AND
  // toggles its expanded details row (and vice-versa — clicking a
  // second time collapses it). The two actions share one click so the
  // operator never has to hit a tiny target twice.
  // ------------------------------------------------------------------

  function _setRowExpanded(row, expanded) {
    const key = row.getAttribute("data-caps-row");
    if (!key) return;
    const details = document.querySelector(
      `.caps-details-row[data-caps-details-for="${key}"]`,
    );
    if (!details) return;
    if (expanded) {
      details.removeAttribute("hidden");
      row.classList.add("is-expanded");
    } else {
      details.setAttribute("hidden", "hidden");
      row.classList.remove("is-expanded");
    }
    const toggle = row.querySelector(".caps-toggle");
    if (toggle) toggle.textContent = expanded ? "▾" : "▸";
  }

  controller.initCapsRowClick = () => {
    document.querySelectorAll(".caps-row").forEach(row => {
      row.addEventListener("click", (ev) => {
        // Clicks on the radio itself select without toggling — that
        // lets keyboard / precise-click users pick silently.
        if (ev.target && ev.target.tagName === "INPUT") return;

        const radio = row.querySelector('input[type="radio"]');
        if (radio && !radio.disabled) {
          radio.checked = true;
          radio.dispatchEvent(new Event("change", { bubbles: true }));
        }
        _setRowExpanded(row, !row.classList.contains("is-expanded"));
      });
    });
  };

  controller.submitSelection = (
    formId, idsFieldId, modeFieldId, requiredCount,
  ) => {
    // ``requiredCount`` (optional) — the number of senders the
    // operator MUST end up selecting for this page. Used on the
    // compatible-senders page in single/multi-receiver mode: each
    // receiver needs its own sender, so #senders must equal
    // #receivers. When ``requiredCount > 0`` both paths are checked:
    //
    //   * group radio → must resolve to exactly ``requiredCount``
    //     members (a group of N senders pairs with N receivers).
    //   * member checkboxes → exactly ``requiredCount`` boxes checked.
    const form = document.getElementById(formId);
    if (!form) return false;
    const resourceNoun = idsFieldId === "receiver_ids"
      ? "receivers"
      : "senders";

    // Mode inference (the form's ``selection_mode`` hidden field,
    // when present, picks the server-side branch):
    //
    //   group_radio on   → ``group``  (all members of one group, and
    //                       no extras — the recompute helper sets
    //                       the radio when that's true).
    //   1 checkbox       → ``single``
    //   ≥2 checkboxes    → ``subset`` (all in one group, enforced by
    //                       ``_confineSelectionToOneGroup``; the
    //                       group radio is OFF because not every
    //                       member of the group is ticked —
    //                       otherwise the radio would be on and we'd
    //                       be in the ``group`` branch above).
    //
    // Cross-group selection is structurally impossible because the
    // selection handlers auto-clear members outside the clicked
    // group's tbody. If future changes relax that, subset mode
    // should gain an explicit validation here.
    const groupRadio = form.querySelector('input[name="_group"]:checked');
    const checkedMembers = form.querySelectorAll("input.member-check:checked");

    let ids = "";
    let mode = "single";
    if (groupRadio) {
      ids = (groupRadio.getAttribute("data-ids") || "").trim();
      mode = "group";
    } else if (checkedMembers.length === 1) {
      ids = (checkedMembers[0].getAttribute("data-ids") || "").trim();
      mode = "single";
    } else if (checkedMembers.length >= 2) {
      ids = Array.from(checkedMembers)
        .map(cb => cb.getAttribute("data-ids") || "")
        .filter(Boolean)
        .join(",");
      mode = "subset";
    } else {
      alert(
        `Please select one group or one or more individual ${resourceNoun}.`,
      );
      return false;
    }

    if (requiredCount && requiredCount > 0) {
      const count = ids ? ids.split(",").filter(Boolean).length : 0;
      if (count !== requiredCount) {
        alert(
          `Please pick exactly ${requiredCount} sender`
          + (requiredCount === 1 ? "" : "s")
          + " — one per selected receiver."
          + (mode === "group"
              ? ` (The group you picked has ${count}.)`
              : ""),
        );
        return false;
      }
    }

    document.getElementById(idsFieldId).value = ids;
    if (modeFieldId) {
      const modeField = document.getElementById(modeFieldId);
      if (modeField) modeField.value = mode;
    }
    return true;
  };

  // ------------------------------------------------------------------
  // Configure page — Constrain / Activate toggle buttons.
  //
  // Each toggle is off (red) by default and flips on (green) after a
  // successful action. Every top-level toggle applies its action to
  // every sender rendered on the page. Per-sender result text lands
  // in the ``.result-cell[data-result-for]`` cell of each row.
  // ------------------------------------------------------------------

  function _senderIdsFromConfigureForm(form) {
    // Every sender section carries its id on the .caps-row datum and
    // on the device-title cell. Prefer the caps-row because rows with
    // no selected constraint set don't emit one.
    const ids = new Set();
    form.querySelectorAll('.caps-row[data-sender-id]').forEach(tr => {
      const v = tr.getAttribute('data-sender-id');
      if (v) ids.add(v);
    });
    if (ids.size === 0) {
      form.querySelectorAll('.device-title[data-sender-id]').forEach(td => {
        const v = td.getAttribute('data-sender-id');
        if (v) ids.add(v);
      });
    }
    return Array.from(ids);
  }

  function _collectConstraintSetForSender(form, senderId) {
    // A MUX sender renders one caps-row per part (trunk MUX + each
    // VIDEO/AUDIO/DATA sub-layer); a non-mux sender renders exactly one.
    // Build one BCP-004-01 constraint set per row — scoping each row's
    // params by their shared ``data-cs-part`` — and bundle them all into
    // the IS-11 active-constraints body. A non-mux sender therefore still
    // yields a single-element ``constraint_sets`` (one row, part "trunk").
    const rows = Array.from(form.querySelectorAll(
      `.caps-row[data-sender-id="${senderId}"][data-conset-index]`,
    ));
    const sets = rows.map(row => _constraintSetFromRow(form, senderId, row));
    return { constraint_sets: sets };
  }

  function _constraintSetFromRow(form, senderId, capsRow) {
    // Build one BCP-004-01 constraint-set object from a single part's
    // caps-row + its params.
    //
    // Every emitted constraint_set MUST carry
    // ``urn:x-nmos:cap:meta:preference = 100`` — the Node's
    // fix-the-flow path at
    // ``nmos/node/compatibility.py:force_flow_properties_compatibility``
    // silently SKIPS any conset with preference <= 0, so the flow
    // never gets narrowed to match the new constraint. The PUT
    // returns 200 (the constraint is stored), but the sender's
    // ``CompatibilityStatus`` then stays at ``active_constraints_
    // violation`` and the next PATCH-activate returns 500.
    //
    // Trunk CSs (no meta:layer) emit ``enabled=true``. Sub-layer CSs
    // (meta:layer defined + meta:format) emit ``enabled=false`` +
    // ``layer_enabled=true`` + the format/layer metadata — per
    // ``test_mp2t.py::test_config4a_video_layer_constraint_propagates``.
    //
    // ``data-cs-part`` (the CS's flow part) scopes param collection to
    // THIS row, so a MUX's per-part sections don't bleed params into one
    // another. Defaults to "trunk" for backward compatibility.
    const part = capsRow.getAttribute('data-cs-part') || 'trunk';
    const metaFormat = (capsRow.getAttribute('data-cs-meta-format') || '').trim();
    const metaLayerRaw = (capsRow.getAttribute('data-cs-meta-layer') || '').trim();
    const hasLayer = metaLayerRaw !== '' && !Number.isNaN(Number(metaLayerRaw));
    const isSubLayer = hasLayer && metaFormat !== '' && metaFormat !== 'mux';

    const constraintSet = {};
    constraintSet['urn:x-nmos:cap:meta:preference'] = 100;
    if (isSubLayer) {
      constraintSet['urn:x-nmos:cap:meta:enabled'] = false;
      constraintSet['urn:x-matrox:cap:meta:layer_enabled'] = true;
      constraintSet['urn:x-matrox:cap:meta:format'] =
        `urn:x-nmos:format:${metaFormat}`;
      constraintSet['urn:x-matrox:cap:meta:layer'] = Number(metaLayerRaw);
    } else {
      constraintSet['urn:x-nmos:cap:meta:enabled'] = true;
    }

    form.querySelectorAll(
      `[data-sender-id="${senderId}"][data-param-urn][data-cs-part="${part}"]`,
    ).forEach(el => {
      if (el.disabled) return;
      const urn = el.getAttribute('data-param-urn');
      if (el.tagName === 'SELECT' && el.multiple) {
        // ``option.value`` is the JSON-encoded raw typed value the
        // server emitted (see ``_widget_for_constraint`` in
        // handlers.py). Parse it back so booleans stay booleans,
        // numbers stay numbers, etc. — otherwise IS-11 rejects a
        // boolean cap whose enum contains the strings "True"/"False".
        const sel = Array.from(el.selectedOptions).map(o => {
          try { return JSON.parse(o.value); }
          catch (_e) { return o.value; }
        });
        if (sel.length > 0) constraintSet[urn] = { enum: sel };
        return;
      }
      // Single-value cap (single-option enum or pinned min==max range).
      // Uneditable in the UI, but it is a real constraint that MUST be
      // asserted — e.g. ``media_type=[video/H265]`` pins the codec, and
      // a native conset is *entirely* single-value caps. ``data-single-
      // value`` is the JSON-encoded raw typed value; ``data-single-shape``
      // says whether to rebuild it as an ``enum`` or a ``minimum/maximum``
      // pin. (Disabled — i.e. transport — single caps were already
      // skipped above, so they are never asserted.)
      if (el.classList.contains('param-single')) {
        const raw = el.getAttribute('data-single-value');
        let v;
        try { v = JSON.parse(raw); }
        catch (_e) { v = raw; }
        if (el.getAttribute('data-single-shape') === 'range') {
          constraintSet[urn] = { minimum: v, maximum: v };
        } else {
          constraintSet[urn] = { enum: [v] };
        }
        return;
      }
      // Range: the server renders the readonly mirror as the full
      // declared range by default, matching multi-select widgets
      // that default to all enum values. Once the user moves the
      // slider, _initRangeSync writes a numeric value to the mirror
      // and the request intentionally narrows to that exact value.
      if (el.classList.contains('param-range-value')) {
        const n = Number(el.value);
        if (!Number.isNaN(n)) {
          constraintSet[urn] = { minimum: n, maximum: n };
          return;
        }
        const mn = Number(el.getAttribute('data-range-min'));
        const mx = Number(el.getAttribute('data-range-max'));
        if (!Number.isNaN(mn) && !Number.isNaN(mx)) {
          constraintSet[urn] = { minimum: mn, maximum: mx };
        }
      }
    });
    return constraintSet;
  }

  // ``target`` controls which result cell is updated:
  //   * ``"sender"``   (default) → ``data-result-for="<sender-id>"``
  //   * ``"receiver"`` → ``data-result-for-receiver="<receiver-id>"``
  // The receivers-configure page has BOTH cells per row — sender
  // actions land in the sender cell, receiver actions in the
  // receiver cell, so outcomes don't overwrite each other.
  // ------------------------------------------------------------------
  // Cert-required reactive alert (Phase 5B).
  //
  // The server's _remote_envelope sets ``error_kind = "client_cert_required"``
  // on a write that came back 401 with WWW-Authenticate=nmos-mtls. The
  // server also adds the device to admin.cert_required_devices, so the
  // NEXT page render will paint that Device's group box light red and
  // disable its action buttons. Until the operator reloads, we show an
  // inline alert at the top of the page so the failed click isn't
  // silent: the alert explains the cause and offers a Reload button.
  // ------------------------------------------------------------------

  function _maybeAlertCertRequired(envelope) {
    if (!envelope || envelope.error_kind !== "client_cert_required") return;
    const deviceId = envelope.device_id || "(unknown device)";
    const msg = envelope.message
      || "Client certificate required for write operations on this device.";

    // Anchor the alert above the page's main form so it sits where
    // the operator's eyes return after clicking.
    let host = document.querySelector("#senders-form, #receivers-form");
    if (host && host.parentElement) host = host.parentElement;
    else host = document.querySelector(".col-12") || document.body;

    // De-duplicate per-device: one alert per device-id, replace its
    // body if the same device fails again so the operator sees the
    // latest message without a stack of alerts piling up.
    let banner = host.querySelector(
      `.controller-cert-required-alert[data-device-id="${deviceId}"]`,
    );
    if (!banner) {
      banner = document.createElement("div");
      banner.className =
        "alert alert-danger alert-dismissible fade show "
        + "controller-cert-required-alert";
      banner.setAttribute("role", "alert");
      banner.setAttribute("data-device-id", deviceId);
      host.insertBefore(banner, host.firstChild);
    }
    banner.innerHTML = `
      <strong>Client certificate required.</strong>
      ${msg}
      <button type="button" class="btn btn-sm btn-outline-light ml-3"
              onclick="window.location.reload()">Reload page</button>
      <button type="button" class="close" data-dismiss="alert"
              aria-label="Close">
        <span aria-hidden="true">&times;</span>
      </button>
    `;
  }

  function _setResultCell(resourceId, cls, text, title, target) {
    const attr = (target === 'receiver')
      ? 'data-result-for-receiver' : 'data-result-for';
    const cell = document.querySelector(
      `.result-cell[${attr}="${resourceId}"]`,
    );
    if (cell) {
      cell.className = `result-cell ${cls}`;
      cell.textContent = text;
      // ``title`` carries the full remote body (for an error) so
      // hovering the cell reveals the detail — the cell itself is
      // too narrow to show a long NError ``debug`` payload inline.
      if (title) cell.setAttribute('title', title);
      else cell.removeAttribute('title');
    }
  }

  function _pairedReceiverId(form, senderId) {
    // Pair by index: form carries ``data-sender-ids`` and
    // ``data-receiver-ids`` as aligned CSV lists. Position of the
    // sender in its list → matching receiver at the same position.
    // This is what the receivers-configure page uses when the
    // receiver-activate toggle flips.
    const sCsv = form.getAttribute('data-sender-ids') || '';
    const rCsv = form.getAttribute('data-receiver-ids') || '';
    const sIds = sCsv.split(',').map(s => s.trim()).filter(Boolean);
    const rIds = rCsv.split(',').map(s => s.trim()).filter(Boolean);
    const idx = sIds.indexOf(senderId);
    return (idx >= 0 && idx < rIds.length) ? rIds[idx] : null;
  }

  async function _fireSenderAction(action, newState, senderId, form) {
    let url;
    let opts;
    if (action === 'constrain') {
      if (newState) {
        url = `${PREFIX}/api/senders/${senderId}/constrain`;
        opts = {
          method: 'POST',
          body: JSON.stringify(_collectConstraintSetForSender(form, senderId)),
        };
      } else {
        url = `${PREFIX}/api/senders/${senderId}/unconstrain`;
        opts = { method: 'POST' };
      }
    } else if (action === 'activate') {
      url = newState
        ? `${PREFIX}/api/senders/${senderId}/activate`
        : `${PREFIX}/api/senders/${senderId}/deactivate`;
      // When activating and the operator has chosen Privacy
      // settings, attach ``{privacy, receiver_id?}`` so the server
      // handler can orchestrate PEP field transfer. ECDH modes
      // additionally need the paired receiver (server reads its
      // ``/active/`` to fetch ``ecdh_receiver_public_key``).
      opts = { method: 'POST' };
      if (newState) {
        const privacyBody = controller.currentPrivacyBody();
        if (privacyBody && privacyBody.privacy) {
          const body = { privacy: privacyBody.privacy };
          const rid = _pairedReceiverId(form, senderId);
          if (rid) body.receiver_id = rid;
          opts.body = JSON.stringify(body);
        }
      }
    } else if (action === 'activate_receivers') {
      // Receivers-path only: each sender's row represents a
      // (sender, receiver) pair. Flip on → PATCH the paired
      // receiver's staged with master_enable=true + the sender's
      // SDP (fetched server-side). Flip off → master_enable=false.
      // The per-sender result cell is repurposed to show the
      // receiver-side outcome on this action — same cell because
      // there is exactly one receiver paired with each sender row.
      const receiverId = _pairedReceiverId(form, senderId);
      if (!receiverId) {
        return {
          ok: false, error: "no receiver paired with this sender",
        };
      }
      if (newState) {
        url = `${PREFIX}/api/receivers/${receiverId}/activate`;
        const body = { sender_id: senderId };
        const privacyBody = controller.currentPrivacyBody();
        if (privacyBody && privacyBody.privacy) {
          body.privacy = privacyBody.privacy;
        }
        opts = {
          method: 'POST',
          body: JSON.stringify(body),
        };
      } else {
        url = `${PREFIX}/api/receivers/${receiverId}/deactivate`;
        opts = { method: 'POST' };
      }
    } else {
      return { ok: false, error: `unknown action: ${action}` };
    }
    try {
      const resp = await controller.apiFetch(url, opts);
      let body = null;
      try { body = await resp.json(); } catch (_e) { /* ignore */ }
      return { ok: resp.ok, status: resp.status, body };
    } catch (err) {
      return { ok: false, error: String(err) };
    }
  }

  function _setToggleState(btn, state) {
    const ariaState = (state === true || state === 'true')
      ? 'true'
      : (state === 'mixed' ? 'mixed' : 'false');
    btn.classList.toggle('btn-toggle-on', ariaState === 'true');
    btn.classList.toggle('btn-toggle-off', ariaState === 'false');
    btn.classList.toggle('btn-toggle-mixed', ariaState === 'mixed');
    btn.setAttribute('aria-pressed', ariaState);
  }

  function _aggregateToggleState(values) {
    if (values.length === 0 || values.every(value => !value)) return 'false';
    if (values.every(Boolean)) return 'true';
    return 'mixed';
  }

  async function _runToggle(btn, form) {
    // The browser normally suppresses click events on ``disabled``
    // buttons, but a programmatic dispatch (e.g. a test harness or a
    // keyboard accessibility shim) would bypass that. Guarding here
    // keeps the Constrain-disabled-when-IS-11-absent invariant from
    // silently sending a doomed PUT. Cheap and defensive.
    if (btn.disabled) return;
    const action = btn.getAttribute('data-action');
    const currentState = btn.getAttribute('aria-pressed') || 'false';
    // Off turns every resource on. On and mixed both drive every resource to
    // the safer off state; a second press can then turn a normalised selection
    // on. This preserves safe-off behaviour without claiming mixed means on.
    const newState = currentState === 'false';
    const senderIds = _senderIdsFromConfigureForm(form);
    if (senderIds.length === 0) return;

    // Receiver-side actions land their outcome in the receiver
    // result cell; sender-side actions land in the sender cell.
    // ``resultIdFor(sid)`` returns the id that identifies the
    // correct cell (paired receiver or the sender itself).
    const isReceiverAction = (action === 'activate_receivers');
    const cellTarget = isReceiverAction ? 'receiver' : 'sender';
    const resultIdFor = (sid) => isReceiverAction
      ? (_pairedReceiverId(form, sid) || '')
      : sid;

    btn.classList.add('is-working');
    btn.disabled = true;
    senderIds.forEach(sid => {
      const id = resultIdFor(sid);
      if (id) _setResultCell(id, 'pending', '…', null, cellTarget);
    });

    let anyFailed = false;
    let successCount = 0;
    for (const sid of senderIds) {
      const resultId = resultIdFor(sid);
      const res = await _fireSenderAction(action, newState, sid, form);
      if (res.ok) {
        successCount += 1;
        const status = res.status != null ? res.status : 'ok';
        _setResultCell(resultId, 'ok', `OK (${status})`, null, cellTarget);
      } else {
        anyFailed = true;
        // ``res.body`` is the controller's envelope
        // ``{status, body, error, message, error_kind, device_id}``.
        // ``message`` is the pre-computed human summary; ``error_kind``
        // is the machine-readable tag we switch on for special UI
        // treatment (e.g. cert-required pops an inline alert).
        const env = res.body || {};
        _maybeAlertCertRequired(env);
        const msg = env.message
          || res.error
          || `HTTP ${res.status || '?'}`;
        // Tooltip: full raw body (stringified) so the operator can
        // hover the cell to see the NError debug / full error text.
        let tip = msg;
        if (env.body !== undefined && env.body !== null) {
          try {
            tip = typeof env.body === 'string'
              ? `${msg}\n\n${env.body}`
              : `${msg}\n\n${JSON.stringify(env.body, null, 2)}`;
          } catch (_e) { /* keep msg */ }
        }
        _setResultCell(resultId, 'error', msg, tip, cellTarget);
      }
    }

    btn.classList.remove('is-working');
    btn.disabled = false;
    // A complete operation has a definite state. A partial operation is mixed;
    // retaining the prior state would deny that some resources changed.
    if (!anyFailed) {
      _setToggleState(btn, newState);
    } else if (successCount > 0) {
      _setToggleState(btn, 'mixed');
    }
  }

  function _initRangeSync(form) {
    // Keep the readonly text mirror in sync with the slider so the
    // form submission reads the latest value.
    form.querySelectorAll('.param-range').forEach(range => {
      const urn = range.getAttribute('data-param-urn');
      const sid = range.getAttribute('data-sender-id');
      const mirror = form.querySelector(
        `.param-range-value[data-param-urn="${urn}"][data-sender-id="${sid}"]`,
      );
      if (!mirror) return;
      range.addEventListener('input', () => { mirror.value = range.value; });
    });
  }

  // ------------------------------------------------------------------
  // Persistence: remember per-sender param edits across page reloads.
  //
  // Key shape: ``nmos_controller.config.v3.<sender_id>.<cs_hash>``
  //   value: JSON { <param_urn>: <array|number|string>, ... }
  //
  // The CS hash is a server-computed SHA-256 of the constraint set's
  // content (see ``_constraint_set_hash`` in handlers.py). Scoping
  // by content means if the device dynamically swaps what lives at
  // a given index — or mutates the set — stored edits against the
  // old content are automatically orphaned and the UI falls back to
  // defaults. The top "Reset" button clears every key for the CSs
  // currently on this page, then reloads so defaults re-render.
  // ------------------------------------------------------------------

  const _STORAGE_PREFIX = 'nmos_controller.config.v3.';

  function _storageKey(senderId, consetHash) {
    return `${_STORAGE_PREFIX}${senderId}.${consetHash}`;
  }

  function _getPersistedRecord(senderId, consetHash) {
    try {
      const raw = localStorage.getItem(_storageKey(senderId, consetHash));
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return (parsed && typeof parsed === 'object') ? parsed : null;
    } catch (_e) {
      return null;
    }
  }

  function _persistRecord(senderId, consetHash, record) {
    try {
      localStorage.setItem(
        _storageKey(senderId, consetHash), JSON.stringify(record),
      );
    } catch (_e) { /* quota exceeded or private mode — ignore */ }
  }

  function _clearPersistedForSenders(senderRecords) {
    // senderRecords: [{senderId, consetHash}, ...]
    senderRecords.forEach(r => {
      try {
        localStorage.removeItem(_storageKey(r.senderId, r.consetHash));
      } catch (_e) { /* ignore */ }
    });
  }

  function _consetHashForSender(form, senderId) {
    const row = form.querySelector(
      `.caps-row[data-sender-id="${senderId}"][data-conset-hash]`,
    );
    if (!row) return null;
    const v = row.getAttribute('data-conset-hash');
    return v != null && v !== '' ? v : null;
  }

  function _sendersWithConset(form) {
    const out = [];
    form.querySelectorAll('.caps-row[data-sender-id][data-conset-hash]')
      .forEach(row => {
        const senderId = row.getAttribute('data-sender-id');
        const consetHash = row.getAttribute('data-conset-hash');
        if (senderId && consetHash) {
          out.push({ senderId, consetHash });
        }
      });
    return out;
  }

  function _snapshotSenderInputs(form, senderId) {
    // Capture the current editable values for one sender as a plain
    // map: param URN → multi-select array / narrowed range number.
    // Untouched ranges are rendered as "min … max" and intentionally
    // omitted so cache restore does not narrow them later.
    const record = {};
    form.querySelectorAll(
      `[data-sender-id="${senderId}"][data-param-urn]`,
    ).forEach(el => {
      if (el.disabled) return;
      const urn = el.getAttribute('data-param-urn');
      if (el.tagName === 'SELECT' && el.multiple) {
        record[urn] = Array.from(el.selectedOptions).map(o => o.value);
        return;
      }
      if (el.classList.contains('param-range-value')) {
        const n = Number(el.value);
        if (!Number.isNaN(n)) record[urn] = n;
      }
    });
    return record;
  }

  function _applyRecordToInputs(form, senderId, record) {
    if (!record || typeof record !== 'object') return;
    form.querySelectorAll(
      `[data-sender-id="${senderId}"][data-param-urn]`,
    ).forEach(el => {
      if (el.disabled) return;
      const urn = el.getAttribute('data-param-urn');
      if (!(urn in record)) return;
      const saved = record[urn];
      if (el.tagName === 'SELECT' && el.multiple && Array.isArray(saved)) {
        const wanted = new Set(saved.map(String));
        Array.from(el.options).forEach(opt => {
          opt.selected = wanted.has(opt.value);
        });
        return;
      }
      if (el.classList.contains('param-range-value')
          && typeof saved === 'number') {
        el.value = String(saved);
        // Sync the slider next to the text mirror.
        const slider = form.querySelector(
          `.param-range[data-sender-id="${senderId}"][data-param-urn="${urn}"]`,
        );
        if (slider) slider.value = String(saved);
      }
    });
  }

  function _hookPersistOnChange(form) {
    // Save a fresh snapshot on any edit. Debounced via ``requestAnimationFrame``
    // so rapid slider moves don't hammer localStorage.
    const scheduled = new Set();
    function saveSoon(senderId, consetHash) {
      const key = `${senderId}.${consetHash}`;
      if (scheduled.has(key)) return;
      scheduled.add(key);
      requestAnimationFrame(() => {
        scheduled.delete(key);
        _persistRecord(
          senderId, consetHash,
          _snapshotSenderInputs(form, senderId),
        );
      });
    }
    form.addEventListener('change', (ev) => {
      const el = ev.target;
      if (!(el instanceof Element)) return;
      const senderId = el.getAttribute('data-sender-id');
      if (!senderId) return;
      const h = _consetHashForSender(form, senderId);
      if (!h) return;
      saveSoon(senderId, h);
    });
    form.addEventListener('input', (ev) => {
      // Capture live slider moves — ``change`` alone doesn't always
      // fire for ``<input type="range">`` across browsers.
      const el = ev.target;
      if (!(el instanceof Element)) return;
      if (el.tagName !== 'INPUT') return;
      if (!el.classList.contains('param-range')) return;
      const senderId = el.getAttribute('data-sender-id');
      if (!senderId) return;
      const h = _consetHashForSender(form, senderId);
      if (!h) return;
      saveSoon(senderId, h);
    });
  }

  function _restoreAllFromStorage(form) {
    _sendersWithConset(form).forEach(({ senderId, consetHash }) => {
      const rec = _getPersistedRecord(senderId, consetHash);
      if (rec) _applyRecordToInputs(form, senderId, rec);
    });
  }

  function _hookResetButton(form) {
    const btn = document.querySelector('.btn-reset');
    if (!btn) return;
    btn.addEventListener('click', () => {
      _clearPersistedForSenders(_sendersWithConset(form));
      window.location.reload();
    });
  }

  controller.initConfigureToggles = (formId) => {
    const form = document.getElementById(formId);
    if (!form) return;
    _initRangeSync(form);
    _restoreAllFromStorage(form);
    _hookPersistOnChange(form);
    _hookResetButton(form);
    document.querySelectorAll('.btn-toggle[data-action]').forEach(btn => {
      btn.addEventListener('click', () => _runToggle(btn, form));
    });
  };

  // ------------------------------------------------------------------
  // Action verbs (Constrain / Unconstrain / Activate / Deactivate)
  // ------------------------------------------------------------------

  controller.runVerb = async (verb, ids, constraintSets, consetIndex) => {
    for (const id of ids) {
      const row = document.querySelector(`#results [data-resource-id="${id}"]`);
      const cell = row && row.querySelector(".result-cell");
      if (cell) { cell.className = "result-cell pending small"; cell.textContent = "…"; }

      let url, opts;
      if (verb === "constrain") {
        url = `${PREFIX}/api/senders/${id}/constrain`;
        opts = { method: "POST", body: JSON.stringify({
          conset_index: consetIndex != null ? Number(consetIndex) : null,
        }) };
      } else if (verb === "unconstrain") {
        url = `${PREFIX}/api/senders/${id}/unconstrain`;
        opts = { method: "POST" };
      } else if (verb === "activate") {
        url = `${PREFIX}/api/senders/${id}/activate`;
        opts = { method: "POST" };
      } else if (verb === "deactivate") {
        url = `${PREFIX}/api/senders/${id}/deactivate`;
        opts = { method: "POST" };
      } else if (verb === "deactivate_receivers") {
        // Special form — sender-side proxy of patch-staged with master_enable=false
        url = `${PREFIX}/api/receivers/${id}/activate`;
        opts = { method: "POST", body: JSON.stringify({
          sender_id: "", master_enable: false,
        }) };
      } else {
        if (cell) { cell.className = "result-cell error small"; cell.textContent = `unknown verb: ${verb}`; }
        continue;
      }

      try {
        const resp = await controller.apiFetch(url, opts);
        const body = await resp.json().catch(() => null);
        if (cell) {
          if (resp.ok) {
            cell.className = "result-cell ok small";
            cell.textContent = `OK (${body && body.status != null ? body.status : resp.status})`;
          } else {
            cell.className = "result-cell error small";
            cell.textContent = `ERR ${resp.status}: ${(body && (body.error || body.message)) || ""}`;
          }
        }
      } catch (err) {
        if (cell) {
          cell.className = "result-cell error small";
          cell.textContent = `network: ${err}`;
        }
      }
    }
  };

  // ------------------------------------------------------------------
  // Connect flow (senders + receivers paired by index)
  // ------------------------------------------------------------------

  controller.runConnect = async (senderIds, receiverIds) => {
    await controller.runVerb("activate", senderIds);
    const n = Math.min(senderIds.length, receiverIds.length);
    for (let i = 0; i < n; i++) {
      const rid = receiverIds[i];
      const sid = senderIds[i];
      const row = document.querySelector(`#results [data-resource-id="${rid}"]`);
      const cell = row && row.querySelector(".result-cell");
      if (cell) { cell.className = "result-cell pending small"; cell.textContent = "…"; }
      try {
        const resp = await controller.apiFetch(
          `${PREFIX}/api/receivers/${rid}/activate`,
          { method: "POST", body: JSON.stringify({ sender_id: sid }) },
        );
        const body = await resp.json().catch(() => null);
        if (cell) {
          if (resp.ok) {
            cell.className = "result-cell ok small";
            cell.textContent = `OK (${body && body.status != null ? body.status : resp.status})`;
          } else {
            cell.className = "result-cell error small";
            cell.textContent = `ERR ${resp.status}: ${(body && (body.error || body.message)) || ""}`;
          }
        }
      } catch (err) {
        if (cell) {
          cell.className = "result-cell error small";
          cell.textContent = `network: ${err}`;
        }
      }
    }
  };

  // ------------------------------------------------------------------
  // SSE: live status badges + facet dots on the listing pages
  // ------------------------------------------------------------------

  // BCP-008 status vocabulary — lowercase-dash form so each value maps
  // directly onto an ``is-<value>`` CSS class.
  const STATUS_CLASSES = [
    "is-inactive",
    "is-healthy",
    "is-partially-healthy",
    "is-unhealthy",
    "is-not-used",
  ];
  const FACETS = ["link", "sync", "conn", "media"];

  function _setStatusClass(el, statusValue) {
    const cls = `is-${statusValue || "inactive"}`;
    for (const c of STATUS_CLASSES) el.classList.remove(c);
    if (!STATUS_CLASSES.includes(cls)) {
      el.classList.add("is-inactive");
    } else {
      el.classList.add(cls);
    }
  }

  function _applyStatusToRow(resourceId, status) {
    status = status || {};
    // Colour from the overall status. Do NOT synthesize "healthy" from
    // ``active`` — a device with no BCP-008 monitor reports "not-used"
    // (grey) for every facet, and we must not paint it green.
    const overall = status.overall || "inactive";

    // A single resource can be rendered in MULTIPLE rows on one page:
    // a mux sender on the configure page has one result cell PER
    // constraint-set row. Every selector here must therefore update
    // ALL matches, not just the first. Using querySelector left the
    // 2nd+ cells with a stale ``data-live-active``, so aggregate state
    // in ``_reconcileConfigureToggles`` kept the Activate toggle green
    // after a deactivate until a full page reload.
    const badgeText = status.monitored
      // Monitored: active/idle text comes from the monitor's overall
      // status (idle when inactive/not-used). Not monitored: colours are
      // grey and the text tracks subscription.active. The badge always
      // carries text so the inline-block baseline stays aligned with the
      // .status-dot siblings — see device_block.html.
      ? ((overall === "inactive" || overall === "not-used") ? "idle" : "active")
      : (status.active ? "active" : "idle");
    document.querySelectorAll(
      `.status-badge[data-resource-id="${resourceId}"]`,
    ).forEach(badge => {
      _setStatusClass(badge, overall);
      badge.textContent = badgeText;
    });

    for (const facet of FACETS) {
      document.querySelectorAll(
        `.status-dot[data-resource-id="${resourceId}"][data-kind="${facet}"]`,
      ).forEach(dot => _setStatusClass(dot, status[facet] || "inactive"));
    }

    // Configure-page state cells. Sender column uses
    // ``data-result-for``; receiver column uses
    // ``data-result-for-receiver``. Both exist on the receivers
    // configure page; only the sender one exists on the senders
    // configure page. ``data-live-active`` caches the live value so
    // ``_reconcileConfigureToggles`` can compute the aggregate state
    // without re-parsing textContent.
    document.querySelectorAll(
      `.result-cell[data-result-for="${resourceId}"]`,
    ).forEach(cell => _updateStateCell(cell, !!status.active));
    document.querySelectorAll(
      `.result-cell[data-result-for-receiver="${resourceId}"]`,
    ).forEach(cell => _updateStateCell(cell, !!status.active));
  }

  function _updateStateCell(cell, isActive) {
    cell.className = "result-cell text-muted small";
    cell.textContent = isActive ? "active" : "idle";
    cell.setAttribute("data-live-active", isActive ? "true" : "false");
    cell.removeAttribute("title");
  }

  // ``data-caps-row`` is ``"<resource-id>-<cs-index>"``. The resource id
  // is a UUID (which contains hyphens), and the CS index is the trailing
  // ``-<digits>`` — strip exactly that to recover the resource id.
  function _capsRowResourceId(attr) {
    if (!attr) return null;
    const m = /^(.*)-\d+$/.exec(attr);
    return m ? m[1] : null;
  }

  // Move the green flow-match highlight on a capabilities page. ``fm`` is
  // {matched_cs_indices: [int,...]} — the CS to green (most-specific per
  // part: a muxed stream greens its trunk AND each sub-layer at once). Each
  // CS row of this resource whose index is in the set gets ``.flow-match``
  // on its label cell; the rest are cleared. Empty set clears all.
  controller.applyFlowMatch = (resourceId, fm) => {
    if (!resourceId || !fm) return;
    const matched = (fm.matched_cs_indices || []).map(String);
    document.querySelectorAll(
      `.caps-row[data-caps-row^="${resourceId}-"]`,
    ).forEach(row => {
      if (_capsRowResourceId(row.getAttribute("data-caps-row")) !== resourceId) {
        return;  // prefix guard: a longer id sharing this id's prefix
      }
      const label = row.querySelector(".cs-label");
      if (!label) return;
      const attr = row.getAttribute("data-caps-row");
      const idx = attr.slice(resourceId.length + 1);
      label.classList.toggle("flow-match", matched.includes(idx));
    });
  };

  // Derive a human-readable value from a canonical flow-match key
  // (mirrors flow_match.flow_match_key on the server: "i:43200",
  // "r:48000/1", "s:video/raw", "b:true"). Used to live-update the range
  // widget's current-value annotation.
  function _flowKeyDisplay(key) {
    if (!key) return "";
    const i = key.indexOf(":");
    let v = i >= 0 ? key.slice(i + 1) : key;
    if (key.startsWith("r:") && v.endsWith("/1")) v = v.slice(0, -2);
    return v;
  }

  // Configuration page: green the multi-value option / single value / range
  // annotation that equals the flow's CURRENT value, per URN. ``valuesByPart``
  // is {part_key: {urn: canonical-key}} from the SSE payload — each element
  // carries its CS's ``data-cs-part`` so a muxed sub-layer widget compares
  // against ITS sub-flow's values, not the trunk's. Independent of the
  // CS-name green (applyFlowMatch) and of the operator's own selection.
  controller.applyFlowValues = (resourceId, valuesByPart) => {
    if (!resourceId || !valuesByPart) return;
    const want = (el) => {
      const vals = valuesByPart[el.getAttribute("data-cs-part") || "trunk"];
      return vals ? vals[el.getAttribute("data-param-urn")] : undefined;
    };
    // Multi-value <select multiple>: flag the matching <option>.
    document.querySelectorAll(
      `select[data-sender-id="${resourceId}"][data-param-urn]`,
    ).forEach(sel => {
      const w = want(sel);
      sel.querySelectorAll("option[data-flow-key]").forEach(opt => {
        opt.classList.toggle(
          "flow-match", w != null && opt.getAttribute("data-flow-key") === w,
        );
      });
    });
    // Single-value pinned input.
    document.querySelectorAll(
      `.param-single[data-sender-id="${resourceId}"][data-param-urn]`,
    ).forEach(inp => {
      const w = want(inp);
      inp.classList.toggle(
        "flow-match", w != null && inp.getAttribute("data-flow-key") === w,
      );
    });
    // Range widget: no discrete option — update the current-value annotation.
    document.querySelectorAll(
      `.param-flow-value[data-sender-id="${resourceId}"][data-param-urn]`,
    ).forEach(ann => {
      const w = want(ann);
      if (w == null) { ann.classList.add("d-none"); return; }
      ann.setAttribute("data-flow-key", w);
      ann.textContent = "current: " + _flowKeyDisplay(w);
      ann.classList.remove("d-none");
    });
  };

  let _eventSource = null;

  function _closeStatusStream() {
    if (_eventSource) {
      try { _eventSource.close(); } catch (_e) { /* ignore */ }
      _eventSource = null;
    }
  }

  function _openStatusStream() {
    // Already open, or the page is hidden (a background/bfcached page must
    // not hold a socket — see the lifecycle wiring in initStatusStream).
    if (_eventSource || document.hidden) return;
    // Collect ids from every place the page might render a live
    // status indicator:
    //   * ``.status-badge[data-resource-id]`` — listing pages.
    //   * ``.result-cell[data-result-for]``   — configure pages
    //     (sender column).
    //   * ``.result-cell[data-result-for-receiver]`` — receivers
    //     configure page (receiver column).
    // The union is what we subscribe to; SSE delivers only matching
    // ids, and ``_applyStatusToRow`` updates whichever elements are
    // present on the current page.
    const ids = new Set();
    document.querySelectorAll(".status-badge[data-resource-id]").forEach(el => {
      const v = el.getAttribute("data-resource-id");
      if (v) ids.add(v);
    });
    document.querySelectorAll(".result-cell[data-result-for]").forEach(el => {
      const v = el.getAttribute("data-result-for");
      if (v) ids.add(v);
    });
    document.querySelectorAll(
      ".result-cell[data-result-for-receiver]",
    ).forEach(el => {
      const v = el.getAttribute("data-result-for-receiver");
      if (v) ids.add(v);
    });
    // Capabilities pages: each CS row carries ``data-caps-row="<id>-<idx>"``.
    // Subscribe to the owning resource id so the green flow-match
    // highlight tracks live flow changes. The id is a UUID (which itself
    // contains hyphens); the CS index is the trailing ``-<digits>``.
    // Receiver caps pages carry a sender↔receiver pairing on each row
    // (data-caps-receiver). Collect it so the server can recompute the
    // flow-match against the receiver-NARROWED CS list (the rows shown),
    // not the sender's full caps — otherwise the live green lands on the
    // wrong row when narrowing drops/reorders CS. sender caps pages have
    // no such attribute and are unaffected.
    const pairs = [];
    document.querySelectorAll(".caps-row[data-caps-row]").forEach(el => {
      const id = _capsRowResourceId(el.getAttribute("data-caps-row"));
      if (id) ids.add(id);
      const rid = el.getAttribute("data-caps-receiver");
      if (id && rid) {
        const pair = `${id}:${rid}`;
        if (!pairs.includes(pair)) pairs.push(pair);
      }
    });
    if (ids.size === 0) return;

    // EventSource has no custom-header support; the admin session
    // cookie is carried automatically as a same-origin credential.
    let url = `${PREFIX}/api/status-events?ids=${encodeURIComponent(Array.from(ids).join(","))}`;
    if (pairs.length) {
      url += `&pair=${encodeURIComponent(pairs.join(","))}`;
    }
    try {
      _eventSource = new EventSource(url, { withCredentials: true });
    } catch (err) {
      return;
    }

    _eventSource.addEventListener("status", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        _applyStatusToRow(data.id, data.status);
        // Additive: capabilities-page green-highlight (CS name) + the
        // configuration page's multi-value option / single / range green.
        // Present only on flow-change frames; absent on status-only frames.
        if (data.flow_match) {
          controller.applyFlowMatch(data.id, data.flow_match);
          if (data.flow_match.values_by_part) {
            controller.applyFlowValues(data.id, data.flow_match.values_by_part);
          }
        }
        _reconcileConfigureToggles();
        // Privacy panel: the lock on dropdowns + Exclusivity is a
        // function of "is any resource active" — same signal the
        // Activate button uses. Reconcile on every event so a
        // remote deactivation (or any stale cache value the first
        // status push corrects) immediately unlocks the panel.
        _reconcilePrivacyLock();
      } catch (_e) { /* ignore malformed */ }
    });

    _eventSource.onerror = () => {
      // Leave the connection alone — EventSource auto-reconnects.
    };
  }

  controller.initStatusStream = () => {
    _openStatusStream();
    // Release the SSE socket whenever this page is not the active,
    // foreground page, and reclaim it when it is again.
    //
    // An EventSource holds ONE of the browser's ~6 HTTP/1.1 sockets-per-
    // host for its entire life. A page the browser keeps alive after you
    // navigate away (bfcache, or a lingering connection) otherwise keeps
    // that socket — measured at ~65 s each here — and a handful of them
    // exhaust the pool, so the NEXT navigation has no socket to send on
    // and STALLS for tens of seconds (the reported ~20-45 s caps→configure
    // delay; the browser's own Navigation Timing showed it all in
    // "stalled"). Closing on pagehide/visibility-hidden frees the socket
    // immediately; the snapshot on reconnect re-syncs state on return.
    window.addEventListener("pagehide", _closeStatusStream);   // unload + bfcache
    window.addEventListener("pageshow", _openStatusStream);    // incl. bfcache restore
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) _closeStatusStream();
      else _openStatusStream();
    });
  };

  // ------------------------------------------------------------------
  // Privacy panel
  // ------------------------------------------------------------------
  //
  // The Privacy partial on the configure page exposes:
  //   * ``#privacy-protocol`` / ``#privacy-mode`` / ``#privacy-curve``
  //     — Bootstrap selects populated from the server-computed
  //     intersection. Each resource's IS-05 transport-parameter
  //     constraints are intersected at page render.
  //   * ``#privacy-exclusivity`` — toggles the Node Reservation
  //     session lifecycle across every device in the selection.
  //   * ``.privacy-panel[data-node-ids]`` — CSV of unique node ids
  //     the acquire/release endpoints expect (reservation is per-
  //     Node, covering every sender/receiver on that Node).
  //
  // We expose the operator's current choice via
  // ``controller.currentPrivacyBody()`` which the activate-flow in
  // ``_fireSenderAction`` calls to attach ``{privacy, receiver_id}``
  // to the activate POST body.

  let _privacyState = null;

  controller.initPrivacyPanel = () => {
    const panel = document.querySelector(".privacy-panel");
    if (!panel) {
      _privacyState = null;
      return;
    }
    const nodeCsv = panel.getAttribute("data-node-ids") || "";
    const nodeIds = nodeCsv.split(",").map(s => s.trim()).filter(Boolean);
    _privacyState = {
      panel,
      nodeIds,
      protocolEl: panel.querySelector('[data-role="privacy-protocol"]'),
      modeEl:     panel.querySelector('[data-role="privacy-mode"]'),
      curveEl:    panel.querySelector('[data-role="privacy-curve"]'),
      curveGroup: panel.querySelector('[data-role="privacy-curve-group"]'),
      exclusivityEl:
        panel.querySelector('[data-role="privacy-exclusivity"]'),
      statusEl:
        panel.querySelector('[data-role="privacy-reservation-status"]'),
    };
    // Hide Curve on mode change when the selected mode is non-ECDH —
    // the Curve dropdown is irrelevant there and hiding it keeps the
    // form uncluttered. Kept as a DOM-visibility flip, not a removal,
    // so toggling back to an ECDH mode restores it instantly.
    const syncCurveVisibility = () => {
      if (!_privacyState.curveGroup || !_privacyState.modeEl) return;
      const mode = _privacyState.modeEl.value || "";
      const visible = mode.startsWith("ECDH_");
      _privacyState.curveGroup.style.display = visible ? "" : "none";
    };
    if (_privacyState.modeEl) {
      _privacyState.modeEl.addEventListener("change", syncCurveVisibility);
      syncCurveVisibility();
    }
    if (_privacyState.exclusivityEl) {
      _privacyState.exclusivityEl.addEventListener("change", _onExclusivityToggle);
    }
    // Run the live reconciler once at page load so the lock state
    // matches the DOM's ``data-live-active`` cells regardless of
    // what the server-rendered snapshot said. Fixes the "panel is
    // locked though nothing is active" case where the cache held
    // a stale subscription.active value at render time.
    _reconcilePrivacyLock();
    // No unload-time release.
    //
    // There used to be a ``beforeunload`` beacon here posting
    // ``/api/privacy/release?all=true``, meaning to clean up when the
    // browser closed. But ``beforeunload`` fires on *document* unload,
    // which includes ordinary in-app navigation — so simply leaving the
    // configure page to look at the live status on Senders/Receivers
    // dropped every reservation the admin held. That is not the intended
    // behaviour: a reservation exists precisely to be held across a
    // multi-step operation.
    //
    // A reservation is released on **admin logout** and on **app
    // shutdown**, which is what ``SessionStore.release_all`` documents
    // itself as being for. Both already happen server-side:
    // ``logout_handler`` calls ``release_all`` and ``SessionStore.stop``
    // releases everything on cleanup.
    //
    // Consequence, accepted deliberately: closing the tab without
    // signing out leaves the reservation held until the admin signs in
    // and out again, or the controller restarts. Holding a reservation
    // slightly too long is the safe direction to fail — releasing one
    // while an operator is still working is not.
  };

  // Returns the current operator choice as an object suitable for
  // merging into an activate POST body, or ``null`` when no Privacy
  // panel is on the page or PEP is not available. Shape:
  //   { privacy: { protocol, mode, curve? }, exclusivity: bool }
  controller.currentPrivacyBody = () => {
    if (_privacyState === null) return null;
    const proto = _privacyState.protocolEl && _privacyState.protocolEl.value;
    const mode  = _privacyState.modeEl && _privacyState.modeEl.value;
    if (!proto || !mode) return null;
    const privacy = { protocol: proto, mode };
    if (mode.startsWith("ECDH_")
        && _privacyState.curveEl && _privacyState.curveEl.value) {
      privacy.curve = _privacyState.curveEl.value;
    }
    return {
      privacy,
      exclusivity: !!(_privacyState.exclusivityEl
                      && _privacyState.exclusivityEl.checked),
    };
  };

  async function _onExclusivityToggle(ev) {
    if (_privacyState === null) return;
    const checked = !!ev.target.checked;
    const nodeIds = _privacyState.nodeIds;
    if (nodeIds.length === 0) return;
    const url = checked
      ? `${PREFIX}/api/privacy/acquire`
      : `${PREFIX}/api/privacy/release`;
    // Panel state classes drive the padlock icon:
    //   is-reserved → closed green lock (session held on every Node)
    //   is-pending  → amber lock (acquire / release in flight)
    //   (neither)   → open grey lock (not reserved, the default)
    // PEP negotiability lives on its own indicator and is NOT
    // affected here — a reservation failure must not turn the PEP
    // dot red.
    const setStatus = (text, kind) => {
      if (_privacyState.statusEl) _privacyState.statusEl.textContent = text;
      const panel = _privacyState.panel;
      if (!panel) return;
      panel.classList.toggle("is-reserved", kind === "reserved");
      panel.classList.toggle("is-pending", kind === "pending");
    };
    setStatus(checked ? " · acquiring…" : " · releasing…", "pending");
    try {
      const resp = await controller.apiFetch(url, {
        method: "POST",
        body: JSON.stringify({ node_ids: nodeIds }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        // Revert the UI toggle on failure so the displayed state
        // matches the server's.
        ev.target.checked = !checked;
        const failed = (body && body.failed) || [];
        // A reservation held by someone else is the common, explicable
        // failure — and the Node tells us who, via the ``Link`` header it
        // returns with its 423. Say that plainly rather than echoing a raw
        // reason string: "held by administrator" is actionable, "423" is not.
        const describe = (f) => {
          if (f.locked === "true") {
            return f.owner
              ? `already held by ${f.owner}`
              : "already held by another controller";
          }
          return `${f.node_id}: ${f.reason}`;
        };
        const summary = failed.length
          ? [...new Set(failed.map(describe))].join("; ")
          : `HTTP ${resp.status}`;
        // Acquire failed → stay not-reserved (open lock).
        // Release failed → keep the previous reserved state rather
        // than lying about it; flip back to reserved in that case.
        setStatus(` · failed — ${summary}`,
                  checked ? null : "reserved");
        return;
      }
      if (checked) {
        const acquired = (body && body.acquired) || [];
        setStatus(
          ` · reserved on ${acquired.length} node${acquired.length === 1 ? "" : "s"}`,
          "reserved",
        );
      } else {
        setStatus(" · released", null);
      }
    } catch (_e) {
      ev.target.checked = !checked;
      setStatus(" · transport error", "blocked");
    }
  }

  // Top-row Activate / Receivers-Activate toggles preserve all-on,
  // all-off, and mixed as distinct states. After each SSE push we
  // rescan every per-resource state cell and update the right toggle
  // if the aggregate changed. The Constrain toggle
  // isn't reconciled here — constrained state isn't streamed (see
  // sender_state_map in handlers.py) and stays driven by local
  // action dispatch.
  function _reconcilePrivacyLock() {
    // Live-update the Privacy panel's "locked while active" state to
    // match the SSE-driven ``data-live-active`` flags on the result
    // cells. The server-rendered state is a snapshot of the cache
    // at page-load time; this handler keeps the panel honest as
    // status changes stream in from the RDS WS.
    const panel = document.querySelector(".privacy-panel");
    if (!panel) return;
    const form = panel.querySelector(".privacy-form");
    if (!form) return;             // panel may render the "cannot
                                   // negotiate" banner without a form

    const cells = document.querySelectorAll(
      ".result-cell[data-live-active]",
    );
    if (cells.length === 0) return;  // no state cells on this page
                                     // (e.g. caps-only view)
    const anyActive = Array.from(cells).some(
      c => c.getAttribute("data-live-active") === "true",
    );
    // ``exclusivity-available`` is fixed for the page's lifetime —
    // if the Nodes don't advertise the reservation service at all,
    // the toggle stays disabled regardless of active state.
    const exclusivityAvailable = (
      panel.getAttribute("data-exclusivity-available") === "1"
    );

    // Dropdowns: toggle ``disabled`` to match live activity.
    for (const role of [
      "privacy-protocol", "privacy-mode", "privacy-curve",
    ]) {
      const el = form.querySelector(`[data-role="${role}"]`);
      if (el) el.disabled = anyActive;
    }

    // Exclusivity switch — disable for either reason, leave the
    // label alone. The only label variant ever shown is the fixed
    // "(not available)" from the server render (service-missing),
    // which does not flip at runtime. Avoiding any label mutation
    // keeps the row width perfectly stable across status events.
    const excl = form.querySelector('[data-role="privacy-exclusivity"]');
    if (excl) {
      excl.disabled = anyActive || !exclusivityAvailable;
    }

    // Locked-note: server-rendered inline span on the footer row.
    // Show/hide via the HTML ``hidden`` attribute so the panel
    // layout does not reflow as activity flips — the span always
    // occupies its fixed slot, just visibility changes.
    const note = panel.querySelector('[data-role="privacy-locked-note"]');
    if (note) note.hidden = !anyActive;

    form.setAttribute("data-privacy-locked", anyActive ? "1" : "0");
  }

  function _reconcileConfigureToggles() {
    const senderCells = document.querySelectorAll(
      ".result-cell[data-result-for]",
    );
    const receiverCells = document.querySelectorAll(
      ".result-cell[data-result-for-receiver]",
    );
    const aggregateState = (cells) => _aggregateToggleState(
      Array.from(cells).map(
        c => c.getAttribute("data-live-active") === "true",
      ),
    );
    const setToggle = (action, state) => {
      const btn = document.querySelector(
        `.btn-toggle[data-action="${action}"]`,
      );
      if (!btn) return;
      _setToggleState(btn, state);
    };
    setToggle("activate", aggregateState(senderCells));
    setToggle("activate_receivers", aggregateState(receiverCells));
  }

})();
