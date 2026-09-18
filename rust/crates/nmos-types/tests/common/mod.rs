// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Shared machinery for the corpora that drive bodies through the generated
//! types.
//!
//! Three integration tests decode the same six resource types from recorded
//! Python verdicts, and the part they share -- dispatching on a resource-type
//! string, and separating the registry's wrapper text from the type layer's own
//! message -- is exactly the part that must not drift between them. A body that
//! `decode_parity` and `structural_parity` disagreed about how to decode would
//! be comparing two different things against one corpus.

#![allow(dead_code)]

use nmos_types::generated::{
    ndevice::NDevice, nflow::NFlow, nnode::NNode, nreceiver::NReceiver, nsender::NSender,
    nsource::NSource,
};
use serde_json::Value;

/// Decode a body as the named resource type.
///
/// The error is the type layer's own message text, which is what the corpora
/// compare -- see [`inner_message`] for why the recorded text is wider.
///
/// # Panics
///
/// On a resource type the corpus names but this does not handle, which means
/// the corpus grew a type and this was not updated. Failing loudly is the point:
/// silently skipping it would drop a whole resource type from the comparison.
pub fn decode(resource_type: &str, body: &Value) -> Result<(), String> {
    let result = match resource_type {
        "node" => NNode::decode(body).map(|_| ()),
        "device" => NDevice::decode(body).map(|_| ()),
        "source" => NSource::decode(body).map(|_| ()),
        "flow" => NFlow::decode(body).map(|_| ()),
        "sender" => NSender::decode(body).map(|_| ()),
        "receiver" => NReceiver::decode(body).map(|_| ()),
        other => panic!("unknown resource type in corpus: {other}"),
    };
    result.map_err(|e| e.message().to_owned())
}

/// Strip `decode.py`'s wrapper, leaving the type layer's own message.
///
/// The corpus records what `nmos/registry/decode.py` puts in the HTTP 400 body,
/// which wraps the type layer's text: `node failed validation: missing required
/// member Id`. That wrapper is registry code, which has not been ported yet, so
/// the comparison is against the inner part.
///
/// Returning `None` means the message never had the wrapper -- it came from
/// `decode.py` before the type layer was reached, like `expected a JSON object
/// for node`. Callers count those rather than ignoring them.
pub fn inner_message(resource_type: &str, message: &str) -> Option<String> {
    let prefix = format!("{resource_type} failed validation: ");
    message.strip_prefix(&prefix).map(str::to_owned)
}

/// One recorded case: what Python decided about one body.
#[derive(Debug, serde::Deserialize)]
pub struct Case {
    pub resource_type: String,
    pub label: String,
    pub ok: bool,
    #[serde(default)]
    pub message: Option<String>,
    pub body: Value,
}

/// How one case came out when Rust ran it.
pub enum Outcome {
    /// Both reached the same verdict, and the same message if they rejected.
    Agreed,
    /// Python rejected above the type layer, so there is nothing to compare.
    RegistryLevel,
    /// They disagree; the string says how.
    Disagreed(String),
}

/// Compare one case, returning what happened rather than asserting.
///
/// Callers aggregate, so that a broken decode path reports every case it
/// affects instead of stopping at the first.
pub fn compare(case: &Case) -> Outcome {
    let actual = decode(&case.resource_type, &case.body);
    match (case.ok, &actual) {
        (true, Ok(())) => Outcome::Agreed,
        (false, Err(got)) => {
            let Some(want) = case
                .message
                .as_deref()
                .and_then(|m| inner_message(&case.resource_type, m))
            else {
                return Outcome::RegistryLevel;
            };
            if got == &want {
                Outcome::Agreed
            } else {
                Outcome::Disagreed(format!(
                    "{}/{}: rust {got:?} != python {want:?}",
                    case.resource_type, case.label,
                ))
            }
        }
        (true, Err(got)) => Outcome::Disagreed(format!(
            "{}/{}: rust rejected ({got}) but python accepted",
            case.resource_type, case.label,
        )),
        (false, Ok(())) => {
            if case
                .message
                .as_deref()
                .and_then(|m| inner_message(&case.resource_type, m))
                .is_none()
            {
                return Outcome::RegistryLevel;
            }
            Outcome::Disagreed(format!(
                "{}/{}: rust accepted but python rejected ({})",
                case.resource_type,
                case.label,
                case.message.as_deref().unwrap_or_default(),
            ))
        }
    }
}
