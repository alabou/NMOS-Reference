//! Model and emitter digests for this generated tree. DO NOT EDIT.

/// SHA-256 over `nmos/codegen/definitions/` and `descriptors.py`.
///
/// The Python tree carries this same value. `nmos/codegen/tests/
/// test_fingerprint.py` asserts they are equal, which is what makes "both
/// implementations describe one model" a checkable claim rather than a hope.
// Wrapped because a 64-character digest plus the declaration exceeds rustfmt's
// line width, and `cargo fmt --check` is part of the gate. Emitting it already
// wrapped keeps a regeneration from dirtying the tree.
pub const MODEL_FINGERPRINT: &str =
    "59169ab792ecca5836c9a9830b5f81f77b0104fa4fdce347b22522b79c94b45a";

/// SHA-256 over the generator and its templates.
///
/// Per-tree: the two languages are rendered by two templates, so these differ
/// between the trees by design.
pub const EMITTER_FINGERPRINT: &str =
    "7ccd0f3a4ac64dc8b4c084c2efd2de088dc6e92b2a8906a77f46b5f896ac4b58";
