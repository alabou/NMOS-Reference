# Vendored etcd protocol definitions

These three files are copied **byte-for-byte** from the etcd source tree. They are
not edited here, so `sha256sum` below is enough to prove what they are, and a
`diff` against the upstream tag shows nothing. Everything this project needs to
change about them happens at generation time in `nmos/etcd/generate.py`, which
reads these originals and writes a stripped copy to a temporary directory before
invoking `protoc`.

Keeping the originals pristine is the point: a vendored proto that has been
hand-edited is indistinguishable from one that has drifted, and the whole wire
contract with the database depends on it being exactly what etcd ships.

## Source

Tag: **v3.6.14** — the version this project pins (see `install-etcd.sh`).

| File | Upstream path | SHA-256 |
|---|---|---|
| `rpc.proto` | `api/etcdserverpb/rpc.proto` | `c57117e8f4764667be927d862f4fb01fbf1faaf0f725bf2eecc3a523e12aacc6` |
| `kv.proto` | `api/mvccpb/kv.proto` | `7416acaac7c76524c717df9107a8020d3c4eda92a490a35f1f211592ce8cfa1d` |
| `auth.proto` | `api/authpb/auth.proto` | `e2b6936f3aee307f0f183c99613983e7d5d66e5daaf6da3cd2f4b6976db0a0a1` |

Re-vendoring, when the pinned etcd version moves:

```sh
TAG=v3.6.14
for f in etcdserverpb/rpc mvccpb/kv authpb/auth; do
    curl -sSfL "https://raw.githubusercontent.com/etcd-io/etcd/${TAG}/api/${f}.proto" \
         -o "nmos/etcd/proto/$(basename ${f}).proto"
done
sha256sum nmos/etcd/proto/*.proto     # update the table above
python -m nmos.etcd.generate          # regenerate, then run the etcd test suite
```

## Why they are stripped rather than compiled as-is

`rpc.proto` as published depends on four proto files this project has no use for
and does not vendor:

| Dropped import | What it carries | Why it is not needed |
|---|---|---|
| `gogoproto/gogo.proto` | gogo code-generation hints | Go-specific; meaningless to the Python generator. |
| `google/api/annotations.proto` | 41 `google.api.http` method bindings | These describe the **gRPC-JSON gateway** REST mapping. This client speaks native gRPC, so the mapping is dead weight. |
| `protoc-gen-openapiv2/options/annotations.proto` | Swagger/OpenAPI metadata | Documentation generation only. |
| `etcd/api/versionpb/version.proto` | `etcd_version_msg` / `_field` / `_enum_value` markers | Records the etcd release each field appeared in. Informational; the version gate is enforced at runtime by a `Maintenance.Status` RPC instead. |

Stripping them removes **no message, field, enum value or service method** — only
annotations *about* those declarations. `generate.py` fails loudly rather than
silently dropping anything it does not recognise, so a future etcd release that
adds a genuinely load-bearing option will stop the build instead of quietly
producing a client with a missing field.

The two remaining imports are rewritten from etcd's build-root-relative form
(`etcd/api/mvccpb/kv.proto`) to the flat form (`kv.proto`), because all three
files are compiled from one directory.
