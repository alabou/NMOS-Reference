"""Generated etcd protobuf message classes. DO NOT EDIT.

Produced by ``python -m nmos.etcd.generate`` from the vendored protos in
``nmos/etcd/proto/``.

Committed to git, like ``nmos/types/generated/``. That is what lets someone try
the distributed registry with only ``pip install -r requirements-etcd.txt`` and
``./install-etcd.sh`` -- no codegen step, and no need to understand the build to
run the thing.

The cost of committing generated code is that it can go stale against its
source. PROTO_FINGERPRINT is the guard: it is the digest of the vendored
``.proto`` files this package was built from, and ``check_generated_current()``
compares it against those files at startup, so a proto change that has not been
regenerated is a clear message rather than a subtly wrong wire contract.
"""

PROTO_FINGERPRINT = "35c9ee6f6fe01a01a0e93a4a73e02e02b841e2444d0d9af9b23af74bf5b9cac1"
"""SHA-256 over the vendored .proto sources these stubs were generated from."""
