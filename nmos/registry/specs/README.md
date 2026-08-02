# IS-04 reference material for `nmos/registry/`

Everything in this folder is **normative reference material, copied verbatim from
upstream. Do not edit it.** It is the ground truth the registry implementation is written
against; when the code and these documents disagree, the documents win.

## Provenance

| | |
|---|---|
| Repository | <https://github.com/AMWA-TV/is-04> |
| Branch | `v1.3.x` |
| Tag | `v1.3.3` |
| Commit | `8e6876d9067cc56f9eca5345d44e41d9e1754444` (2024-12-11) |
| Local mirror | `IPMX-testing/cache/is-04/` |

The four files that were already here before the mirror was expanded
(`QueryAPI.raml`, `RegistrationAPI.raml`, `Behaviour - Querying.md`,
`Behaviour - Registration.md`) were verified byte-identical to that tag, as were the nine
loose `*.json` schemas at the root of this folder.

## Layout

Upstream splits the material across two directories. This folder flattens the RAML level:

| Here | Upstream |
|---|---|
| `QueryAPI.raml`, `RegistrationAPI.raml` | `APIs/` |
| `schemas/` (47 files) | `APIs/schemas/` |
| `examples/` (35 files) | `examples/` (repo root) |
| `Behaviour - *.md` | `docs/` |

Consequences of the flattening:

- `!include schemas/…` inside the RAMLs **resolves** here.
- Every `"$ref"` between the JSON schemas in `schemas/` **resolves** here.
- `!include ../examples/…` inside the RAMLs **does not resolve** here — upstream that path
  is relative to `APIs/`, i.e. the repo root's `examples/`. The files themselves are all
  present in `examples/`; only the relative path in the RAML is off by one level. Nothing
  in this project parses RAML, so this is a documentation nit, not a build break.

The nine `*.json` schemas at the root of this folder are byte-identical duplicates of the
same files inside `schemas/`. They predate the mirror and are kept so existing references
to them do not break; `schemas/` is the authoritative copy.

## Additional normative documents used by the implementation

These live elsewhere and are *not* duplicated here:

- `IPMX-testing/cache/is-04/docs/APIs - Query Parameters.md` — paging, basic queries,
  downgrade, RQL and ancestry semantics.
- `IPMX-testing/cache/is-04/docs/APIs.md` — trailing slashes, error-body shape, version
  comparison.
- `nmos-reference/specs/NMOS With Control Plane Security.md` (IPMX TR-10-SEC) — §"NMOS
  Registry" and the Registry Access Policy (RAP), which is why the Registration API is
  TLS/mTLS-only and never OAuth 2.0.
