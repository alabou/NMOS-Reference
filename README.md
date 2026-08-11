# nmos-reference

A Python reference implementation of an **NMOS Node** with **Matrox NMOS extensions**.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Async](https://img.shields.io/badge/concurrency-asyncio-blueviolet.svg)](https://docs.python.org/3/library/asyncio.html)

---

## What this is

An NMOS Node implementation — covering the AMWA NMOS Interface Specifications (IS-04, IS-05, IS-10, IS-11), a curated set of AMWA Best Current Practices, and selected VSF Technical Recommendations — written in typed Python on top of `asyncio` + `aiohttp`. It is intended as both a working device and a teaching reference.

---

## Highlights

- **NMOS Interface Specifications** — Discovery (IS-04), Connection (IS-05), Authorization (IS-10), and Stream Compatibility (IS-11; excludes the `Input` / `Output` resources).
- **Capabilities-driven controller** — the embedded NMOS Controller consumes BCP-004-01/-02 capabilities and drives IS-11 negotiation through the **Matrox Capability Constraint Framework (CCF)** in `caps/`, reconfiguring Senders / Receivers at runtime against what peers actually advertise rather than against prebaked SDP templates. See [Specification coverage](#specification-coverage).
- **Hierarchical mux capabilities** — transport / container layering for MPEG2-TS, NDI, SRT, and RTSP, plus the AM824 / AES3 audio mux container, with IS-11 negotiating each sub-flow / sub-stream (video, audio, data) independently against the peer's per-layer constraints. Audio sub-flows can be PCM, AAC, or AM824; video sub-flows raw, JPEG XS, H.264, or H.265 — all selected by capability matching. See [One Model](#one-model--independent--multiplexed-streams-unified).
- **BCP-008 status reporting over IS-04 (no IS-12 / MS-05-02 dependency)** — Sender / Receiver status flows through IS-04 registration as monitor resources, so any registry-subscribed controller observes changes without implementing the NC control-protocol stack. See [Specification coverage](#specification-coverage).
- **VSF Technical Recommendations** — TR-10-13 (privacy-encrypted transport) and TR-10-14 (USB-over-IP and capability sets).
- **Three security configurations**, each with launch scripts and test coverage:
  - **Config A** — Mutual TLS, no OAuth 2.0
  - **Config B** — OAuth 2.0 with server TLS
  - **Config C** — Mutual TLS + OAuth 2.0
- **TLS** — cipher whitelist enforcement, ECDH curve restriction, configurable CRL (GCRL) for revocation handling.
- **Typed JSON serializers** — generated from the NMOS JSON schemas, so the wire format and the in-memory types stay in sync.
- **Typed Python** — `mypy --strict` clean across the `nmos/` package and the vendored Authorization Server in `fake-as/` (tests excluded).
- **Asyncio throughout** — aiohttp HTTP / WebSocket servers, `DispatchGroup`-based task lifecycles, errgroup-style cancellation.
- **Bundled IS-04 registry** — `nmos_registry.py` serves the Registration and Query APIs (HTTP + WebSocket) so the whole system runs from this checkout with no third-party registry to install. See [NMOS Registry](#nmos-registry).
- **Test suite** — 2 900+ tests across unit, integration, and end-to-end paths.

---

## Quick start

Nothing outside this checkout is required. The repository ships its own IS-04
registry, so a full multi-Node system — registry, Nodes, Controller — comes up
from three terminals:

```bash
# Prerequisites: Python 3.12 or newer, plus the dependencies in pyproject.toml
pip install -e .[dev]

./start-registry-bare.sh     # terminal 1 — IS-04 Registration + Query APIs
./start-node1-bare.sh        # terminal 2
./start-node2-bare.sh        # terminal 3
```

Then open <http://127.0.0.1:5050/controller/> and sign in with the password
`admin`. The Controller discovers both Nodes through the registry and updates
live over the registry's WebSocket. See [NMOS Registry](#nmos-registry).

For TLS and OAuth 2.0 — the fully secured rig — see
[Secured quick start](#secured-quick-start--the-fully-secured-rig-config-c-mutual-tls--oauth-20).

### External dependencies of the launch scripts

| Component | Config A (`start-node1-noauth2.sh`) | Config B (`start-node1-nomtls.sh`) | Config C (`start-node1.sh`) |
|---|---|---|---|
| **NMOS Registry (IS-04)** | optional | optional | optional |
| **OAuth 2.0 Authorization Server (IS-10)** | not used | **required** | **required** |
| **TLS server certificate** | required | required | required |
| **TLS client certificate** | required (mTLS) | not used | required (mTLS) |

Notes on the dependencies:

- **NMOS Registry**: a single Node runs **standalone** with no registry — pass `--rdsHost ""` (the launch-script default already wires this when no `$3 $4` are supplied). In this mode the embedded NMOS Controller seeds its cache **once at startup** from the local Node's resources, so the Controller UI shows the initial set of senders / receivers / sources / flows. **The cache is not live-updated** afterwards — IS-05 activations, IS-11 reconfigurations, BCP-008 status changes that happen at run-time will not appear in the Controller UI until you point the Node at a real registry. To exercise multi-Node negotiation AND see live updates, run the [registry that ships with this repository](#nmos-registry) (`./start-registry.sh`) or any other IS-04-compliant registry such as [nmos-cpp's](https://github.com/sony/nmos-cpp), passing `$3 $4` positional args on the launch script.

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

Verify the install by running the test suite: `python3 -m pytest -q` — see [Tests](#tests).

---

## Secured quick start — the fully secured rig (Config C: mutual TLS + OAuth 2.0)

TLS and OAuth 2.0 everywhere, two Nodes, no Keycloak and no Docker. **Read
[Required before any TLS configuration](#required-before-any-tls-configuration-hosts-file-entries)
first** — without the hosts-file entries every component fails to verify its
peers.

Four terminals, and the order matters: the Nodes need both the Authorization
Server and the registry to be up before they start.

```bash
# terminal 1 — OAuth 2.0 Authorization Server on 9443
./start-fake-as.sh

# terminal 2 — IS-04 registry, mutual TLS (RAP=2)
./start-registry.sh 2

# terminal 3 — Node 1 (SNX00001) + the Controller UI on 5050
./start-node1.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2

# terminal 4 — Node 2 (SNX00002)
./start-node2.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2
```

For a third Node, add a fifth terminal — `start-node3.sh` takes the same
arguments and policy flags:

```bash
./start-node3.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2
```

Three Nodes make the **inaccessible-device** case reachable, which two cannot.
Start the Authorization Server scoped to a subset instead:

```bash
./start-fake-as.sh --serial=SNX00001 --serial=SNX00002
```

Every token then names those two in its `aud`, so the Controller may configure
Nodes 1 and 2 while Node 3 — discovered through the registry all the same — is
shown as inaccessible with its controls disabled up front rather than failing
403 on the first click. `--serial` is repeatable; add `--serial=SNX00003` and
the same rig becomes three configurable Nodes.

The positional arguments are `<as-host> <as-port> <rds-host> <rds-port>`, so
both Nodes are told to reach the Authorization Server at `XYZ-SNX00000:9443`
and the registry at `XYZ-SNX00000:8444`. Give the registry its **certificate
name**, not `127.0.0.1` — under `--rap=1` or `--rap=2` the Node verifies the
registry's certificate, and an IP literal matches no DNS SAN in it.

Then open <https://XYZ-SNX00001:5050/controller/> and sign in **twice**:

| Gate | Credentials |
|---|---|
| The Controller's own password form | password `admin` |
| The Authorization Server it redirects you to | `tr-10-sec-operator` / `admin` |

Your browser will warn about the certificate — the shipped PKI is a private test
CA that no browser trusts. Accept the warning once per origin: the Controller on
`XYZ-SNX00001:5050`, and the Authorization Server on `XYZ-SNX00000:9443` that
sign-in redirects to. **Do not add the test root to a system or browser trust
store** — it ships with the project, so everyone running it has the same root and
its private key is not yours to control; trusting it would cover *any* site
presented by whoever holds that key, not just this rig.

Node 2 is worth starting even if you only care about one Node. The token this
rig issues is scoped to `SNX00001`, so on the **Senders** or **Receivers**
page Node 2's device block shows the refusal *and its reason*:

```
OAuth2 token grants do not cover device serial 'SNX00002';
current aud entries: 'XYZ-SNX00001'
```

Per-device token scoping made visible, which is what the
`tutorial-security` walkthrough is built around — see [Tutorials](#tutorials).

For a single Node in the other two profiles:

```bash
# Config A — mTLS without OAuth 2.0 (no Authorization Server needed)
./start-node1-noauth2.sh

# Config B — OAuth 2.0 with server TLS
./start-node1-nomtls.sh
```

Both take the same positional arguments. With no registry arguments a Node
runs standalone — see the note under
[External dependencies of the launch scripts](#external-dependencies-of-the-launch-scripts).
`nmos_node.py --help` documents the full flag surface.

### Required before any TLS configuration: hosts-file entries

**You anti-virus may prevent you from testing NMOS-Reference with TLS** Some anti-virus software performs HTTPS scanning, in which it intercepts HTTPS communications within and outside the computer and inserts itself into the communication path and the TLS trust chain. This can prevent Nodes from communicating and authenticating themselves as expected.

Under Windows, it is best to perform TLS testing under WSL. Under Linux-based operating systems, there should normally be no problem unless similar HTTPS interception is performed by installed security tools.

**Every TLS configuration reaches its peers by DNS name, never by IP address.** The
shipped certificates carry DNS SANs of the form `XYZ-SNX000nn` (plus a `.local`
variant), and RFC 6125 hostname verification compares the name in the URL against
those SANs. An IP literal matches no DNS SAN, so `https://127.0.0.1:8443` fails
verification even though it reaches the right socket:

```
SSLCertVerificationError: IP address mismatch,
certificate is not valid for '127.0.0.1'
```

Add these to `/etc/hosts` before running anything with TLS:

```
127.0.0.1   XYZ-SNX00000
127.0.0.1   XYZ-SNX00001
127.0.0.1   XYZ-SNX00002
```

| Name | Used by |
|---|---|
| `XYZ-SNX00000` | NMOS Registry, and the OAuth 2.0 Authorization Server — the reserved infrastructure serial |
| `XYZ-SNX00001` | Node 1 and its Controller UI |
| `XYZ-SNX00002` | Node 2 |
| `XYZ-SNX00003` | Node 3 |

Certificates ship for all four serials, so nothing outside this checkout is
needed. Further Nodes follow the same pattern, but their certificates do not
ship — point `IPMX_CERT_ROOT` at a `Certificates/` tree that carries them.

This applies to how components address **each other**, not just to your browser.
Passing `127.0.0.1` as a launch script's registry-host argument fails under RAP=2
for exactly the reason above — use `./start-node1.sh XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2`,
not `127.0.0.1`.

The no-TLS launchers (`*-bare.sh`) bind `127.0.0.1` directly and need none of this.

### Connecting a browser on Windows to a rig running in WSL2

Every launcher binds **loopback only** — `127.0.0.1` inside the WSL
distribution, never the WSL interface address. WSL2 forwards Windows
`localhost` to the distribution's loopback, and that forwarding is the whole
mechanism: do **not** point anything at the address `wsl.exe hostname -I`
prints, because nothing is listening there.

For a **bare** (no-TLS) rig, nothing is needed:

```
http://localhost:5050/controller/
```

For any **TLS** rig, the browser must use the certificate's own name — both so
TLS verification passes and so the OAuth 2.0 `redirect_uri` matches one the
Authorization Server has registered. Add to
`C:\Windows\System32\drivers\etc\hosts`, editing it as Administrator, and note
these are `127.0.0.1` rather than the WSL address:

```
127.0.0.1   XYZ-SNX00000
127.0.0.1   XYZ-SNX00001
127.0.0.1   XYZ-SNX00002
```

Then browse to <https://XYZ-SNX00001:5050/controller/>. `XYZ-SNX00000` has to
resolve on Windows too, even though you never type it: under Configuration B
or C the Controller redirects your browser there to sign in.

The certificates are issued by a private test CA, so every TLS origin will warn.
**Accept the warning for each origin. Do not install the test root into the
Windows certificate store.** That root ships with the project, so every user of
it has the same one, and its private key is not under your control — trusting it
machine-wide would let anyone holding that key present a trusted certificate for
*any* site to this machine, long after you are done with the rig. A per-origin
exception costs a few clicks and goes away with the browser profile.

Under Configuration B or C there are two origins to accept: the Controller on
`XYZ-SNX00001:5050`, and the Authorization Server on `XYZ-SNX00000:9443` that
sign-in redirects to. Visiting <https://XYZ-SNX00000:9443/> once up front gets
its warning out of the way so the redirect lands without interruption.

How each browser surfaces it:

| Browser | What you see | How to continue |
|---|---|---|
| Chrome | "Your connection is not private" — `NET::ERR_CERT_AUTHORITY_INVALID` | **Advanced** → **Proceed to `<name>` (unsafe)** |
| Edge | "Your connection isn't private" — `NET::ERR_CERT_AUTHORITY_INVALID` | **Advanced** → **Continue to `<name>` (unsafe)** |
| Firefox | "Warning: Potential Security Risk Ahead" — `SEC_ERROR_UNKNOWN_ISSUER` | **Advanced…** → **Accept the Risk and Continue** |

Firefox keeps its own exception store, so accepting in Chrome or Edge does not
cover it, and vice versa.

If `localhost` forwarding is not working — occasionally it needs
`wsl --shutdown` and a restart — a rig bound to loopback cannot be reached
from Windows at all. Confirm from inside WSL first:

```bash
curl -sk -o /dev/null -w '%{http_code}\n' https://XYZ-SNX00001:5050/controller/   # expect 401
```

A 401 means the Node is serving and the problem is the WSL network layer, not
the rig.

### Running the rigs from Windows Command Prompt

Every rig above has a Windows counterpart. Start the **bare** (no-TLS) rig from
Command Prompt, one launcher per window. Both launchers prefer the repository's
`.venv\Scripts\python.exe`:

```bat
start-registry-bare.bat
start-node1-bare.bat
start-node2-bare.bat
```

These launchers mirror the shell contracts. The node launchers expect an IS-04
Registry on `127.0.0.1` (Query API port 8443, Registration API port 8444),
which `start-registry-bare.bat` provides with matching defaults. Without a
Registry the Node APIs still start, but their consoles report connection-refused
retries and the Controller cannot assemble a shared two-node resource view.

Each launcher prints the selected Registry address before starting.

The secured rigs have Windows counterparts too. `start-node1.bat` and
`start-node2.bat` take the same arguments and policy flags as their shell
equivalents, and `start-fake-as.bat` runs the Authorization Server, so
Configuration C works from Command Prompt too:

```bat
start-fake-as.bat
start-registry.bat 2
start-node1.bat XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2
start-node2.bat XYZ-SNX00000 9443 XYZ-SNX00000 8444 --rap=2
```

Anything using TLS needs the hosts-file entries described in
[Required before any TLS configuration](#required-before-any-tls-configuration-hosts-file-entries),
in `C:\Windows\System32\drivers\etc\hosts`, edited as Administrator. The
remaining shell-only launchers are the Configuration A and B node variants
(`start-node1-noauth2.sh`, `start-node1-nomtls.sh`).
The launchers default to a registry on `127.0.0.1`; `NMOS_RDS_HOST` overrides
that address and `NMOS_RDS_REG_PORT` overrides port 8444, with the Query API
port derived as one less than the Registration API port.

### Windows multicast over loopback

The bare rig puts both Nodes on `127.0.0.1`, so an activated UDP or RTP stream
sends to a multicast group over the loopback interface. Windows treats that
differently from Linux in two ways, and both matter when reading the consoles.

**Sending needs a joiner first.** Sending to a multicast group over the loopback
interface fails for as long as nothing on the host has joined that group on
`127.0.0.1` — Windows has no route to hand the datagram to. The failure surfaces
as `WSAENETUNREACH` (10051) on a blocking socket, or as
`ERROR_NETWORK_UNREACHABLE` (1231) on the overlapped path that asyncio uses:

```
send error: [WinError 1231] The network location cannot be reached.
```

The moment a Receiver joins the group on that interface the route exists, sends
succeed, and delivery works. So a Sender activated before its Receiver reports a
transmission error for the first second or two, then clears itself once the
Receiver joins. A Sender that reports 1231 *and never clears* means no Receiver
ever joined the group — check the Receiver console rather than the Sender.

There is no such dependency on a real NIC, where a route always exists. Assigning
the Nodes real interface addresses instead of `127.0.0.1` avoids the startup
error entirely.

**Receiving cannot bind the group address.** Binding a socket to the multicast
group address is a BSD/Linux idiom — the kernel accepts a class-D address and
narrows delivery to that group. Winsock requires the bind address to be a *local*
address (a unicast address on an interface, or the wildcard) and rejects a group
address with `WSAEADDRNOTAVAIL` (10049). The engine therefore binds the wildcard
on Windows and relies on `IP_ADD_MEMBERSHIP` plus the IS-05 `SourceIp` filter to
select the traffic; it still binds the group address on Linux.

---

## NMOS Registry

`nmos_registry.py` is a standalone IS-04 v1.3 registry — the Registration API
that Nodes POST their resources to, and the Query API (HTTP + WebSocket) that
Controllers read them back from. It removes the need to install a
third-party registry before trying this project.

```bash
./start-registry-bare.sh          # no TLS
./start-registry.sh 1             # server-authenticated TLS   (RAP=1)
./start-registry.sh 2             # mutual TLS                 (RAP=2)
./start-registry.sh 2 8444 --oauth2   # ... plus OAuth 2.0 on the Query API
```

Three listeners, defaulting to the ports the Node's `--rds*` flags already
expect, so a Node needs only `--rdsHost`:

| Listener | Default port | Node flag |
|---|---|---|
| Registration API | 8447 | `--rdsRegistrationPort` |
| Query API | 8446 | `--rdsQueryPort` |
| Query WebSocket | 8448 | target of the subscription `ws_href` |

The launch scripts use 8444 / 8443 / 8448 instead, matching the defaults the
node launchers already pass.

The registry reports its effective Registry Access Policy in the startup
banner, so the running compliance mode is visible rather than inferred.

### Implemented behaviour

- Registration: 201/200 with `Location`, cascade delete of child resources,
  referential-integrity and version-regression rejection, heartbeats with
  garbage collection of silent Nodes (12 s default) and their sub-resources.
- Query: pagination with `X-Paging-*` and `Link` headers, basic queries
  including dotted paths into objects and arrays, downgrade validation, and
  `501` for the optional RQL and ancestry features.
- Subscriptions: WebSocket grains for added / removed / modified / sync
  events, filtered subscriptions with the synthetic transition events IS-04
  mandates, and `max_update_rate_ms` coalescing.
- A periodic status line in nmos-cpp's exact format, so logs from the two
  implementations are directly comparable.

### Controller sign-in

The Node serves its built-in NMOS Controller under `/controller/` on `--nodeControlPort`, once you set `--controllerAdminPassword`.

The Controller is gated by a **password-only login form** at `/controller/login`, checked against `--controllerAdminPassword`. There is **no user name**, and this is **not HTTP Basic auth** — an earlier version of the app used Basic, and a cached `Authorization: Basic` header is now ignored on the way in and stripped before any request is proxied to a Node (see `nmos/controller/auth.py` for the rationale: a native browser popup supports neither logout nor error messaging).

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

---

## Agent-driven UI driver (optional)

`nmos/agentui/` drives the embedded Controller through a real Chromium, acting only
through the affordances a signed-in operator has, and writes a screenshot-and-text
journal of every step. It exists so the UI's behaviour — particularly its
per-control gating — can be demonstrated and audited rather than described.

> **If you are an AI agent** asked to demo, explain, or walk through the Controller,
> start with **[nmos/agentui/FOR-AI-AGENTS.md](nmos/agentui/FOR-AI-AGENTS.md)** —
> how to operate the driver, live or as a scenario, and how to emit a tutorial.
> Then read **[nmos/agentui/OPERATING-THE-CONTROLLER.md](nmos/agentui/OPERATING-THE-CONTROLLER.md)**
> for the domain rules a demo will otherwise trip over.

It **attaches** to a node you already started; it never launches one.
`start-node*.sh` remains the sole launch contract, so which configuration a run
exercises stays your choice. The node's address, control port, scheme, and TLS
trust material are read from its command line; the admin password comes from the
environment and is deliberately *not* harvested from process state.

Fully optional: the node runtime never imports it, playwright is confined to
`nmos/agentui/driver/`, and the default test gate never runs it.

### Setup

```bash
cd nmos-reference
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-agentui.txt

# Second step — pip cannot install a browser.
# Fetches a self-contained Chromium (~656 MB) into a repo-local directory.
PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright" \
    .venv/bin/python -m playwright install chromium
```

Everything lands in `.playwright/`: nothing enters the system package database, no
`apt` or `sudo` step is required, and removal is `rm -rf .playwright`. Both
`.playwright/` and `artifacts/` are gitignored.

### Running

Bring up the full rig — registry and **two** nodes — in three terminals. The
driver attaches to what is already running and never starts anything itself:

```bash
./start-registry-bare.sh     # terminal 1
./start-node1-bare.sh        # terminal 2 — SNX00001
./start-node2-bare.sh        # terminal 3 — SNX00002
```

```bash
# terminal 4, from nmos-reference/
export NMOS_CONTROLLER_ADMIN_PASSWORD=admin      # the --controllerAdminPassword value
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright"
.venv/bin/python -m nmos.agentui --listScenarios
.venv/bin/python -m nmos.agentui --scenario attach-and-look
```

#### What each scenario needs

The full rig runs everything, which is why it is the recommendation. A smaller
rig still runs part of the set:

| Rig | Scenarios | Why |
|---|---|---|
| One node, no registry | `attach-and-look`, `inspect-one-sender`, `selection-guard`, `blocked-controls`, `session-lost` | Read-only walks over one node's own resources. The Controller seeds its cache from the local node at startup, which is enough. |
| One node **+ registry** | adds `route-one-receiver`, `privacy-exclusivity` | These activate a route and then watch the status badges. Status travels as BCP-008 monitor resources through IS-04, so **without a registry the badges never update** and the scenario reports an unconfirmed observation. |
| **Two nodes + registry** | adds `cross-node-reverse`, `demo-group-route-tb`, `demo-group-route-hevc`, `tutorial-jpegxs` | Each routes from a sender on SNX00001 to a receiver on SNX00002, so both must be registered with the *same* registry for the Controller to see them as one resource set. |

Note that **`tutorial-jpegxs` — the scenario that emits a tutorial — is in the
last row.** With one node it cannot find its SNX00002 receiver and stops early.

A run that is short of what it needs does not fail silently: the driver reports
the missing precondition (`"Is the node registered with a registry and serving
senders?"`, `"reverse-direction buttons will not appear. Start a second node."`)
and `manifest.json` records whether a live status update was genuinely observed
or merely unconfirmed.

On Windows, start the equivalent rig from Command Prompt — see
[Running the rigs from Windows Command Prompt](#running-the-rigs-from-windows-command-prompt).

Options mirror `nmos_node.py`'s camelCase style: `--scenario`, `--controlPort`
(disambiguate when several nodes serve a UI), `--artifactsRoot`, `--headed`,
`--stepTimeoutMs`, `--pinChain`.

Artifacts land in `artifacts/agentui/<run_id>/` — read `journal.md`. Alongside it,
`manifest.json` records the run's own honesty checks: whether any navigation went
unaccounted for, whether a second browser page appeared, whether the driver itself
issued HTTP, whether certificate verification was ever bypassed, and whether a
live status update was genuinely observed or merely unconfirmed.

Scenarios marked *makes changes* issue real IS-05/IS-11 calls and, by design,
perform **no teardown** — they leave the rig in the state they reached so you can
inspect it. Note that an unreleased exclusive-access reservation stays held until
the session expires or someone signs out.

For a TLS node the browser is made to trust the certificate by pinning its SPKI
hash, after verifying the chain with `nmos/cert_check.py`; where the certificate's
own DNS name resolves, the cleaner name-based path is used and no browser flag is
passed at all. There is deliberately no option to disable verification, because a
run with checks switched off looks identical to one where they work.

---

## One Model — independent + multiplexed streams unified

This implementation follows the Matrox **"One Model to Rule them All"** design, formalised in the [NMOS-MatroxOnly](https://github.com/alabou/NMOS-MatroxOnly) corpus. The model presents two stream topologies — that NMOS controllers typically handle through separate code paths — under a single configuration surface:

- **Group of independent streams** — multiple Senders, one per essence (e.g., one audio Sender + one video Sender + one data Sender).
- **Multiplexed stream** — one Sender carrying multiple sub-streams (e.g., a single MPEG2-TS mux containing video + audio + data).

From the user's point of view the configuration model is the same: both shapes expose `video 0`, `audio 0`, `audio 1`, … and are configured through IS-11 active constraints in the same way. The Controller abstracts the underlying transport / streaming implementation. The constraint-set metadata on each Sender / Receiver — `format`, `layer`, and `layer_compatibility_groups` (alongside the natural-grouping role / role-index from BCP-002-01) — are what let one code path drive both topologies.

In practice this means the same IS-11 negotiation handles MPEG2-TS (over RTP, UDP or SRT), RTSP, NDI, AES3/AM824 (over RTP) audio mux containers, **and** independent RTP senders for the same set of essences. The Controller asks "what's the user's intent for `video 0` and `audio 0`?" once; the per-Sender/Receiver hints decide whether that becomes a mux-sub-stream configuration or a coordinated independent-Sender configuration.

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

On Windows, multicast flows between Nodes on `127.0.0.1` behave differently from
Linux — see
[Windows multicast over loopback](#windows-multicast-over-loopback).

Together with the embedded capabilities-driven NMOS Controller, the streaming engine supports a multi-Node test fabric across any number of machines: IS-05 activations drive real connections, the Controller runs IS-11 stream-compatibility negotiation against the connected peers, and Senders / Receivers are reconfigured against what the device declares in BCP-004-01/-02 — codec profile / level, sampling, bit-depth, packet-time, transport, channel layout. The TLS, OAuth 2.0, PEP, registry registration, and capability negotiation paths are exercised end-to-end.

---

## Specification coverage

### AMWA NMOS Interface Specifications ([specs.amwa.tv](https://specs.amwa.tv/))

| Spec | Role |
|---|---|
| **IS-04** Discovery & Registration | Node API, Registry client |
| **IS-05** Connection Management | Senders, Receivers, staged/active model |
| **IS-10** Authorization | OAuth 2.0 Bearer tokens, JWKS, claims |
| **IS-11** Stream Compatibility | Sender / Receiver capability + constraint support via the Matrox CCF framework (`caps/`) — supported + active constraint sets and parameter constraints; dynamic reconfiguration driven by active constraint sets; per-sub-flow / per-sub-stream configuration for hierarchical mux transports. IS-11 `Input` / `Output` resources are not implemented. |

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

This repository also *generates* tutorials from real runs against a live rig,
each step carrying a screenshot, the values actually observed, the API calls
the run issued, and links to both the specification and the implementing
source. Two ship today:

| Tutorial | Teaches | Rig |
|---|---|---|
| `tutorial-jpegxs` | Activating a video sender and subscribing a receiver over JPEG XS | Two nodes + registry (bare is fine) |
| `tutorial-security` | How TLS and OAuth 2.0 decide what a Controller may do — the two-stage sign-in, certificate identity, and per-device token scoping | Configuration C, two nodes |

```bash
export PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright"
export NMOS_CONTROLLER_ADMIN_PASSWORD=admin
.venv/bin/python -m nmos.agentui --scenario tutorial-security --tutorial
```

`tutorial-security` is read-only and changes nothing on the devices. See
[nmos/agentui/FOR-AI-AGENTS.md](nmos/agentui/FOR-AI-AGENTS.md) for the driver
itself.

---

## Project layout

```
nmos/                   — Core NMOS implementation
  api/                  — HTTP/WS endpoints, TLS context factories, IS-04/05/...
  controller/           — Built-in NMOS Controller + outbound OAuth2/registry clients
  node/                 — Node resources (senders/receivers/sources/flows/devices), config
  registry/             — Standalone IS-04 Registration + Query APIs (server side)
    specs/              — IS-04 v1.3.3 RAML, JSON schemas and behaviour docs (verbatim)
  agentui/              — Agent driver for the Controller UI (real Chromium, journalled runs)
    core/               — Surface/step/journal primitives, process scan, TLS pinning
    apps/nmos_controller/ — Controller-specific driver: discovery, session, pages, trace join
    driver/             — Playwright launcher and Surface implementation
    FOR-AI-AGENTS.md, OPERATING-THE-CONTROLLER.md — read these before driving the UI
  json/                 — Typed JSON serialization engine
  types/generated/      — Auto-generated typed wrappers for all NMOS resource types
  codegen/              — Go-source parser + generator that produces types/generated/
  oauth2/               — Bearer token validation, JWKS cache lifecycle
  crypto/               — ExclusiveSession: token-based mutual exclusion for Node Reservation
  tasks/                — DispatchGroup wrapping asyncio.TaskGroup
  codec/                — Audio/video codec descriptors (H.264, H.265, AAC, JXSV, AES3, …)
  enums/, ip/, errors/, uuid/  — Domain primitives

sdp/                    — SDP encoding/decoding (Matrox profile)
caps/                   — Capability/constraint framework (Matrox CCF)
pep/                    — Privacy Encryption Protocol (PEP) helpers
Certificates/build.0/   — Test PKI subset (SNX00000 infrastructure, SNX00001..3
                          Nodes, both RSA and ECDSA, plus ExampleRootCA-bundle.pem
                          holding both roots) so TLS and the TLS test suites run
                          from a clone with nothing outside it
fake-as/                — Test OAuth 2.0 Authorization Server, vendored (see below)

nmos_node.py            — Node entry point; parses CLI and starts the server
nmos_registry.py        — Registry entry point; Registration + Query + WebSocket listeners
multi_aud_as.py         — Runs fake-as/ with a multi-Node token audience, for rigs
                          where the Controller may configure some devices but not
                          others (start-fake-as.sh --serial=A --serial=B)
run_server.py           — Lightweight wrapper for embedding nmos_node from scripts
demo_controller.py      — Standalone demo controller for manual exploration
start-node*.sh          — Launch scripts for the three security configurations
start-registry*.sh      — Registry launchers (bare = no TLS; the other takes a RAP value)
start-fake-as.sh        — Test OAuth 2.0 Authorization Server (vendored; see below)
start-node*-bare.bat    — Windows launchers for the bare (registry-only) rigs
start-registry*.bat     — Windows registry launchers
requirements.txt        — Runtime dependencies
requirements-agentui.txt — Extra dependencies for the agent driver (Playwright)
```
### The vendored Authorization Server (`fake-as/`)

The TLS + OAuth 2.0 rig needs an Authorization Server. Rather than require
Keycloak and Docker, this repository ships one: `fake-as/` holds
`ipmx_fake_as.py` and `ipmx_security_tokens.py`, started by
`./start-fake-as.sh`.

---

## Tests

```bash
# Full test suite (~4 minutes; 2 900+ tests)
# Paths come from `testpaths` in pyproject.toml — nmos/ plus caps/tests
python3 -m pytest -q

# Per-module
python3 -m pytest -q nmos/oauth2/tests/
python3 -m pytest -q nmos/registry/tests/
python3 -m pytest -q nmos/agentui/tests/
python3 -m pytest -q caps/tests/
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

**The streaming engine emulates transport, so some TR-10 stream requirements may deliberately not be met.** The engine exists to exercise connection management, capability negotiation, encryption, status monitoring and registry orchestration end-to-end — not to be a reference for the media transport itself:

- **IGMP source-specific joins.** TR-10 requires IGMPv3 with "the source-specific method". Receivers here join with any-source `IP_ADD_MEMBERSHIP` and filter on the IS-05 `SourceIp` in the receive loop instead. For an emulated transport on a single host that is sufficient; a real product should use `IP_ADD_SOURCE_MEMBERSHIP` so the kernel enforces the source filter.

- **RTCP Sender Reports** TR-10 requires a Sender to transmit RTCP Sender Reports. The streaming emulation does not.

Those deviations do not affect the control-plane behaviour this project is a reference for.

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
