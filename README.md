# nmos-reference

A Python reference implementation of an **NMOS Node** with **Matrox NMOS extensions**.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Async](https://img.shields.io/badge/concurrency-asyncio-blueviolet.svg)](https://docs.python.org/3/library/asyncio.html)

---

## What this is

An NMOS Node implementation — covering the AMWA NMOS Interface Specifications (IS-04, IS-05, IS-10, IS-11), a curated set of AMWA Best Current Practices, and selected VSF Technical Recommendations — written in typed Python on top of `asyncio` + `aiohttp`. It is intended as both a working device and a teaching reference.

The security surface — TLS (with cipher and curve restriction), OAuth 2.0 Bearer-token validation with JWKS lifecycle, and CRL handling — is implemented and exercised by the bundled test suite.

---

## Highlights

- **NMOS Interface Specifications** — Discovery (IS-04), Connection (IS-05), Authorization (IS-10), and Stream Compatibility (IS-11; excludes the `Input` / `Output` resources).
- **Capabilities-driven controller** — the embedded NMOS Controller consumes BCP-004-01/-02 Sender / Receiver capabilities and drives IS-11 stream-compatibility negotiation (active + supported constraint sets, parameter constraints) through the **Matrox Capability Constraint Framework (CCF)** in `caps/`. Senders and Receivers are reconfigured at runtime against what the connected peers actually advertise — the matching surface is the device's declared capabilities, not prebaked SDP templates. IS-11 `Input` / `Output` resources are not implemented (see the Matrox NMOS extensions table below).
- **Hierarchical mux capabilities** — transport / container layering for MPEG2-TS, NDI, SRT, and RTSP, plus the AM824 / AES3 audio mux container. IS-11 carries through the mux hierarchy: the Controller negotiates and configures each sub-flow / sub-stream (video, audio, data) independently against the connected peer's per-layer constraints. Audio sub-flows can be PCM, AAC, or AM824; video sub-flows can be raw, JPEG XS, H.264, or H.265 — all selected by capability matching.
- **BCP-008 status reporting over IS-04 (no IS-12 / MS-05-02 dependency)** — Sender / Receiver status flows through IS-04 registration. The status appears as monitor resources in the registry; any controller subscribing to the registry's `/x-nmos/query/v1.3/subscriptions/` WebSocket sees changes asynchronously as grain messages. Any registry-connected controller can observe status without implementing the NC control-protocol stack.
- **VSF Technical Recommendations** — TR-10-13 (privacy-encrypted transport) and TR-10-14 (USB-over-IP and capability sets).
- **Three security configurations**, each with launch scripts and test coverage:
  - **Config A** — Mutual TLS, no OAuth 2.0
  - **Config B** — OAuth 2.0 with server TLS
  - **Config C** — Mutual TLS + OAuth 2.0
- **TLS** — cipher whitelist enforcement, ECDH curve restriction, configurable CRL (GCRL) for revocation handling.
- **Typed JSON serializers** — generated from the NMOS JSON schemas, so the wire format and the in-memory types stay in sync.
- **Typed Python** — `mypy --strict` clean across the `nmos/` package (tests excluded).
- **Asyncio throughout** — aiohttp HTTP / WebSocket servers, `DispatchGroup`-based task lifecycles, errgroup-style cancellation.
- **Test suite** — 2 000+ tests across unit, integration, and end-to-end paths.

---

## One Model — independent + multiplexed streams unified

This implementation follows the Matrox **"One Model to Rule them All"** design, formalised in the [NMOS-MatroxOnly](https://github.com/alabou/NMOS-MatroxOnly) corpus. The model presents two stream topologies — that NMOS controllers typically handle through separate code paths — under a single configuration surface:

- **Group of independent streams** — multiple Senders, one per essence (e.g., one audio Sender + one video Sender + one data Sender).
- **Multiplexed stream** — one Sender carrying multiple sub-streams (e.g., a single MPEG2-TS mux containing video + audio + data).

From the user's point of view the configuration model is the same: both shapes expose `video 0`, `audio 0`, `audio 1`, … and are configured through IS-11 active constraints in the same way. The Controller abstracts the underlying transport / streaming implementation. The constraint-set metadata on each Sender / Receiver — `format`, `layer`, and `layer_compatibility_groups` (alongside the natural-grouping role / role-index from BCP-002-01) — are what let one code path drive both topologies.

In practice this means the same IS-11 negotiation handles MPEG2-TS, RTSP, NDI, USB-over-IP muxes, AM824 audio mux containers, **and** independent RTP / SRT senders for the same set of essences. The Controller asks "what's the user's intent for `video 0` and `audio 0`?" once; the per-Sender/Receiver hints decide whether that becomes a mux-sub-stream configuration or a coordinated independent-Sender configuration.

---

## Quick start

Three pre-configured launch scripts cover the three security configurations.

```bash
# Prerequisites: Python 3.12 or newer, plus the dependencies in pyproject.toml
pip install -e .[dev]

# Config A — mTLS without OAuth 2.0
./start-node1-noauth2.sh

# Config B — OAuth 2.0 with server TLS
./start-node1-nomtls.sh

# Config C — mTLS + OAuth 2.0
./start-node1.sh
```

Each launch script binds the Node on a hostname that matches the configured server-certificate's SAN. Resolve that hostname to `127.0.0.1` in `/etc/hosts` for local testing. Positional arguments to the launch script set the AS host/port and registry host/port — see `--help` in `nmos_node.py` for the full flag surface.

The Node ships a built-in NMOS Controller under `/controller/` on `--nodeControlPort` once you set `--controllerAdminPassword`. See [Controller sign-in](#controller-sign-in).

### External dependencies of the launch scripts

| Component | Config A (`start-node1-noauth2.sh`) | Config B (`start-node1-nomtls.sh`) | Config C (`start-node1.sh`) |
|---|---|---|---|
| **NMOS Registry (IS-04)** | optional | optional | optional |
| **OAuth 2.0 Authorization Server (IS-10)** | not used | **required** | **required** |
| **TLS server certificate** | required | required | required |
| **TLS client certificate** | required (mTLS) | not used | required (mTLS) |

Notes on the dependencies:

- **NMOS Registry**: a single Node runs **standalone** with no registry — pass `--rdsHost ""` (the launch-script default already wires this when no `$3 $4` are supplied). In this mode the embedded NMOS Controller seeds its cache **once at startup** from the local Node's resources, so the Controller UI shows the initial set of senders / receivers / sources / flows. **The cache is not live-updated** afterwards — IS-05 activations, IS-11 reconfigurations, BCP-008 status changes that happen at run-time will not appear in the Controller UI until you point the Node at a real registry. To exercise multi-Node negotiation AND see live updates, use any IS-04-compliant registry (e.g. [nmos-cpp's registry](https://github.com/sony/nmos-cpp), or another instance of this implementation) by passing `$3 $4` positional args on the launch script.

- **OAuth 2.0 Authorization Server**: required for Configs B and C — the Node fetches JWKS from the AS, validates Bearer tokens against the published public keys, and enforces the IS-10 claim semantics (`aud`, `scope`, `x-nmos-*`). Any IS-10-compliant AS works; a [Keycloak](https://www.keycloak.org/) realm is a common choice for production deployments. Pass the AS host / port to the launch script as `$1 $2`. Config A does not contact an AS.
- **TLS material**: each launch script references a server cert / key (and, for mTLS, a client cert / key) and a trust root. Vendors substitute their own PKI by editing the scripts or by running `nmos_node.py` directly with `--nodeCertificate` / `--nodeKey` / `--nodeTrustedRootCA`.

### Dev mode — no TLS

The Node also supports a **no-TLS dev mode** that sits outside Configs A/B/C — plain HTTP on every surface, no OAuth, no client-cert verification. It is **not certifiable under any security spec** (under NMOS With Control Plane Security a device shall not claim compliance while so configured), but is useful for quick connectivity experiments without PKI setup.

Run `nmos_node.py` directly with the disable flags:

```bash
python3 nmos_node.py \
  --nodeDisableTLS --rdsDisableTLS --oauth2DisableTLS \
  --nodeAddr 127.0.0.1 --nodePort 5050 \
  --nodeControlPort 8080 --controllerAdminPassword admin
```

The full flag surface is documented by `--help`.

### Controller sign-in

The embedded Controller is gated by a **password-only login form** at `/controller/login`, checked against `--controllerAdminPassword`. There is **no user name**, and this is **not HTTP Basic auth** — an earlier version of the app used Basic, and a cached `Authorization: Basic` header is now ignored on the way in and stripped before any request is proxied to a Node (see `nmos/controller/auth.py` for the rationale: a native browser popup supports neither logout nor error messaging).

Opening any page unauthenticated redirects to the login form. API paths under `/controller/api/` answer `401` with:

```text
WWW-Authenticate: Session realm="nmos-controller"
```

A successful login sets an `nmos_controller_session` cookie holding `<issued_at>.<base64url(hmac_sha256(sha256(password), issued_at))>`. Because the signing secret derives from the admin password, changing `--controllerAdminPassword` invalidates every outstanding session.

For a scripted client, post the password and keep the cookie:

```bash
curl -c cookies.txt -X POST -d "password=admin" \
  http://127.0.0.1:8080/controller/login          # 302 on success, 401 on a bad password
curl -b cookies.txt http://127.0.0.1:8080/controller/api/senders
```

### Verify the install

```bash
python3 -m pytest -q nmos/ sdp/
```

Runs the full test suite (~3 minutes, 2 000+ tests). See the [Tests](#tests) section for per-module invocation and marker breakdown.

---

## Streaming engine — connectivity emulation

The Node ships with a built-in streaming engine that emulates Sender / Receiver behaviour at the packet level. It lets you validate connectivity, transport setup, encryption, and registry orchestration without real media hardware on either end.

When IS-05 activates a Sender or Receiver, the engine starts (or stops) a flow of structured test packets over the configured transport. Each test packet carries a small fixed-format header that lets the receiving side detect:

- **Packet loss** — via the per-packet sequence counter
- **Late delivery** — via the embedded timestamp (>100 ms threshold)
- **Source mismatch** — the receiver verifies the configured source matches the inbound address
- **Length / framing errors** — packets that aren't the expected size
- **Privacy-encryption integrity** — counter + key-version fields exercise the TR-10-13 PEP path end-to-end

Supported transports out of the box:

| Transport | Use |
|---|---|
| **UDP multicast / unicast** | RTP-style flows, multi-receiver fan-out, MPEG2-TS over UDP |
| **SRT** | Reliable unicast over the public internet |
| **TCP** | Reliable unicast |
| **USB-over-IP** | USB device traffic tunneled over IP per TR-10-14 |

Together with the embedded capabilities-driven NMOS Controller, the streaming engine supports a multi-Node test fabric across any number of machines: IS-05 activations drive real connections, the Controller runs IS-11 stream-compatibility negotiation against the connected peers, and Senders / Receivers are reconfigured against what the device declares in BCP-004-01/-02 — codec profile / level, sampling, bit-depth, packet-time, transport, channel layout. The TLS, OAuth 2.0, PEP, registry registration, and capability negotiation paths are exercised end-to-end.

---

## Specification coverage

### AMWA NMOS Interface Specifications ([specs.amwa.tv](https://specs.amwa.tv/))

| Spec | Role |
|---|---|
| **IS-04** Discovery & Registration | Node API, Registry client |
| **IS-05** Connection Management | Senders, Receivers, staged/active model |
| **IS-10** Authorization | OAuth 2.0 Bearer tokens, JWKS, claims |
| **IS-11** Stream Compatibility | Sender / Receiver capability + constraint support via the Matrox CCF framework (`caps/`); dynamic reconfiguration driven by active constraint sets; per-sub-flow / per-sub-stream configuration for hierarchical mux transports. IS-11 `Input` / `Output` resources are not implemented. |

### AMWA NMOS Best Current Practices ([specs.amwa.tv](https://specs.amwa.tv/))

| BCP | Role |
|---|---|
| **BCP-002-01** Natural Grouping of NMOS Resources | Grouping Sender / Receiver / Source resources into logical units |
| **BCP-002-02** Asset Distinguishing Information | Instance Identifier used by IS-10 audience claims |
| **BCP-003-01** Securing Communications with TLS | TLS cipher whitelist, mTLS, version pinning |
| **BCP-003-02** Authorization with OAuth 2.0 | Token validation, public-key cache, scope semantics |
| **BCP-004-01** Receiver Capabilities | Sender / Receiver capability advertisement |
| **BCP-004-02** Receiver Capabilities — Schemas | Capability/constraint JSON schemas |
| **BCP-005-03** NMOS With Privacy Encryption | Privacy-encrypted Sender / Receiver wiring |
| **BCP-006-01** NMOS With JPEG XS | JPEG XS sender/receiver SDP profile, profile/level/sublevel mapping |
| **BCP-006-02** NMOS With H.264 | H.264 (AVC) sender/receiver SDP profile, profile/level mapping |
| **BCP-006-03** NMOS With H.265 | H.265 (HEVC) sender/receiver SDP profile, profile/level/tier mapping |
| **BCP-007-02** NMOS With USB | USB sender/receiver SDP profile and verification |
| **BCP-008-01** Receiver Monitoring | Receiver status delivered through IS-04 — async WebSocket subscriptions to the NMOS registry's `/x-nmos/query/v1.3/subscriptions/` (no IS-12 / MS-05-02 dependency) |
| **BCP-008-02** Sender Monitoring | Sender status delivered through IS-04 — same async-WebSocket-grain channel; status changes are observable by every controller subscribed to the registry |

### VSF Technical Recommendations ([vsf.tv](https://vsf.tv/technical-recommendations/))

| TR | Role |
|---|---|
| **TR-10-13** Privacy Encryption Protocol | Per-flow AES-CTR encryption, key derivation, RTP adaptation. UDP, SRT, RTSP protocol adaptation also supported. |
| **TR-10-14** USB-over-IP | USB protocol adaptation |

### Matrox NMOS extensions ([NMOS-MatroxOnly](https://github.com/alabou/NMOS-MatroxOnly))

The Matrox extensions are formalised in the [NMOS-MatroxOnly](https://github.com/alabou/NMOS-MatroxOnly) specifications corpus. This implementation covers the subset of that corpus listed below; each entry is integrated with IS-11 and the Matrox CCF so every sub-flow / sub-stream of a hierarchical mux is configured independently by the embedded Controller.

| Extension | Role |
|---|---|
| **NMOS With MPEG2-TS** | MPEG2-TS (H.222.0) mux containing video + audio + data sub-streams; per-sub-stream IS-11 constraint negotiation |
| **NMOS With NDI** | NDI mux sender / receiver — Matrox-extended capability set covering the BCP-007-01 surface |
| **NMOS With RTSP** | RTSP-based receiver with RTP sub-flows; capability-driven RTSP `OPTIONS` / `DESCRIBE` / `SETUP` |
| **NMOS With SRT** | SRT unicast transport (caller / listener), with PEP encryption hand-off |
| **NMOS With USB** | USB device transport (USB-over-IP) — Matrox-extended capability set covering the BCP-007-02 surface, with the TR-10-14 protocol adaptation wired through PEP |
| **NMOS With AES3 (AM824)** | AES3-style audio mux container; per-channel constraint negotiation |
| **NMOS With AAC** | AAC audio sender / receiver with codec-profile constraints |
| **NMOS With H.264** | H.264 (AVC) Sender / Receiver — Matrox-extended capability set covering the BCP-006-02 surface; full profile / level / bitrate negotiation |
| **NMOS With H.265** | H.265 (HEVC) Sender / Receiver — Matrox-extended capability set covering the BCP-006-03 surface; profile / level / tier / bitrate negotiation |
| **NMOS With Privacy Encryption** | PEP integration on every supported transport (RTP, SRT, RTSP, USB) — Matrox-extended capability set covering the BCP-005-03 surface |
| **NMOS With OAuth 2.0** | OAuth 2.0 Bearer-token validation flow — Matrox-extended capability set covering the BCP-003-02 / IS-10 surface (JWKS cache lifecycle, claim semantics, scope / x-nmos-* enforcement, public-key rotation) |
| **NMOS With Status Reporting** | Sender / Receiver monitor resources delivered over IS-04 async WebSocket subscriptions — Matrox-extended capability set covering the BCP-008-01 / BCP-008-02 surface; no IS-12 / MS-05-02 dependency |
| **NMOS With IS-11** | Sender / Receiver capability + constraint flow — Matrox-extended capability set covering the IS-11 surface, with hierarchical-mux sub-flow / sub-stream constraints keyed by `layer`, `format`, and `layer_compatibility_groups`. **Excludes** IS-11 `Input` / `Output` resource support. |
| **NMOS With Reservation API** | Exclusive-acquire / renew / release control surface for protected resources |
| **NMOS With Control Plane Security** | Control-plane security across the IS-04 Node API and the IS-05 / IS-08 / IS-11 / IS-12 / IS-14 control APIs — Matrox-extended security (IS-10 / BCP-003-01 / BCP-003-02) with TLS 1.2/1.3 (PFS, RSA/ECDSA, CRL), mTLS, and OAuth 2.0 Bearer/JWT validation combinable as mTLS-only / OAuth2-only / mTLS+OAuth2, enforcing the three Node Access Policies (Unrestricted Read-Write, Unrestricted Read-Only, Restricted Read-Write) with fail-closed posture. This specification supersedes NMOS With OAuth 2.0. |
| **Capability layer extensions** | Constraint sets keyed by `layer`, `format`, and `layer_compatibility_groups` — the basis for hierarchical mux negotiation |

---

## Tutorials

If you're new to NMOS or to the Matrox extensions, two sets of tutorials are recommended starting points:

- **AMWA NMOS** — concept walk-throughs, architectural overviews, and worked examples are published at [specs.amwa.tv/nmos/info/](https://specs.amwa.tv/nmos/info/). Start here for the foundational vocabulary (Senders / Receivers / Sources / Flows / Devices / Nodes), the registration + discovery model, and how IS-04 / IS-05 / IS-11 fit together.
- **Matrox NMOS Advanced Streaming Architecture (NASA)** — Matrox-extension tutorials covering the hierarchical mux model, the IS-11 sub-flow negotiation pipeline, PEP wiring, capability-layer / compat-groups usage, and the Reservation API. Available at [github.com/alabou/NMOS-MatroxOnly/tree/main/tutorials](https://github.com/alabou/NMOS-MatroxOnly/tree/main/tutorials).

---

## Project layout

```
nmos/                   — Core NMOS implementation
  api/                  — HTTP/WS endpoints, TLS context factories, IS-04/05/...
  controller/           — Built-in NMOS Controller + outbound OAuth2/registry clients
  node/                 — Node resources (senders/receivers/sources/flows/devices), config
  json/                 — Typed JSON serialization engine
  types/generated/      — Auto-generated typed wrappers for all NMOS resource types
  oauth2/               — Bearer token validation, JWKS cache lifecycle
  tasks/                — DispatchGroup wrapping asyncio.TaskGroup
  codec/                — Audio/video codec descriptors (H.264, H.265, AAC, JXSV, AES3, …)
  enums/, ip/, errors/, uuid/  — Domain primitives

sdp/                    — SDP encoding/decoding (Matrox profile)
caps/                   — Capability/constraint framework (Matrox CCF)
pep/                    — Privacy Encryption Protocol (PEP) helpers

nmos_node.py            — Main entry point; parses CLI and starts the server
run_server.py           — Lightweight wrapper for embedding nmos_node from scripts
demo_controller.py      — Standalone demo controller for manual exploration
start-node*.sh          — Launch scripts for the three security configurations
```

---

## Tests

```bash
# Full test suite (~3 minutes; 2 000+ tests)
python3 -m pytest -q nmos/ sdp/

# Per-module
python3 -m pytest -q nmos/oauth2/tests/
python3 -m pytest -q nmos/api/tests/test_tr10_tls.py
```

The test markers are documented in `pyproject.toml`:
- default gate excludes `e2e` and `slow`
- `integration` tests run in-process across multiple modules and remain in the default gate
- `e2e` / `slow` markers cover full-protocol scenarios you can opt in to

---

## Compliance Boundary

This repository implements an NMOS Node and a curated set of VSF IPMX / TR-10-x extensions. Its coverage of the broader Matrox specification corpus is bounded by the spec coverage tables above.

`NMOS-MatroxOnly/` is the broader Matrox documentation corpus. This Python implementation supports only the subset of that corpus that has been validated end-to-end here. See the [NMOS-MatroxOnly](https://github.com/alabou/NMOS-MatroxOnly) repository for the full specification set; the spec coverage tables above list what this implementation exercises.

**IS-05 Bulk interface is intentionally not supported.** The Node implements the per-Sender / per-Receiver IS-05 single-resource endpoints (`/single/...`) but does not expose the `/bulk/...` interface. Bulk operations are out of scope: the Controller drives multi-resource activations as coordinated single-resource calls, which keeps the connection-management state machine uniform across Senders and Receivers and avoids the partial-success semantics of bulk activations. Vendors who need IS-05 Bulk on their own products must add it themselves.

---

## License

[Apache License 2.0](LICENSE) — © 2025-2026 Alain Bouchard.

---

## Acknowledgements

This implementation tracks specifications from three bodies:

- The **AMWA NMOS** Interface Specifications and Best Current Practices maintained by the [Advanced Media Workflow Association](https://www.amwa.tv/) — the work of the AMWA Networked Media Incubator and contributors.
- The **VSF Technical Recommendations** (TR-10 family) maintained by the [Video Services Forum](https://www.videoservicesforum.org/) — the work of the VSF IPMX Task Force.
- The **Matrox NMOS Advanced Streaming Architecture** (NASA) documented in the [NMOS-MatroxOnly](https://github.com/alabou/NMOS-MatroxOnly) specifications corpus.

See each specification for full credits.
