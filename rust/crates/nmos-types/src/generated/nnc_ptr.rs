//! Generated NMOS type: `NNcPtr`. DO NOT EDIT.
//!
//! Rendered from `nmos/codegen/definitions/` by
//! `nmos/codegen/templates/type.rs.jinja2`. The descriptors are the source of
//! truth and the Python tree in `nmos/types/generated/` is rendered from the
//! same ones, so the two describe one model by construction.
//!
//! Regenerate with: `python -m nmos.codegen.generate`

#[allow(unused_imports)]
use nmos_json::error::{Error, Result};
#[allow(unused_imports)]
use nmos_json::{EnumId, Hyperlink, Nullable, RawJson, Tags, Tai, decode, validators};
use serde::Serialize;
#[allow(unused_imports)]
use serde_json::Value;

/// `NNcPtr`: a single value carried without a wrapping object.
///
/// `#[serde(transparent)]` so it encodes as its contents, with no extra nesting,
/// matching the Python type's encode.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(transparent)]
pub struct NNcPtr(
    /// The wrapped value, encoded with no enclosing object.
    pub Value,
);

impl NNcPtr {
    /// Decode from a JSON value.
    pub fn decode(src: &Value) -> Result<Self> {
        // The Python builtin `object`: any JSON value, taken as-is.
        Ok(Self(decode::generic(src)))
    }
}
