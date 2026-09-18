// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Bearer-token validation for the Query API.
//!
//! Port of the server half of `nmos/oauth2/__init__.py`: verify a JWT's
//! signature against a JWKS, then decide what its claims permit. The client
//! half -- metadata discovery, JWKS fetching, the token-acquisition grants --
//! belongs to a Node rather than a registry and is not ported here.
//!
//! # Where this may and may not be applied
//!
//! On the **Query API only**. TR-10-SEC:105 forbids the Registration API from
//! requiring OAuth 2.0, and `handlers_registration.py:40` records that "none of
//! these routes is wrapped in `check_oauth2`. That is required, not an
//! oversight." [`InterfaceSecurity::registration`] has no `oauth2` parameter
//! for the same reason, so the prohibition is unexpressible here rather than
//! merely unwritten.
//!
//! # Why OpenSSL and not a JWT crate
//!
//! The obvious dependency, `jsonwebtoken`, brings `ring` -- a second crypto
//! implementation in a process that already links `libssl`. That would defeat
//! the reason this port moved to OpenSSL at all: one validated module, one
//! certification boundary. Verifying tokens with the same library that
//! terminates TLS keeps that promise, at the cost of assembling the public keys
//! by hand, which is what `_parse_rsa_public_key` and `_parse_ec_public_key` do
//! in Python anyway.
//!
//! # The two-boolean return
//!
//! [`validate_access`] returns `(allowed, valid_token)` because the caller owes
//! different answers to different failures, and the specification's pseudocode
//! distinguishes them:
//!
//! | result | HTTP |
//! |---|---|
//! | `valid_token == false` | 401 -- the token is malformed, expired or unusable |
//! | `valid_token == true, allowed == false` | 403 -- a good token that does not permit this |
//! | both true | the request proceeds |
//!
//! Collapsing the two would tell an unauthorised caller that its token was
//! malformed, or a caller with a stale token that it lacked permission --
//! either way sending it to fix the wrong thing.

use std::sync::Arc;

use base64::Engine as _;
use openssl::bn::BigNum;
use openssl::ec::{EcGroup, EcKey};
use openssl::ecdsa::EcdsaSig;
use openssl::hash::MessageDigest;
use openssl::nid::Nid;
use openssl::pkey::PKey;
use openssl::rsa::Rsa;
use openssl::sign::Verifier;
use parking_lot::RwLock;
use serde_json::{Map, Value};

/// The `typ` values TR-10-SEC §14.3.3.2 permits in the JOSE header.
///
/// Lower-cased: RFC 7515 §4.1.9 makes the comparison case-insensitive.
const PERMITTED_TYP: &[&str] = &["jwt", "at+jwt", "application/at+jwt"];

/// The signing algorithms TR-10-SEC §14.3.3.2 permits.
///
/// The specification widens IS-10's RS512-only list to four, and a Node
/// claiming compliance validates with all of them.
const PERMITTED_ALGORITHMS: &[&str] = &["RS256", "RS512", "ES256", "ES512"];

/// One key from a JWKS keyset.
///
/// Every field is a string because that is how they arrive; `n`, `e`, `x` and
/// `y` are base64url big-endian integers, decoded at verification time.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct JsonWebKey {
    /// Key type: `RSA` or `EC`.
    pub kty: String,
    /// Algorithm: one of [`PERMITTED_ALGORITHMS`].
    pub alg: String,
    /// Key id, matched against the token header's `kid`.
    pub kid: String,
    /// Usage, conventionally `sig`.
    pub use_: String,
    /// RSA modulus.
    pub n: String,
    /// RSA exponent.
    pub e: String,
    /// EC x coordinate.
    pub x: String,
    /// EC y coordinate.
    pub y: String,
}

/// A JSON Web Key Set.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Jwks {
    /// The keys, searched in order by `kid`.
    pub keys: Vec<JsonWebKey>,
}

impl Jwks {
    /// Parse a JWKS document.
    ///
    /// Absent members become empty strings rather than errors, matching
    /// `_parse_jwks`: a keyset holding one unusable key should still offer the
    /// others, and an EC key legitimately has no `n`.
    #[must_use]
    pub fn from_value(document: &Value) -> Self {
        let mut keys = Vec::new();
        if let Some(entries) = document.get("keys").and_then(Value::as_array) {
            for entry in entries {
                let field = |name: &str| {
                    entry
                        .get(name)
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .to_owned()
                };
                keys.push(JsonWebKey {
                    kty: field("kty"),
                    alg: field("alg"),
                    kid: field("kid"),
                    use_: field("use"),
                    n: field("n"),
                    e: field("e"),
                    x: field("x"),
                    y: field("y"),
                });
            }
        }
        Self { keys }
    }

    /// The key with this `kid`, or none.
    ///
    /// Linear and exact, as `_find_key` is. An empty `kid` therefore matches a
    /// key that declares no `kid`, which is the single-key JWKS case.
    #[must_use]
    fn find(&self, kid: &str) -> Option<&JsonWebKey> {
        self.keys.iter().find(|key| key.kid == kid)
    }
}

/// The keyset the running listener validates against, shared and replaceable.
///
/// **Not a plain `Option<Arc<Jwks>>`, and the distinction is load-bearing.**
/// [`InterfaceSecurity`] is cloned into the router's layer when the router is
/// built, and the keys arrive later -- the first successful fetch happens after
/// the listeners are already serving, and every refresh after that. A plain
/// value would be copied into the layer once, at startup, holding `None`
/// forever: the cache would fetch keys nobody could see, and every request
/// would be refused with "no OAuth2 public keys available" while the log
/// cheerfully reported a successful fetch.
///
/// Python has the problem solved by accident -- `check_oauth2` reads
/// `request.app["node"]`, a live object -- so nothing there hints that this
/// needs care.
///
/// A read lock per authenticated request is the cost. It is uncontended
/// essentially always: writes happen once per refresh, which is once a day.
///
/// [`InterfaceSecurity`]: crate::security::InterfaceSecurity
#[derive(Debug, Clone, Default)]
pub struct SharedJwks(Arc<RwLock<Option<Arc<Jwks>>>>);

impl SharedJwks {
    /// An empty handle: no keys, so every bearer token is refused.
    ///
    /// The fail-closed starting state TR-10-SEC §14.3.2 requires -- until the
    /// first fetch succeeds, authenticated access is refused.
    #[must_use]
    pub fn empty() -> Self {
        Self::default()
    }

    /// A handle already holding a keyset, for tests and static configuration.
    #[must_use]
    pub fn with(keys: Jwks) -> Self {
        Self(Arc::new(RwLock::new(Some(Arc::new(keys)))))
    }

    /// The current keyset, or `None` while uninitialised or invalidated.
    #[must_use]
    pub fn get(&self) -> Option<Arc<Jwks>> {
        self.0.read().clone()
    }

    /// Replace the keyset. `None` invalidates, which refuses every token.
    pub fn set(&self, keys: Option<Arc<Jwks>>) {
        *self.0.write() = keys;
    }
}

/// Decode base64url, tolerating absent padding.
///
/// # Parity note
///
/// Python's `_b64url_decode` maps the URL alphabet onto the standard one, pads,
/// and calls `base64.b64decode` **without** `validate=True`, which silently
/// discards characters outside the alphabet. This is strict and rejects them.
///
/// That cannot admit a token this would otherwise refuse, nor refuse one it
/// would otherwise admit in any security-relevant way: the signature is
/// computed over the literal `header.payload` ASCII, so a stray character
/// changes the signed input and the signature fails regardless. The difference
/// is confined to which malformed tokens reach the claims-parsing step, and
/// both implementations answer 401 either way.
fn b64url_decode(text: &str) -> Option<Vec<u8>> {
    base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(text.trim_end_matches('='))
        .ok()
}

/// Verify a token's signature and return its claims.
///
/// Returns `(verified, claims)`. The claims come back **even when verification
/// fails**, exactly as `validate_token_with_claims` does -- a caller that wants
/// to log which client presented a bad token needs them. Nothing may act on
/// unverified claims, which is why the boolean is first and separate.
///
/// A token that is not three parts, whose header is unparseable, whose `typ` or
/// `alg` is outside TR-10-SEC §14.3.3.2, or whose payload is unparseable yields
/// `(false, {})`; once the payload parses, failures yield `(false, claims)`.
#[must_use]
pub fn validate_token_with_claims(token: &str, jwks: &Jwks) -> (bool, Map<String, Value>) {
    let empty = Map::new();

    let parts: Vec<&str> = token.split('.').collect();
    let [header_b64, payload_b64, signature_b64] = parts.as_slice() else {
        return (false, empty);
    };

    let Some(header) =
        b64url_decode(header_b64).and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
    else {
        return (false, empty);
    };

    // §14.3.3.2: `typ` shall be present and one of the three spellings.
    let typ = header
        .get("typ")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !PERMITTED_TYP.contains(&typ.to_ascii_lowercase().as_str()) {
        return (false, empty);
    }

    // §14.3.3.2 also closes the algorithm list. Checked before dispatch so an
    // unsupported `alg` fails here rather than further down -- and so `none`
    // can never reach a branch that might treat it as unsigned.
    let alg = header
        .get("alg")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !PERMITTED_ALGORITHMS.contains(&alg) {
        return (false, empty);
    }

    let kid = header
        .get("kid")
        .and_then(Value::as_str)
        .unwrap_or_default();

    let Some(claims) = b64url_decode(payload_b64)
        .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
        .and_then(|value| match value {
            Value::Object(map) => Some(map),
            _ => None,
        })
    else {
        return (false, empty);
    };

    let Some(key) = jwks.find(kid) else {
        return (false, claims);
    };
    let Some(signature) = b64url_decode(signature_b64) else {
        return (false, claims);
    };

    // The signed input is the two encodings as received, joined by a dot --
    // not a re-encoding of the decoded values, which would differ wherever the
    // sender's base64 padding or field order differed from ours.
    let signed = format!("{header_b64}.{payload_b64}");

    let verified = match alg {
        "RS256" | "RS512" => verify_rsa(key, alg, signed.as_bytes(), &signature),
        "ES256" | "ES512" => verify_ecdsa(key, alg, signed.as_bytes(), &signature),
        _ => false,
    };
    (verified, claims)
}

/// RSASSA-PKCS1-v1_5 over SHA-256 or SHA-512.
fn verify_rsa(key: &JsonWebKey, alg: &str, signed: &[u8], signature: &[u8]) -> bool {
    let (Some(n), Some(e)) = (b64url_decode(&key.n), b64url_decode(&key.e)) else {
        return false;
    };
    let (Ok(n), Ok(e)) = (BigNum::from_slice(&n), BigNum::from_slice(&e)) else {
        return false;
    };
    let Ok(public) = Rsa::from_public_components(n, e).and_then(PKey::from_rsa) else {
        return false;
    };
    let digest = if alg == "RS256" {
        MessageDigest::sha256()
    } else {
        MessageDigest::sha512()
    };
    // PKCS#1 v1.5 is OpenSSL's default padding for an RSA verifier, which is
    // what JWS `RS*` specifies; PSS would be `PS*` and is not on the list.
    Verifier::new(digest, &public)
        .and_then(|mut verifier| {
            verifier.update(signed)?;
            verifier.verify(signature)
        })
        .unwrap_or(false)
}

/// ECDSA over P-256 or P-521.
fn verify_ecdsa(key: &JsonWebKey, alg: &str, signed: &[u8], signature: &[u8]) -> bool {
    // The curve is taken from the **key's** `alg`, not the token header's.
    // That is what `_parse_ec_public_key` does, and the two can disagree: a
    // token saying ES256 verified against a key declaring ES512 gets a P-521
    // curve with a SHA-256 digest and a 64-byte signature split. Such a
    // combination simply fails to verify, so the mismatch is not exploitable,
    // but it is replicated rather than tidied because tidying it would change
    // which tokens are accepted.
    let curve = match key.alg.as_str() {
        "ES256" => Nid::X9_62_PRIME256V1,
        "ES512" => Nid::SECP521R1,
        _ => return false,
    };

    // JWS carries `r || s` fixed-width and raw; OpenSSL wants DER. The width
    // follows the token's `alg`: 32 bytes each for P-256, 66 for P-521. Both
    // halves are stated rather than doubled so there is no arithmetic to get
    // wrong on a length that guards a slice.
    let (width, expected) = if alg == "ES256" { (32, 64) } else { (66, 132) };
    if signature.len() != expected {
        return false;
    }
    let Some((r, s)) = signature.split_at_checked(width) else {
        return false;
    };
    let (Ok(r), Ok(s)) = (BigNum::from_slice(r), BigNum::from_slice(s)) else {
        return false;
    };
    let Ok(der) = EcdsaSig::from_private_components(r, s).and_then(|sig| sig.to_der()) else {
        return false;
    };

    let (Some(x), Some(y)) = (b64url_decode(&key.x), b64url_decode(&key.y)) else {
        return false;
    };
    let (Ok(x), Ok(y)) = (BigNum::from_slice(&x), BigNum::from_slice(&y)) else {
        return false;
    };
    let Ok(group) = EcGroup::from_curve_name(curve) else {
        return false;
    };
    let Ok(public) =
        EcKey::from_public_key_affine_coordinates(&group, &x, &y).and_then(PKey::from_ec_key)
    else {
        return false;
    };

    let digest = if alg == "ES256" {
        MessageDigest::sha256()
    } else {
        MessageDigest::sha512()
    };
    Verifier::new(digest, &public)
        .and_then(|mut verifier| {
            verifier.update(signed)?;
            verifier.verify(&der)
        })
        .unwrap_or(false)
}

/// The tri-state an access attribute evaluates to.
///
/// Distinct from a boolean because "this token does not grant it" and "this
/// token is malformed" owe the caller 403 and 401 respectively.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Access {
    /// Granted.
    Allow,
    /// Refused by a well-formed token.
    Deny,
    /// The token is malformed.
    Invalid,
}

/// Whether one `aud` entry admits this registry.
///
/// Two rules from "NMOS With OAuth2.0", and **either** suffices:
///
/// 1. *serial number* -- the entry contains this instance's BCP-002-02
///    identifier as a substring, and equals one of the server certificate's
///    identities exactly;
/// 2. *DNS name* -- the entry, read as a possibly-wildcarded RFC 4592 pattern,
///    matches one of those identities.
///
/// `use_serial_number_in_aud` no longer selects between them -- both are always
/// tried -- because an Authorization Server may mix the two shapes and an
/// operator should not have to pick one globally.
#[must_use]
pub fn aud_entry_allows_current_node(
    aud_entry: &str,
    node_instance_id: &str,
    tls_server_cert_names: &[String],
    allow_non_tls_for_testing: bool,
) -> bool {
    if aud_entry == "*" {
        return true;
    }

    if !node_instance_id.is_empty() && aud_entry.contains(node_instance_id) {
        if tls_server_cert_names.is_empty() {
            // No server certificate is configured. In production this must fail
            // closed; the bypass exists for in-process tests that cannot run a
            // TLS handshake, and is off unless explicitly enabled.
            if allow_non_tls_for_testing {
                return true;
            }
        } else if tls_server_cert_names.iter().any(|name| name == aud_entry) {
            return true;
        }
    }

    // The DNS rule has nothing to match without certificate identities, and
    // gets no test-mode bypass: the rule is meaningless with no certificate.
    !tls_server_cert_names.is_empty() && matches_dns_wildcard(aud_entry, tls_server_cert_names)
}

/// Whether the pattern matches any of the names.
fn matches_dns_wildcard(pattern: &str, cert_names: &[String]) -> bool {
    cert_names
        .iter()
        .any(|name| dns_wildcard_matches(pattern, name))
}

/// RFC 4592 wildcard matching for one name.
///
/// `*.example.com` matches `sub.example.com` but neither `example.com` nor
/// `a.b.example.com`: a wildcard stands for exactly one label.
fn dns_wildcard_matches(pattern: &str, target: &str) -> bool {
    if let Some(domain) = pattern.strip_prefix("*.") {
        let suffix = format!(".{domain}");
        let Some(prefix) = target.strip_suffix(&suffix) else {
            return false;
        };
        return !prefix.contains('.') && !prefix.is_empty();
    }
    pattern.eq_ignore_ascii_case(target)
}

/// Evaluate a `read` or `write` attribute of an `x-nmos-<api>` private claim.
///
/// Accepted shapes: `["*"]` grants, `[""]` refuses, and a list of signed
/// integers indexes into `aud` -- non-negative entries forming an allow-list
/// that must be satisfied, negative entries an exception list that overrides
/// it. Positive entries must precede negative ones; anything else is malformed.
fn eval_indexed_attr(
    attr: Option<&Value>,
    aud: &[String],
    node_instance_id: &str,
    tls_server_cert_names: &[String],
    allow_non_tls_for_testing: bool,
) -> Access {
    // Absent means no grant -- not an error. A private claim that mentions only
    // `read` refuses `write` by saying nothing about it.
    let Some(attr) = attr else {
        return Access::Deny;
    };
    let Some(entries) = attr.as_array() else {
        return Access::Invalid;
    };

    if let [Value::String(only)] = entries.as_slice() {
        return match only.as_str() {
            "*" => Access::Allow,
            "" => Access::Deny,
            _ => Access::Invalid,
        };
    }
    if entries.is_empty() {
        return Access::Invalid;
    }

    let mut positive: Vec<i64> = Vec::new();
    let mut negative: Vec<i64> = Vec::new();
    let mut seen_negative = false;
    for entry in entries {
        let Some(index) = entry.as_f64() else {
            return Access::Invalid;
        };
        // `as_f64` accepts every JSON number, matching Python's
        // `isinstance(v, (int, float))`, and the truncation below matches its
        // `int(v)`.
        let index = index as i64;
        if index < 0 {
            seen_negative = true;
            negative.push(index);
        } else {
            if seen_negative {
                // Ordering violation: an allow-list entry after an exception.
                return Access::Invalid;
            }
            positive.push(index);
        }
    }

    let matches = |index: i64| -> Access {
        let Ok(at) = usize::try_from(index.abs()) else {
            return Access::Invalid;
        };
        let Some(entry) = aud.get(at) else {
            return Access::Invalid;
        };
        if aud_entry_allows_current_node(
            entry,
            node_instance_id,
            tls_server_cert_names,
            allow_non_tls_for_testing,
        ) {
            Access::Allow
        } else {
            Access::Deny
        }
    };

    if !positive.is_empty() {
        let mut allowed = false;
        for index in positive {
            match matches(index) {
                Access::Invalid => return Access::Invalid,
                Access::Allow => {
                    allowed = true;
                    break;
                }
                Access::Deny => {}
            }
        }
        if !allowed {
            return Access::Deny;
        }
    }

    for index in negative {
        match matches(index) {
            Access::Invalid => return Access::Invalid,
            // The entry names this registry, so the exception applies to it.
            Access::Allow => return Access::Deny,
            Access::Deny => {}
        }
    }

    Access::Allow
}

/// What a verified token's claims permit.
///
/// A faithful rendering of the pseudocode in "NMOS With OAuth2.0". See the
/// module docs for what the two booleans oblige the caller to return.
///
/// The signature is assumed already verified: this reads claims and does no
/// cryptography.
#[must_use]
#[allow(clippy::too_many_arguments)]
pub fn validate_access(
    claims: &Map<String, Value>,
    read_write: bool,
    api_name: &str,
    node_instance_id: &str,
    tls_server_cert_names: &[String],
    use_client_credentials_grant_only: bool,
    allow_non_tls_for_testing: bool,
    now: f64,
) -> (bool, bool) {
    const INVALID: (bool, bool) = (false, false);
    const FORBIDDEN: (bool, bool) = (false, true);

    for required in ["iss", "sub", "aud", "client_id", "exp", "scope"] {
        if !claims.contains_key(required) {
            return INVALID;
        }
    }

    let Some(exp) = claims.get("exp").and_then(Value::as_f64) else {
        return INVALID;
    };
    if now > exp {
        // Expired is invalid, not forbidden: the caller should obtain a fresh
        // token, which is a 401 instruction, not a 403 one.
        return INVALID;
    }

    let (Some(sub), Some(client_id)) = (
        claims.get("sub").and_then(Value::as_str),
        claims.get("client_id").and_then(Value::as_str),
    ) else {
        return INVALID;
    };
    if use_client_credentials_grant_only && sub != client_id {
        return INVALID;
    }

    let Some(scope) = claims.get("scope").and_then(Value::as_str) else {
        return INVALID;
    };
    if !scope.split_whitespace().any(|granted| granted == api_name) {
        return FORBIDDEN;
    }

    let Some(aud) = claims.get("aud").and_then(Value::as_array) else {
        return INVALID;
    };
    let mut audience: Vec<String> = Vec::with_capacity(aud.len());
    for entry in aud {
        let Some(entry) = entry.as_str() else {
            return INVALID;
        };
        audience.push(entry.to_owned());
    }
    if !audience.iter().any(|entry| {
        aud_entry_allows_current_node(
            entry,
            node_instance_id,
            tls_server_cert_names,
            allow_non_tls_for_testing,
        )
    }) {
        return FORBIDDEN;
    }

    // The private claim sits under `ext` when that member is present, and at
    // the top level otherwise -- not "either place": a token carrying `ext`
    // does not get a second chance at the top level.
    let access_key = format!("x-nmos-{api_name}");
    let private = match claims.get("ext") {
        Some(ext) => {
            let Some(ext) = ext.as_object() else {
                return INVALID;
            };
            ext.get(&access_key)
        }
        None => claims.get(&access_key),
    };

    let Some(private) = private else {
        // Scope alone grants reading. Writing needs the private claim, so its
        // absence is a refusal rather than a malformation.
        return (!read_write, true);
    };
    let Some(private) = private.as_object() else {
        return INVALID;
    };

    // Once the private claim is present it governs entirely: the implicit read
    // that scope alone conferred no longer applies.
    let read = eval_indexed_attr(
        private.get("read"),
        &audience,
        node_instance_id,
        tls_server_cert_names,
        allow_non_tls_for_testing,
    );
    if read == Access::Invalid {
        return INVALID;
    }
    if read != Access::Allow {
        return FORBIDDEN;
    }

    let write = eval_indexed_attr(
        private.get("write"),
        &audience,
        node_instance_id,
        tls_server_cert_names,
        allow_non_tls_for_testing,
    );
    if write == Access::Invalid {
        return INVALID;
    }
    // `validate_access` carries a consistency check here -- "write without read
    // is invalid" -- and it is **unreachable**, in the Python and therefore
    // here. The `read != Allow` return a dozen lines above has already taken
    // that path, so by this point `read` is necessarily `Allow` and the second
    // half of the condition is always false.
    //
    // Kept rather than dropped, because removing it would be a silent decision
    // about someone else's code and the shapes are meant to correspond line for
    // line. A token granting write but not read gets 403 from the earlier
    // return, not the 401 this branch intends. Reported rather than changed.
    if write == Access::Allow && read != Access::Allow {
        return INVALID;
    }

    if read_write {
        (read == Access::Allow && write == Access::Allow, true)
    } else {
        (read == Access::Allow, true)
    }
}

/// Whether a client certificate identity matches the token's `client_id`.
///
/// "Mutual TLS Client Certificate Binding": the comparison is case-insensitive,
/// and **a wildcard is never a match**. A certificate for `*.example.com` must
/// not bind a token issued to `anything.example.com`, which is why the wildcard
/// names are skipped outright rather than run through the RFC 4592 matcher.
#[must_use]
pub fn check_client_cert_name(cert_names: &[String], client_id: &str) -> bool {
    cert_names
        .iter()
        .filter(|name| !name.contains('*'))
        .any(|name| name.eq_ignore_ascii_case(client_id))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn claims_of(value: Value) -> Map<String, Value> {
        value.as_object().expect("an object").clone()
    }

    /// Far enough in the future that `exp` is not the thing under test.
    const NOW: f64 = 1_000.0;

    fn good_claims() -> Map<String, Value> {
        claims_of(json!({
            "iss": "https://as.example.com",
            "sub": "client-1",
            "aud": ["*"],
            "client_id": "client-1",
            "exp": 2_000.0,
            "scope": "query connection",
        }))
    }

    // -- scope and audience ------------------------------------------------

    #[test]
    fn a_token_without_the_api_in_scope_is_forbidden_not_invalid() {
        // 403, not 401: the token is fine, it just does not cover this API.
        // Answering 401 would send the caller to re-authenticate, which will
        // hand back a token with the same scope.
        let mut claims = good_claims();
        claims.insert("scope".into(), json!("connection registration"));
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], false, false, NOW),
            (false, true),
        );
    }

    #[test]
    fn scope_matching_is_whole_words() {
        // `querying` must not satisfy `query`; splitting on whitespace is what
        // makes that so, rather than a substring test.
        let mut claims = good_claims();
        claims.insert("scope".into(), json!("querying"));
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], false, false, NOW),
            (false, true),
        );
    }

    #[test]
    fn every_required_claim_is_required() {
        for missing in ["iss", "sub", "aud", "client_id", "exp", "scope"] {
            let mut claims = good_claims();
            claims.remove(missing);
            assert_eq!(
                validate_access(&claims, false, "query", "", &[], false, false, NOW),
                (false, false),
                "a token with no `{missing}` was not treated as invalid",
            );
        }
    }

    #[test]
    fn an_expired_token_is_invalid_rather_than_forbidden() {
        let mut claims = good_claims();
        claims.insert("exp".into(), json!(NOW - 1.0));
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], false, false, NOW),
            (false, false),
        );
    }

    #[test]
    fn the_client_credentials_policy_requires_sub_to_equal_client_id() {
        let mut claims = good_claims();
        claims.insert("sub".into(), json!("someone-else"));
        // Off: the mismatch is tolerated.
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], false, false, NOW),
            (true, true),
        );
        // On: it is not.
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], true, false, NOW),
            (false, false),
        );
    }

    // -- the audience rules ------------------------------------------------

    #[test]
    fn an_empty_certificate_list_fails_the_audience_closed() {
        // The rule that matters most here: with no server certificate, a
        // serial-number audience is refused unless test mode is on. Were this
        // to return true, a registry running without TLS would accept tokens
        // minted for any device.
        assert!(!aud_entry_allows_current_node(
            "reg-SNX00000.example.com",
            "SNX00000",
            &[],
            false,
        ));
        assert!(aud_entry_allows_current_node(
            "reg-SNX00000.example.com",
            "SNX00000",
            &[],
            true,
        ));
    }

    #[test]
    fn the_serial_rule_needs_an_exact_certificate_match_as_well() {
        let names = vec!["registry.example.com".to_owned()];
        // Contains the serial but is not a certificate identity.
        assert!(!aud_entry_allows_current_node(
            "reg-SNX00000.example.com",
            "SNX00000",
            &names,
            false,
        ));
        let names = vec!["reg-SNX00000.example.com".to_owned()];
        assert!(aud_entry_allows_current_node(
            "reg-SNX00000.example.com",
            "SNX00000",
            &names,
            false,
        ));
    }

    #[test]
    fn a_wildcard_audience_replaces_exactly_one_label() {
        // RFC 4592. Getting this wrong in the generous direction would let a
        // token for `*.example.com` address a registry at
        // `internal.secure.example.com`.
        let names = vec!["sub.example.com".to_owned()];
        assert!(aud_entry_allows_current_node(
            "*.example.com",
            "",
            &names,
            false
        ));

        let names = vec!["example.com".to_owned()];
        assert!(
            !aud_entry_allows_current_node("*.example.com", "", &names, false),
            "a wildcard matched the bare domain",
        );

        let names = vec!["a.b.example.com".to_owned()];
        assert!(
            !aud_entry_allows_current_node("*.example.com", "", &names, false),
            "a wildcard matched two labels",
        );
    }

    #[test]
    fn a_star_audience_admits_anyone() {
        assert!(aud_entry_allows_current_node("*", "", &[], false));
    }

    // -- the private claim -------------------------------------------------

    #[test]
    fn without_a_private_claim_scope_grants_read_but_not_write() {
        let claims = good_claims();
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], false, false, NOW),
            (true, true),
        );
        assert_eq!(
            validate_access(&claims, true, "query", "", &[], false, false, NOW),
            (false, true),
            "a write was allowed with no x-nmos-query claim",
        );
    }

    #[test]
    fn a_private_claim_under_ext_is_not_also_looked_for_at_the_top_level() {
        // `ext` present means `ext` is where it lives. A token carrying an
        // empty `ext` alongside a permissive top-level claim must not get the
        // top-level one -- that would be a way to smuggle access past an
        // Authorization Server that writes `ext`.
        let mut claims = good_claims();
        claims.insert("ext".into(), json!({}));
        claims.insert(
            "x-nmos-query".into(),
            json!({"read": ["*"], "write": ["*"]}),
        );
        assert_eq!(
            validate_access(&claims, true, "query", "", &[], false, false, NOW),
            (false, true),
            "a top-level private claim was honoured although `ext` was present",
        );
    }

    #[test]
    fn a_private_claim_removes_the_implicit_read_from_scope() {
        // Presence of the claim makes it authoritative: `read` saying nothing
        // now means no read, where without the claim scope alone sufficed.
        let mut claims = good_claims();
        claims.insert("x-nmos-query".into(), json!({"write": ["*"]}));
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], false, false, NOW),
            (false, true),
        );
    }

    #[test]
    fn write_without_read_is_forbidden_because_the_invalid_branch_is_unreachable() {
        // `validate_access` intends write-without-read to be *invalid* (401),
        // and its consistency check says so -- but the earlier `read != Allow`
        // return has already answered *forbidden* (403) by the time that check
        // is reached, in the Python as much as here. So the observable answer
        // is 403 and the branch below it is dead.
        //
        // This test pins the behaviour that actually occurs rather than the one
        // the code reads as intending. If the dead branch is ever made
        // reachable, this fails and says why.
        let mut claims = good_claims();
        claims.insert("x-nmos-query".into(), json!({"read": [""], "write": ["*"]}));
        assert_eq!(
            validate_access(&claims, true, "query", "", &[], false, false, NOW),
            (false, true),
            "expected 403 from the read-denied return, not 401 from the \
             unreachable consistency check",
        );
    }

    #[test]
    fn a_star_read_and_write_grants_both() {
        let mut claims = good_claims();
        claims.insert(
            "x-nmos-query".into(),
            json!({"read": ["*"], "write": ["*"]}),
        );
        assert_eq!(
            validate_access(&claims, false, "query", "", &[], false, false, NOW),
            (true, true),
        );
        assert_eq!(
            validate_access(&claims, true, "query", "", &[], false, false, NOW),
            (true, true),
        );
    }

    // -- indexed access attributes ----------------------------------------

    #[test]
    fn a_negative_index_before_a_positive_one_is_malformed() {
        // The ordering rule: the allow-list comes first, exceptions after.
        let aud = vec!["*".to_owned(), "other".to_owned()];
        assert_eq!(
            eval_indexed_attr(Some(&json!([-1, 0])), &aud, "", &[], false),
            Access::Invalid,
        );
        assert_eq!(
            eval_indexed_attr(Some(&json!([0, -1])), &aud, "", &[], false),
            Access::Allow,
        );
    }

    #[test]
    fn an_index_past_the_end_of_aud_is_malformed() {
        let aud = vec!["*".to_owned()];
        assert_eq!(
            eval_indexed_attr(Some(&json!([5])), &aud, "", &[], false),
            Access::Invalid,
        );
    }

    #[test]
    fn a_negative_index_naming_this_registry_denies_it() {
        // `[0, -1]` where both entries admit us: the allow-list passes on 0 and
        // the exception at -1 then takes it away.
        let aud = vec!["*".to_owned(), "*".to_owned()];
        assert_eq!(
            eval_indexed_attr(Some(&json!([0, -1])), &aud, "", &[], false),
            Access::Deny,
        );
    }

    #[test]
    fn an_absent_attribute_denies_and_an_empty_list_is_malformed() {
        let aud = vec!["*".to_owned()];
        assert_eq!(eval_indexed_attr(None, &aud, "", &[], false), Access::Deny);
        assert_eq!(
            eval_indexed_attr(Some(&json!([])), &aud, "", &[], false),
            Access::Invalid,
        );
        assert_eq!(
            eval_indexed_attr(Some(&json!([""])), &aud, "", &[], false),
            Access::Deny,
        );
        assert_eq!(
            eval_indexed_attr(Some(&json!(["nonsense"])), &aud, "", &[], false),
            Access::Invalid,
        );
    }

    // -- client certificate binding ---------------------------------------

    #[test]
    fn client_cert_binding_is_case_insensitive_and_refuses_wildcards() {
        let names = vec!["Device.Example.COM".to_owned()];
        assert!(check_client_cert_name(&names, "device.example.com"));

        // The one that matters: a wildcard identity must not bind any token.
        let names = vec!["*.example.com".to_owned()];
        assert!(
            !check_client_cert_name(&names, "anything.example.com"),
            "a wildcard certificate name bound a client_id",
        );
        assert!(
            !check_client_cert_name(&names, "*.example.com"),
            "a wildcard matched even itself",
        );
    }

    // -- the JOSE header ---------------------------------------------------

    fn token_with_header(header: &Value) -> String {
        let encode = |value: &Value| {
            base64::engine::general_purpose::URL_SAFE_NO_PAD
                .encode(serde_json::to_vec(value).expect("serialises"))
        };
        format!(
            "{}.{}.{}",
            encode(header),
            encode(&json!({"sub": "x"})),
            base64::engine::general_purpose::URL_SAFE_NO_PAD.encode([0_u8; 64]),
        )
    }

    #[test]
    fn the_alg_none_downgrade_is_refused() {
        // The classic JWT attack: `alg: none` with an empty signature. The
        // algorithm whitelist is checked before any dispatch, so this cannot
        // reach a branch that might treat the token as unsigned.
        let token = token_with_header(&json!({"typ": "JWT", "alg": "none"}));
        let (verified, claims) = validate_token_with_claims(&token, &Jwks::default());
        assert!(!verified);
        assert!(
            claims.is_empty(),
            "claims were returned for a token rejected at the header",
        );
    }

    #[test]
    fn an_algorithm_outside_the_whitelist_is_refused() {
        // HS256 is the other half of the classic attack -- a symmetric
        // algorithm verified against a public key as if it were the secret.
        for alg in ["HS256", "HS512", "PS256", "RS384", ""] {
            let token = token_with_header(&json!({"typ": "JWT", "alg": alg}));
            let (verified, _) = validate_token_with_claims(&token, &Jwks::default());
            assert!(!verified, "{alg} was accepted");
        }
    }

    #[test]
    fn the_typ_header_is_required_and_case_insensitive() {
        for typ in ["JWT", "jwt", "at+jwt", "AT+JWT", "application/at+jwt"] {
            let token = token_with_header(&json!({"typ": typ, "alg": "RS256"}));
            let (_, claims) = validate_token_with_claims(&token, &Jwks::default());
            assert!(
                !claims.is_empty(),
                "{typ} should have passed the header check and reached the claims",
            );
        }
        for header in [
            json!({"alg": "RS256"}),
            json!({"typ": "JWE", "alg": "RS256"}),
        ] {
            let token = token_with_header(&header);
            let (verified, claims) = validate_token_with_claims(&token, &Jwks::default());
            assert!(!verified);
            assert!(claims.is_empty(), "{header} passed the typ check");
        }
    }

    #[test]
    fn a_token_that_is_not_three_parts_is_refused() {
        for token in ["", "a", "a.b", "a.b.c.d"] {
            let (verified, claims) = validate_token_with_claims(token, &Jwks::default());
            assert!(!verified, "{token:?} was accepted");
            assert!(claims.is_empty());
        }
    }

    #[test]
    fn an_unknown_kid_returns_the_claims_but_does_not_verify() {
        // The claims come back so a caller can log who presented the token;
        // the boolean is what says nothing may be acted on.
        let token = token_with_header(&json!({"typ": "JWT", "alg": "RS256", "kid": "absent"}));
        let (verified, claims) = validate_token_with_claims(&token, &Jwks::default());
        assert!(!verified);
        assert_eq!(claims.get("sub").and_then(Value::as_str), Some("x"));
    }
}
