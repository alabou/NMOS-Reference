//! Generated NMOS type: `NSource`. DO NOT EDIT.
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

use crate::generated::nsource_audio::NSourceAudio;
use crate::generated::nsource_data::NSourceData;
use crate::generated::nsource_mux::NSourceMux;
use crate::generated::nsource_video::NSourceVideo;

/// `NSource`: a discriminated union over 4 concrete types.
///
/// # Dispatch is ordered and committal
///
/// The variants are tried in the order the model declares them, and the FIRST
/// whose predicate matches decides the type -- its decode error is the answer,
/// even when a later variant would have decoded cleanly. Python behaves the
/// same way, and that is why this is not `#[serde(untagged)]` on the way in:
/// untagged picks the first variant that *deserialises*, not the first whose
/// *discriminator* matches, and it backtracks. A body whose `format` says
/// video but whose components are empty must report the empty components, not
/// fall through and be mistaken for something else.
///
/// Declaration order is load-bearing for a second reason: some predicates only
/// exclude (`notin`), so a variant declared later may match a superset of an
/// earlier one. The emitter never sorts these.
///
/// `Serialize` IS untagged, which is correct on the way out -- it writes the
/// inner value with no added tag, exactly as Python's type-switch encode does.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
// Variants differ in size because the concrete NMOS types do. Boxing them to
// even that out would change the public shape of every match arm, for a type
// that is decoded once per registration and then dropped.
#[allow(clippy::large_enum_variant)]
pub enum NSource {
    /// `NSourceVideo`.
    Video(NSourceVideo),
    /// `NSourceAudio`.
    Audio(NSourceAudio),
    /// `NSourceData`.
    Data(NSourceData),
    /// `NSourceMux`.
    Mux(NSourceMux),
}

/// Does this object discriminate as `NSourceVideo`?
fn is_nsource_video(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:video") {
        return false;
    }
    true
}

/// Does this object discriminate as `NSourceAudio`?
fn is_nsource_audio(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:audio") {
        return false;
    }
    true
}

/// Does this object discriminate as `NSourceData`?
fn is_nsource_data(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:data") {
        return false;
    }
    true
}

/// Does this object discriminate as `NSourceMux`?
fn is_nsource_mux(doc: &serde_json::Map<String, Value>) -> bool {
    if doc.get("format").and_then(Value::as_str) != Some("urn:x-nmos:format:mux") {
        return false;
    }
    true
}

impl NSource {
    /// Decode by discriminating on the object's contents.
    pub fn decode(src: &Value) -> Result<Self> {
        let Some(doc) = src.as_object() else {
            return Err(Error::invalid_data(
                "expected JSON object for polymorphic NSource",
            ));
        };

        if is_nsource_video(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Video(NSourceVideo::decode(src)?));
        }
        if is_nsource_audio(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Audio(NSourceAudio::decode(src)?));
        }
        if is_nsource_data(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Data(NSourceData::decode(src)?));
        }
        if is_nsource_mux(doc) {
            // Committal: this variant's error is the answer.
            return Ok(Self::Mux(NSourceMux::decode(src)?));
        }
        Err(Error::invalid_data(
            "no matching type for polymorphic NSource",
        ))
    }
}
