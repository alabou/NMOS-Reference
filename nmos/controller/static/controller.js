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
  const CONTROLLER_JS_VERSION = "41";

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
  }

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

  controller.initSelection = (formId) => {
    const form = document.getElementById(formId);
    if (!form) {
      console.warn(`[nmos-controller] initSelection: form '${formId}' not found`);
      return;
    }

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
      alert("Please select one group or one or more individual senders.");
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
    // Walk every editable param input for this sender, build a
    // BCP-004-01 constraint-set object, and wrap it in the IS-11
    // active-constraints body shape.
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
    const capsRow = form.querySelector(
      `.caps-row[data-sender-id="${senderId}"][data-conset-index]`,
    );
    const metaFormat = (capsRow
      ? capsRow.getAttribute('data-cs-meta-format') || ''
      : ''
    ).trim();
    const metaLayerRaw = (capsRow
      ? capsRow.getAttribute('data-cs-meta-layer') || ''
      : ''
    ).trim();
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
      `[data-sender-id="${senderId}"][data-param-urn]`,
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
      // Range: we look at the text mirror (``.param-range-value``)
      // so the readonly text stays the source of truth when JS
      // updates it from the slider.
      if (el.classList.contains('param-range-value')) {
        const n = Number(el.value);
        if (!Number.isNaN(n)) {
          constraintSet[urn] = { minimum: n, maximum: n };
        }
      }
    });
    return { constraint_sets: [constraintSet] };
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

  async function _runToggle(btn, form) {
    // The browser normally suppresses click events on ``disabled``
    // buttons, but a programmatic dispatch (e.g. a test harness or a
    // keyboard accessibility shim) would bypass that. Guarding here
    // keeps the Constrain-disabled-when-IS-11-absent invariant from
    // silently sending a doomed PUT. Cheap and defensive.
    if (btn.disabled) return;
    const action = btn.getAttribute('data-action');
    const isOn = btn.classList.contains('btn-toggle-on');
    const newState = !isOn;
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
    for (const sid of senderIds) {
      const resultId = resultIdFor(sid);
      const res = await _fireSenderAction(action, newState, sid, form);
      if (res.ok) {
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
    // Flip the toggle only if every sender succeeded — partial
    // failure leaves the button in its prior position so the admin
    // can re-try instead of losing track of intent.
    if (!anyFailed) {
      btn.classList.toggle('btn-toggle-on', newState);
      btn.classList.toggle('btn-toggle-off', !newState);
      btn.setAttribute('aria-pressed', String(newState));
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
  // Key shape: ``nmos_controller.config.v2.<sender_id>.<cs_hash>``
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

  const _STORAGE_PREFIX = 'nmos_controller.config.v2.';

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
    // map: param URN → multi-select array / range number.
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
    const overall = status.overall || (status.active ? "healthy" : "inactive");

    const badge = document.querySelector(
      `.status-badge[data-resource-id="${resourceId}"]`,
    );
    if (badge) {
      _setStatusClass(badge, overall);
      // Only the idle (and not-used) states carry text; coloured states
      // render as empty bars per the BCP-008 indicator convention.
      badge.textContent =
        (overall === "inactive" || overall === "not-used") ? "idle" : "";
    }

    for (const facet of FACETS) {
      const dot = document.querySelector(
        `.status-dot[data-resource-id="${resourceId}"][data-kind="${facet}"]`,
      );
      if (dot) _setStatusClass(dot, status[facet] || "inactive");
    }

    // Configure-page state cells. Sender column uses
    // ``data-result-for``; receiver column uses
    // ``data-result-for-receiver``. Both exist on the receivers
    // configure page; only the sender one exists on the senders
    // configure page. ``data-live-active`` caches the live value so
    // ``_reconcileConfigureToggles`` can compute the any-wise OR
    // without re-parsing textContent.
    const senderCell = document.querySelector(
      `.result-cell[data-result-for="${resourceId}"]`,
    );
    if (senderCell) _updateStateCell(senderCell, !!status.active);
    const receiverCell = document.querySelector(
      `.result-cell[data-result-for-receiver="${resourceId}"]`,
    );
    if (receiverCell) _updateStateCell(receiverCell, !!status.active);
  }

  function _updateStateCell(cell, isActive) {
    cell.className = "result-cell text-muted small";
    cell.textContent = isActive ? "active" : "idle";
    cell.setAttribute("data-live-active", isActive ? "true" : "false");
    cell.removeAttribute("title");
  }

  let _eventSource = null;

  controller.initStatusStream = () => {
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
    if (ids.size === 0) return;

    // EventSource has no custom-header support; the admin session
    // cookie is carried automatically as a same-origin credential.
    const url = `${PREFIX}/api/status-events?ids=${encodeURIComponent(Array.from(ids).join(","))}`;
    try {
      _eventSource = new EventSource(url, { withCredentials: true });
    } catch (err) {
      return;
    }

    _eventSource.addEventListener("status", (ev) => {
      try {
        const data = JSON.parse(ev.data);
        _applyStatusToRow(data.id, data.status);
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
    // Best-effort release when the browser closes / navigates away.
    // Using the beacon keeps the call alive past the page's teardown.
    window.addEventListener("beforeunload", () => {
      try {
        const url = `${PREFIX}/api/privacy/release?all=true`;
        navigator.sendBeacon(url, new Blob([], { type: "application/json" }));
      } catch (_e) { /* ignore */ }
    });
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
        const summary = failed.length
          ? failed.map(f => `${f.node_id}: ${f.reason}`).join("; ")
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

  // Top-row Activate / Receivers-Activate toggles are "any-wise OR":
  // green when at least one resource is in the ``active`` state. After
  // each SSE push we rescan every per-resource state cell and flip
  // the right toggle if the aggregate changed. The Constrain toggle
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
    const anyActive = (cells) => Array.from(cells).some(
      c => c.getAttribute("data-live-active") === "true",
    );
    const setToggle = (action, on) => {
      const btn = document.querySelector(
        `.btn-toggle[data-action="${action}"]`,
      );
      if (!btn) return;
      btn.classList.toggle("btn-toggle-on", on);
      btn.classList.toggle("btn-toggle-off", !on);
      btn.setAttribute("aria-pressed", String(on));
    };
    setToggle("activate", anyActive(senderCells));
    setToggle("activate_receivers", anyActive(receiverCells));
  }

})();
