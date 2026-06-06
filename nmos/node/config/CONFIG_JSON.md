# Node `config.json` authoring guide

This document describes the JSON config shape the Python reference
Node consumes at startup (`--nodeConfig path/to/config.json`) and
the rules its pipeline builder applies.

The config is the source-of-truth for what the Node advertises:
every sender, every receiver, every BCP-004-01 `constraint_set` you
declare here becomes part of the IS-04 resource the Node publishes
to the registry. The Node's own activation engine, the IS-11
stream-compatibility logic, the controller's capability picker, and
the MatroxCCF intersection all read these same fields — so getting
the meta keys right matters.

## File shape

```json
{
  "senders":   [ <sender-config>, … ],
  "receivers": [ <receiver-config>, … ]
}
```

Each `sender-config` / `receiver-config` carries:

| Key | Purpose |
|---|---|
| `label`              | IS-04 `label` (human-readable) |
| `description`        | IS-04 `description` |
| `format`             | `urn:x-nmos:format:video` / `…:audio` / `…:data` / `…:mux` |
| `transport`          | `urn:x-nmos:transport:rtp[.mcast|.ucast]`, `urn:x-matrox:transport:srt.mp2t`, `urn:x-matrox:transport:rtsp`, `urn:x-matrox:transport:ndi`, `urn:x-matrox:transport:usb`, `urn:x-nmos:transport:udp` |
| `group_hint`         | Natural-group tag. Format: `"TRANSPORT N:ROLE M"` — e.g. `"RTP 0:VIDEO 0"`, `"RTP 1:MUX 0"`. See [`nmos/controller/grouping.py`](../../controller/grouping.py) |
| `caps.constraint_sets` | Array of BCP-004-01 constraint sets. See below. |
| `linked_receiver_group` | (senders only) `"RTP 2"`-style reference to a previously-built receiver. Links the sender's Sources to the receiver's Source ids so streams can be forwarded. |

## Constraint-set structure

A `constraint_set` is one **partition** of the sender's / receiver's
operating envelope — an `(format, layer, compatibility_groups)`
tuple plus a list of parameter constraints. Constraint sets are
independent; each describes one valid operating point (or range).

### Meta keys — the IMPORTANT part

There are two URN namespaces. **Mixing them up is the single most
common source of silent bugs** — most of them produce "it looks
right but the intersection is empty" or "the filter dropdown is
missing formats" symptoms.

| Meta key | URN | Defined by |
|---|---|---|
| `label`                         | `urn:x-nmos:cap:meta:label`                         | BCP-004-01 |
| `preference`                    | `urn:x-nmos:cap:meta:preference`                    | BCP-004-01 |
| `enabled`                       | `urn:x-nmos:cap:meta:enabled`                       | BCP-004-01 |
| `format`                        | **`urn:x-matrox:cap:meta:format`**                  | Matrox extension |
| `layer`                         | **`urn:x-matrox:cap:meta:layer`**                   | Matrox extension |
| `layer_compatibility_groups`    | **`urn:x-matrox:cap:meta:layer_compatibility_groups`** | Matrox extension |
| `layer_enabled`                 | `urn:x-matrox:cap:meta:layer_enabled`               | Matrox extension |

> **Rule of thumb:** only `label`, `preference`, `enabled` use the
> `urn:x-nmos:` prefix. Everything that identifies which
> *partition* a set belongs to (format, layer, compatibility
> groups) uses the `urn:x-matrox:` prefix. That's the convention
> all of the following agree on:
>
> * [caps/MatroxCCF.py:88-95](../../../caps/MatroxCCF.py#L88-L95) — the URN registry
> * The controller's capability renderer (handlers.py)
> * The pipeline builder (this directory, `pipelines.py`)
>
> If you use `urn:x-nmos:cap:meta:format` (wrong URN), the config
> will *load* without errors — but MatroxCCF will see your CS as
> "trunk" (no partition), the controller's filter dropdowns will
> collapse to a single fallback entry, and IS-11 intersection will
> not match the way you expect.

### Preference, enabled, and layer_enabled

* `urn:x-nmos:cap:meta:preference` — integer 0–100. Higher wins
  when multiple sets are candidates for the same partition. The
  "native" operating point conventionally uses 100; descriptive
  envelope sets use lower values.
* `urn:x-nmos:cap:meta:enabled` — gates the CS at **top-level**
  intersection. Default `true`. Set to `false` to keep the CS
  declared but out of the top-level negotiation.
* `urn:x-matrox:cap:meta:layer_enabled` — gates the CS at
  **per-layer** intersection. Default `false`. These two keys are
  independent gates — a MUX sub-layer CS is conventionally
  published with `enabled=false` + `layer_enabled=true` so it
  does NOT contaminate the top-level mux envelope but IS active
  when the receiver narrows a specific layer.

> **Rule for readers:** a CS is "visible" / participates in a UI
> or matching decision if `enabled != false` OR `layer_enabled ==
> true`. Skipping on `enabled=false` alone drops every MUX
> sub-layer and is a common source of "the filter dropdown shows
> only mux/0" bugs.

### Layer compatibility groups

`urn:x-matrox:cap:meta:layer_compatibility_groups` is an array of
integer group ids. Receivers use it to tie a layer's acceptance to
a specific combination of other layers (typical use: "my AUDIO
layer 0 only accepts AAC when VIDEO layer 0 is H.264, and only PCM
when VIDEO layer 0 is H.265"). See
[specs/Capabilities.md](../../../specs/Capabilities.md) for the
semantics.

### Parameter constraints

BCP-004-01 parameter constraints by URN, e.g.:

```json
{
  "urn:x-nmos:cap:format:media_type":    {"enum": ["video/H.264"]},
  "urn:x-nmos:cap:format:frame_width":   {"enum": [1920]},
  "urn:x-nmos:cap:format:frame_height":  {"enum": [1080]},
  "urn:x-nmos:cap:format:grain_rate":    {"enum": [{"numerator": 30}]}
}
```

For the full list of parameter URNs and their value shapes see
BCP-004-01 and
[specs/Capabilities.md](../../../specs/Capabilities.md).

## Native vs alternative vs envelope sets

A well-formed `constraint_sets` array generally contains three
tiers:

1. **Native (preference=100, enabled=true)** — single-value constraints
   for the operating point the Node uses by default. Every parameter
   is pinned. Templates for the common codec natives are in
   [`templates.py`](templates.py) (`get_native_raw_template`,
   `get_native_aac_template`, etc.).
2. **Alternatives (preference=100, enabled=true)** — one or more
   CSes describing equivalent single-value operating points the Node
   can switch to (different codecs, different resolutions). Each is
   a full "pinned" set like the native, but for a different choice.
3. **Envelope (preference<100)** — multi-value ranges declaring the
   BROAD capability. The MatroxCCF "pyramid rule" requires every
   parameter mentioned by any non-native set at a given
   `(format, layer)` partition to also be declared by the native
   set at that partition. Get this wrong and
   `are_native_in_pyramid` fails — the controller will narrow the
   caps down to the native's single values and the receiver will
   refuse anything else. See
   [`feedback_pyramid_coverage.md`](../../../../.claude/projects/-home-alain-Projects-IPMX/memory/feedback_pyramid_coverage.md).

## MUX senders — one CS per sub-layer

For a MUX sender (MPEG2-TS, NDI with embedded audio, …) the
pipeline generates one *sub-Flow* per `(format, layer)` partition
that appears in the config. So:

* **One trunk CS** with no meta:format / meta:layer — the
  transport-level envelope (media_type for the mux container,
  bitrate, etc.)
* **One CS per sub-layer** — each with
  `urn:x-matrox:cap:meta:format` and `urn:x-matrox:cap:meta:layer`
  set. These drive the pipeline's sub-Flow count.

If you want a MUX to advertise "up to 3 audio streams", you need
*three* audio CSes — one for each of `layer: 0`, `layer: 1`,
`layer: 2`. The pipeline will create three audio sub-Flows; the
IS-11 intersection will accept a receiver that asks for any 1..3
audio layers. Declaring only layer 0 means the MUX carries exactly
one audio stream.

The Python reference is config-driven: the pipeline counts unique layers per format in
`config.constraint_sets`.

### Example — MUX with video+audio, single layer each

```json
{
  "senders": [{
    "label": "MPEG2-TS mux sender",
    "format": "urn:x-nmos:format:mux",
    "transport": "urn:x-matrox:transport:srt.mp2t",
    "group_hint": "SRT 0:MUX 0",
    "caps": {
      "constraint_sets": [
        { "urn:x-nmos:cap:meta:label": "trunk",
          "urn:x-nmos:cap:meta:preference": 100,
          "urn:x-nmos:cap:meta:enabled": true,
          "urn:x-nmos:cap:format:media_type": {"enum": ["video/MP2T"]}
        },
        { "urn:x-nmos:cap:meta:label": "video-native",
          "urn:x-nmos:cap:meta:preference": 100,
          "urn:x-nmos:cap:meta:enabled": true,
          "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:video",
          "urn:x-matrox:cap:meta:layer": 0,
          "urn:x-nmos:cap:format:media_type": {"enum": ["video/H.264"]}
        },
        { "urn:x-nmos:cap:meta:label": "audio-native",
          "urn:x-nmos:cap:meta:preference": 100,
          "urn:x-nmos:cap:meta:enabled": true,
          "urn:x-matrox:cap:meta:format": "urn:x-nmos:format:audio",
          "urn:x-matrox:cap:meta:layer": 0,
          "urn:x-nmos:cap:format:media_type": {"enum": ["audio/mpeg4-generic"]}
        }
      ]
    }
  }]
}
```

Sample configs in [`builtin/`](builtin/) cover the common shapes:

* `config1.json` — plain RTP video + audio senders and receivers
* `config4a_mux.json` — MPEG2-TS mux with one audio sub-stream
* `config4a_max.json` — MPEG2-TS mux with 3 audio sub-streams
* `config7.json` / `config7u.json` — NDI (uncompressed video +
  PCM audio) mux variants
* `config10.json`, `config11.json`, `config12.json` — IS-11
  intersection test fixtures

## Privacy (PEP)

When the Node is started with `--privacy true`, the pipeline
auto-adds `ext_privacy_*` capabilities to each constraint set
(see [`pipelines.py`](pipelines.py)
`_add_privacy_to_constraint_sets`). You don't need to declare
them yourself unless you want to restrict the set of acceptable
Protocols / Modes / Curves.

## Node Reservation

The Node publishes the exclusive-session service
(`urn:x-matrox:service:exclusive/v1.0`) on its `services` array
automatically; no config.json field is required. Reservation
state lives at runtime, not in the config.

## Related docs

* [`specs/Capabilities.md`](../../../specs/Capabilities.md) —
  general BCP-004-01 shape and semantics
* [`specs/SenderCapabilities.md`](../../../specs/SenderCapabilities.md)
  — sender-side conventions
* [`specs/ReceiverCapabilities.md`](../../../specs/ReceiverCapabilities.md)
  — receiver-side conventions
* [`specs/NMOS With Node Reservation.md`](../../../specs/NMOS%20With%20Node%20Reservation.md)
  — Node Reservation spec
* [`caps/MatroxCCF.py`](../../../caps/MatroxCCF.py) — CCF Caps
  model and URN registry
* [`templates.py`](templates.py) — codec constraint templates for
  native operating points
* [`pipelines.py`](pipelines.py) — pipeline builder that turns a
  config into live Source/Flow/Sender resources
