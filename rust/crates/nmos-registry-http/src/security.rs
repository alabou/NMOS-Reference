// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! What one listener's security posture is, and the one place mTLS is enforced.
//!
//! Port of `InterfaceSecurity` (`nmos/registry/registry.py:62-113`) and
//! `client_auth_middleware` (`nmos/api/middleware.py:81-121`).
//!
//! # The Registration API never requires OAuth 2.0
//!
//! Not a configuration choice: `NMOS With Control Plane Security.md:105`
//! requires that the Registration API "MUST not require the NMOS Nodes to use
//! OAuth 2.0 authorizations", and `:107` requires the registry's DNS-SD
//! `api_auth` to be false. That is why [`InterfaceSecurity::registration`]
//! exists as a constructor rather than leaving `oauth2` for a caller to set: a
//! flag that could be set wrongly would be a compliance failure expressed as a
//! typo.
//!
//! Access control on that interface is TLS only -- server authentication or
//! mutual authentication -- which is exactly the three-value [`Rap`]
//! enumeration.

use axum::http::{HeaderMap, HeaderName, HeaderValue, Method, StatusCode, header};
use axum::response::Response;

use crate::config;
use crate::oauth2::{self, SharedJwks};
use crate::response::{self, RequestView};

/// Registry Access Policy -- TR-10-SEC §10 / §12.2.
///
/// Meaningful for the **Registration** interface, whose three permitted modes
/// are exactly these.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
#[repr(u8)]
pub enum Rap {
    /// Plain HTTP. A development configuration; TR-10-SEC requires TLS for a
    /// compliant deployment.
    UnrestrictedHttp = 0,
    /// Server-authenticated TLS.
    UnrestrictedHttps = 1,
    /// Mutual TLS.
    RestrictedMtls = 2,
}

impl Rap {
    /// The numeric policy, as TR-10-SEC enumerates it.
    #[must_use]
    pub const fn value(self) -> u8 {
        self as u8
    }
}

/// How one listener is secured.
///
/// Field names are an interface contract rather than internal detail: the
/// middleware reads them, and in Python renaming one silently disabled the
/// check that read it. Rust would catch that at compile time, but the names are
/// kept identical so the two implementations can be read against each other.
#[derive(Debug, Clone, Default)]
pub struct InterfaceSecurity {
    /// When true, state-changing verbs require a verified TLS client
    /// certificate.
    pub client_auth_required: bool,
    /// Whether bearer tokens are required. **Always false on Registration.**
    pub oauth2: bool,
    /// The BCP-002-02 instance identifier, matched against a token's `aud`.
    pub serial_number: String,
    /// CN/SAN identities of our own server certificate, the other half of the
    /// `aud` check.
    pub tls_server_cert_names: Vec<String>,
    /// Selects the TR-10-SEC OAIM mode.
    pub use_serial_number_in_aud: bool,
    /// Restricts accepted grant types.
    pub client_credentials_only: bool,
    /// The Authorization Server's public keys, once fetched.
    ///
    /// `None` means "OAuth 2.0 is on but no keys are available", which is a
    /// refusal rather than a pass: `check_oauth2` answers 401 in that state. An
    /// empty keyset would behave the same way, since no `kid` can be found in
    /// it, but the distinction is worth keeping -- one is a registry that has
    /// not reached its Authorization Server yet, the other is one that has and
    /// was given nothing.
    ///
    /// It is a shared handle rather than a value because the keys arrive
    /// *after* this struct has been cloned into the router's layer -- see
    /// [`SharedJwks`] for why a plain value would silently never update.
    pub oauth2_keys: SharedJwks,
}

impl InterfaceSecurity {
    /// The Registration API's posture.
    ///
    /// `oauth2` is not a parameter, and that is the point -- see the module
    /// docs. TR-10-SEC:105 forbids this interface from requiring OAuth 2.0, so
    /// there is no way to express the non-compliant configuration.
    #[must_use]
    pub fn registration(client_auth_required: bool) -> Self {
        Self {
            client_auth_required,
            oauth2: false,
            ..Self::default()
        }
    }

    /// Classify this interface against the TR-10-SEC RAP enumeration.
    ///
    /// Takes `tls` rather than inferring it, because whether TLS is active
    /// belongs to the **listener** and not to this snapshot. Inferring it from
    /// `client_auth_required` alone would report a server-TLS deployment as
    /// plain HTTP -- the difference between RAP 1 and RAP 0, which is a
    /// compliance claim rather than a cosmetic label.
    #[must_use]
    pub const fn rap_for(&self, tls: bool) -> Rap {
        if !tls {
            return Rap::UnrestrictedHttp;
        }
        if self.client_auth_required {
            return Rap::RestrictedMtls;
        }
        Rap::UnrestrictedHttps
    }
}

/// Whether a verb changes state.
///
/// GET/HEAD/OPTIONS pass the mTLS gate unauthenticated, which preserves Node
/// Reservation's "read-only granted without client certificate" rule
/// (`Node Reservation.md:41-45`).
#[must_use]
pub fn is_read_only(method: &Method) -> bool {
    matches!(*method, Method::GET | Method::HEAD | Method::OPTIONS)
}

/// What the TLS layer established about the peer.
///
/// Three states, not two, because `middleware.py:283-293` distinguishes three
/// and collapsing any pair changes behaviour:
///
/// | state | authenticated? |
/// |---|---|
/// | [`Self::NotTls`] | whatever [`allow_non_tls_for_testing`] says |
/// | [`Self::TlsAnonymous`] | **never** |
/// | [`Self::Verified`] | yes |
///
/// The middle row is the one that matters: a TLS connection whose peer sent no
/// certificate is refused whether or not the test-mode flag is set, so that
/// flag cannot turn a real mTLS deployment permissive. Treating "no names" as a
/// single state would have made it able to.
///
/// [`allow_non_tls_for_testing`]: crate::config::allow_non_tls_for_testing
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub enum PeerIdentity {
    /// Plain HTTP, or a connection whose transport is already gone.
    #[default]
    NotTls,
    /// TLS, but the peer presented no certificate -- or presented one that did
    /// not verify, which the acceptor refuses before it reaches here.
    TlsAnonymous,
    /// TLS with a verified client certificate.
    Verified {
        /// CN and SAN entries from that certificate.
        names: Vec<String>,
    },
}

impl PeerIdentity {
    /// Whether this peer counts as authenticated.
    ///
    /// Mirrors `_client_authenticated` exactly, test-mode branch included.
    #[must_use]
    pub fn is_authenticated(&self) -> bool {
        match self {
            // `transport is None` and `ssl_object is None` both return the
            // flag in Python.
            Self::NotTls => crate::config::allow_non_tls_for_testing(),
            // `peercert is not None` -- reached only on TLS, so the flag does
            // not apply.
            Self::TlsAnonymous => false,
            Self::Verified { names } => !names.is_empty(),
        }
    }

    /// The certificate identities, empty when there are none.
    #[must_use]
    pub fn names(&self) -> &[String] {
        match self {
            Self::Verified { names } => names,
            Self::NotTls | Self::TlsAnonymous => &[],
        }
    }
}

/// The single enforcement point for `client_auth_required`.
///
/// `NMOS With OAuth2.0:110` and `NMOS With Node Reservation:57`: when mTLS is
/// enabled, every state-changing request MUST present a verified client
/// certificate -- regardless of whether OAuth 2.0 is also in use. Keeping it in
/// one place is why no handler re-checks it.
///
/// Returns the refusal, or `None` to let the request through.
#[must_use]
pub fn refuse_unauthenticated(
    security: &InterfaceSecurity,
    method: &Method,
    peer: &PeerIdentity,
    view: &RequestView<'_>,
) -> Option<Response> {
    if is_read_only(method) || !security.client_auth_required || peer.is_authenticated() {
        return None;
    }
    Some(response::error(
        StatusCode::UNAUTHORIZED,
        "TLS client authentication required",
        &[(
            HeaderName::from_static("www-authenticate"),
            HeaderValue::from_static(r#"Bearer realm="nmos-mtls""#),
        )],
        Some(view),
    ))
}

/// Refuse a request whose bearer token does not authorise it.
///
/// Port of `check_oauth2`, which in Python is a per-route decorator rather than
/// middleware. It is folded into the one layer here because its `read_write`
/// argument tracks the method exactly -- `registry/__init__.py` gives `read` to
/// every `GET` and `write` to every `POST` and `DELETE` -- so deriving it from
/// the verb registers the same policy without a decorator per route.
///
/// **`OPTIONS` is exempt**, and that is not an optimisation. Python registers
/// its `OPTIONS` handlers bare, outside `read(...)` and `write(...)`. A CORS
/// preflight carries no credentials by construction, so gating it would answer
/// 401 to every browser before the real request was ever sent.
///
/// Returns the refusal, or `None` to let the request through.
#[must_use]
pub fn refuse_unauthorized(
    security: &InterfaceSecurity,
    method: &Method,
    peer: &PeerIdentity,
    view: &RequestView<'_>,
    headers: &HeaderMap,
    now: f64,
) -> Option<Response> {
    if !security.oauth2 || method == Method::OPTIONS {
        return None;
    }

    let unauthorized = |detail: &str| {
        Some(response::error(
            StatusCode::UNAUTHORIZED,
            detail,
            &[(
                HeaderName::from_static("www-authenticate"),
                www_authenticate("nmos-oauth2"),
            )],
            Some(view),
        ))
    };

    let Some(token) = headers
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .map(str::trim)
    else {
        return unauthorized("bearer token is required");
    };

    let Some(keys) = security.oauth2_keys.get() else {
        // Deliberately *without* a `WWW-Authenticate` header, matching
        // `check_oauth2`: the client has nothing to fix. The registry has not
        // obtained the Authorization Server's keys, so inviting a retry with a
        // different token would be misleading.
        return Some(response::error(
            StatusCode::UNAUTHORIZED,
            "no OAuth2 public keys available",
            &[],
            Some(view),
        ));
    };

    let (verified, claims) = oauth2::validate_token_with_claims(token, &keys);
    if !verified {
        return unauthorized("bearer token is not authorized");
    }

    // Mutual-TLS binding, checked before the access rules and only when the
    // peer presented a certificate: a token must not be usable from a
    // connection that authenticated as someone else. On a listener without
    // client certificates there is nothing to bind to and the check does not
    // apply.
    let names = peer.names();
    if !names.is_empty() {
        let client_id = claims
            .get("client_id")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default();
        if !oauth2::check_client_cert_name(names, client_id) {
            return unauthorized("client certificate does not match client_id");
        }
    }

    let (allowed, valid_token) = oauth2::validate_access(
        &claims,
        !is_read_only(method),
        QUERY_SCOPE,
        &security.serial_number,
        &security.tls_server_cert_names,
        security.client_credentials_only,
        config::allow_non_tls_for_testing(),
        now,
    );
    if !valid_token {
        return unauthorized("bearer token is invalid or malformed");
    }
    if !allowed {
        // 403, and no `WWW-Authenticate`: the token is good and re-presenting
        // it will not help.
        return Some(response::error(
            StatusCode::FORBIDDEN,
            "insufficient permissions",
            &[],
            Some(view),
        ));
    }
    None
}

/// The scope name the Query API requires in a token.
///
/// `QUERY_SCOPE` in `registry/__init__.py`. It is also the `api_name` the
/// private claim is keyed by, as `x-nmos-query`.
pub const QUERY_SCOPE: &str = "query";

/// Seconds since the Unix epoch, for `exp`.
///
/// A clock that is before the epoch yields 0, which expires every token rather
/// than accepting every token -- the safe direction for a machine whose clock
/// is badly wrong.
#[must_use]
pub fn now_seconds() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |since| since.as_secs_f64())
}

/// A `WWW-Authenticate` value for one realm.
///
/// Split out because both the mTLS gate and the OAuth 2.0 one send it, and the
/// realm is the only thing that differs.
#[must_use]
pub fn www_authenticate(realm: &str) -> HeaderValue {
    HeaderValue::from_str(&format!(r#"Bearer realm="{realm}""#))
        .unwrap_or_else(|_| HeaderValue::from_static(r#"Bearer realm="nmos""#))
}

/// The peer identity a request carries, if the TLS layer recorded one.
///
/// Carried as a request **extension**, not a header, and the distinction is a
/// security property rather than a style preference: an extension is set
/// server-side and has no wire representation, so a client cannot assert its
/// own identity. A header would have to be stripped from every inbound request
/// before routing, and the day that strip is forgotten -- or is added to one
/// router and not the other -- any client could walk through the mTLS gate by
/// setting it themselves.
///
/// Absent extension means "not authenticated", which is the safe default for a
/// plaintext listener and for a TLS listener whose peer sent no certificate.
#[must_use]
pub fn peer_of<B>(request: &axum::http::Request<B>) -> PeerIdentity {
    request
        .extensions()
        .get::<PeerIdentity>()
        .cloned()
        .unwrap_or_default()
}

/// The mTLS gate, as a layer over a whole router.
///
/// One layer rather than a check in each handler, matching Python's single
/// `client_auth_middleware`. A per-handler check is one handler away from being
/// forgotten, and the forgotten one is the interesting one.
///
/// # Errors
///
/// Never. The signature is `Result` because that is what
/// `axum::middleware::from_fn` expects of a fallible layer; the refusal is an
/// ordinary response.
pub async fn client_auth_layer(
    axum::extract::State(security): axum::extract::State<InterfaceSecurity>,
    request: axum::extract::Request,
    next: axum::middleware::Next,
) -> Response {
    let peer = peer_of(&request);
    let method = request.method().clone();
    let path = request.uri().path().to_owned();
    let headers = request.headers().clone();
    let view = RequestView::new(&path, &headers);

    // mTLS first, then the bearer token. Python reaches the same order by
    // different means -- `client_auth_middleware` is app-level middleware and
    // `check_oauth2` a route decorator, so the middleware necessarily runs
    // first. The order matters: a caller on an unauthenticated connection
    // should be told to present a certificate, not that its token was rejected.
    if let Some(refusal) = refuse_unauthenticated(&security, &method, &peer, &view) {
        return refusal;
    }
    if let Some(refusal) =
        refuse_unauthorized(&security, &method, &peer, &view, &headers, now_seconds())
    {
        return refusal;
    }
    next.run(request).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::oauth2::Jwks;
    use axum::http::HeaderMap;

    fn view<'a>(path: &'a str, headers: &HeaderMap) -> RequestView<'a> {
        RequestView::new(path, headers)
    }

    // -- the OAuth 2.0 gate ----------------------------------------------

    /// OAuth 2.0 on, with a keyset that cannot verify anything.
    fn oauth2_on() -> InterfaceSecurity {
        InterfaceSecurity {
            oauth2: true,
            oauth2_keys: SharedJwks::with(Jwks::default()),
            ..InterfaceSecurity::default()
        }
    }

    fn status_of(response: Option<Response>) -> Option<StatusCode> {
        response.map(|response| response.status())
    }

    #[test]
    fn the_gate_is_inert_when_oauth2_is_off() {
        // Which is always, on Registration.
        let headers = HeaderMap::new();
        let security = InterfaceSecurity::default();
        for method in [Method::GET, Method::POST, Method::DELETE] {
            assert!(
                refuse_unauthorized(
                    &security,
                    &method,
                    &PeerIdentity::default(),
                    &view("/x-nmos/query/v1.3/", &headers),
                    &headers,
                    0.0,
                )
                .is_none(),
                "{method} was gated although OAuth 2.0 is off",
            );
        }
    }

    #[test]
    fn a_cors_preflight_is_never_gated() {
        // Python registers its OPTIONS handlers outside `read(...)`/`write(...)`.
        // A preflight carries no credentials by construction, so gating it would
        // answer 401 to every browser before the real request was ever sent.
        let headers = HeaderMap::new();
        assert!(
            refuse_unauthorized(
                &oauth2_on(),
                &Method::OPTIONS,
                &PeerIdentity::default(),
                &view("/x-nmos/query/v1.3/subscriptions", &headers),
                &headers,
                0.0,
            )
            .is_none(),
            "an OPTIONS preflight was refused",
        );
    }

    #[test]
    fn a_missing_or_malformed_authorization_header_is_401() {
        for value in [
            None,
            Some("Basic abc"),
            Some("bearer lower"),
            Some("Bearer"),
        ] {
            let mut headers = HeaderMap::new();
            if let Some(value) = value {
                headers.insert(
                    header::AUTHORIZATION,
                    HeaderValue::from_str(value).expect("a header value"),
                );
            }
            let refusal = refuse_unauthorized(
                &oauth2_on(),
                &Method::GET,
                &PeerIdentity::default(),
                &view("/x-nmos/query/v1.3/", &headers),
                &headers,
                0.0,
            );
            assert_eq!(
                status_of(refusal),
                Some(StatusCode::UNAUTHORIZED),
                "{value:?} should not have been accepted as a bearer token",
            );
        }
    }

    #[test]
    fn the_refusal_for_a_missing_token_invites_a_bearer_token() {
        let mut headers = HeaderMap::new();
        headers.insert(header::AUTHORIZATION, HeaderValue::from_static("Basic x"));
        let refusal = refuse_unauthorized(
            &oauth2_on(),
            &Method::GET,
            &PeerIdentity::default(),
            &view("/x-nmos/query/v1.3/", &headers),
            &headers,
            0.0,
        )
        .expect("refused");
        assert_eq!(
            refusal.headers().get("www-authenticate"),
            Some(&www_authenticate("nmos-oauth2")),
        );
    }

    #[test]
    fn having_no_keys_refuses_without_inviting_a_retry() {
        // `check_oauth2` answers 401 here with no `WWW-Authenticate`, because
        // the client has nothing to fix -- the registry has not reached its
        // Authorization Server. Inviting a retry would send the caller to mint
        // another token that will fail identically.
        let mut headers = HeaderMap::new();
        headers.insert(
            header::AUTHORIZATION,
            HeaderValue::from_static("Bearer a.b.c"),
        );
        let security = InterfaceSecurity {
            oauth2: true,
            oauth2_keys: SharedJwks::empty(),
            ..InterfaceSecurity::default()
        };
        let refusal = refuse_unauthorized(
            &security,
            &Method::GET,
            &PeerIdentity::default(),
            &view("/x-nmos/query/v1.3/", &headers),
            &headers,
            0.0,
        )
        .expect("refused");
        assert_eq!(refusal.status(), StatusCode::UNAUTHORIZED);
        assert!(
            refusal.headers().get("www-authenticate").is_none(),
            "a no-keys refusal invited the client to retry",
        );
    }

    #[test]
    fn oauth2_is_unreachable_on_the_registration_interface() {
        // The constructor cannot set `oauth2`, so the gate is structurally
        // inert there -- TR-10-SEC:105 as a property of the type rather than a
        // rule someone has to remember.
        let security = InterfaceSecurity::registration(true);
        assert!(!security.oauth2);
        let headers = HeaderMap::new();
        assert!(
            refuse_unauthorized(
                &security,
                &Method::POST,
                &PeerIdentity::default(),
                &view("/x-nmos/registration/v1.3/resource", &headers),
                &headers,
                0.0,
            )
            .is_none(),
        );
    }

    // -- RAP -------------------------------------------------------------

    #[test]
    fn the_three_registration_modes_are_the_three_raps() {
        // TR-10-SEC §"Registry Access Policy".
        let plain = InterfaceSecurity::registration(false);
        let mutual = InterfaceSecurity::registration(true);

        assert_eq!(plain.rap_for(false), Rap::UnrestrictedHttp);
        assert_eq!(plain.rap_for(true), Rap::UnrestrictedHttps);
        assert_eq!(mutual.rap_for(true), Rap::RestrictedMtls);
    }

    #[test]
    fn rap_is_not_inferred_from_client_auth_alone() {
        // A server-TLS deployment reported as plain HTTP is the difference
        // between RAP 1 and RAP 0 -- a compliance claim, not a label.
        let security = InterfaceSecurity::registration(false);
        assert_ne!(
            security.rap_for(true),
            security.rap_for(false),
            "TLS made no difference to the reported policy",
        );
    }

    #[test]
    fn mutual_tls_without_tls_is_still_rap_zero() {
        // The listener decides. A configuration that asks for client auth on a
        // plaintext listener is RAP 0, because nothing is authenticated.
        assert_eq!(
            InterfaceSecurity::registration(true).rap_for(false),
            Rap::UnrestrictedHttp,
        );
    }

    #[test]
    fn the_rap_numbers_match_tr_10_sec() {
        assert_eq!(Rap::UnrestrictedHttp.value(), 0);
        assert_eq!(Rap::UnrestrictedHttps.value(), 1);
        assert_eq!(Rap::RestrictedMtls.value(), 2);
    }

    #[test]
    fn the_registration_interface_cannot_be_given_oauth2() {
        // TR-10-SEC:105 forbids it, so the constructor does not offer it.
        assert!(!InterfaceSecurity::registration(true).oauth2);
        assert!(!InterfaceSecurity::registration(false).oauth2);
    }

    // -- the mTLS gate ----------------------------------------------------

    #[test]
    fn a_read_only_verb_passes_without_a_certificate() {
        // `Node Reservation.md:41-45` -- read-only is granted without one.
        let security = InterfaceSecurity::registration(true);
        let headers = HeaderMap::new();
        for method in [Method::GET, Method::HEAD, Method::OPTIONS] {
            assert!(
                refuse_unauthenticated(
                    &security,
                    &method,
                    &PeerIdentity::default(),
                    &view("/x", &headers),
                )
                .is_none(),
                "{method} was refused",
            );
        }
    }

    #[test]
    fn a_state_changing_verb_without_a_certificate_is_401() {
        // `NMOS With OAuth2.0:110`.
        let security = InterfaceSecurity::registration(true);
        let headers = HeaderMap::new();
        for method in [Method::POST, Method::PUT, Method::PATCH, Method::DELETE] {
            let refusal = refuse_unauthenticated(
                &security,
                &method,
                &PeerIdentity::default(),
                &view("/x", &headers),
            )
            .unwrap_or_else(|| panic!("{method} was allowed through"));
            assert_eq!(refusal.status(), StatusCode::UNAUTHORIZED);
            assert_eq!(
                refusal
                    .headers()
                    .get("www-authenticate")
                    .and_then(|v| v.to_str().ok()),
                Some(r#"Bearer realm="nmos-mtls""#),
            );
        }
    }

    #[test]
    fn a_state_changing_verb_with_a_certificate_passes() {
        let security = InterfaceSecurity::registration(true);
        let headers = HeaderMap::new();
        let peer = PeerIdentity::Verified {
            names: vec!["node1.example.test".to_owned()],
        };
        assert!(
            refuse_unauthenticated(&security, &Method::POST, &peer, &view("/x", &headers))
                .is_none(),
        );
    }

    #[test]
    fn nothing_is_refused_when_client_auth_is_not_required() {
        // The plaintext and server-TLS deployments must not start demanding
        // certificates nobody was asked for.
        let security = InterfaceSecurity::registration(false);
        let headers = HeaderMap::new();
        assert!(
            refuse_unauthenticated(
                &security,
                &Method::POST,
                &PeerIdentity::default(),
                &view("/x", &headers),
            )
            .is_none(),
        );
    }

    // -- peer identity ----------------------------------------------------

    #[test]
    fn a_peer_with_no_names_is_not_authenticated() {
        assert!(!PeerIdentity::default().is_authenticated());
        assert!(!PeerIdentity::Verified { names: Vec::new() }.is_authenticated(),);
    }

    #[test]
    fn an_identity_is_read_from_the_extension() {
        let mut request = axum::http::Request::new(());
        request.extensions_mut().insert(PeerIdentity::Verified {
            names: vec!["node1.example.test".to_owned()],
        });
        assert_eq!(peer_of(&request).names(), ["node1.example.test"]);
        assert!(peer_of(&request).is_authenticated());
    }

    #[test]
    fn a_request_without_the_extension_is_unauthenticated() {
        // The safe default: a plaintext listener, and a TLS listener whose
        // peer sent no certificate, both land here.
        let request = axum::http::Request::new(());
        assert!(!peer_of(&request).is_authenticated());
    }

    #[test]
    fn a_client_cannot_assert_an_identity_through_headers() {
        // The reason the identity is an extension rather than a header. Every
        // header a client might try is inert, because nothing reads headers to
        // decide this.
        let mut request = axum::http::Request::new(());
        for name in [
            "x-nmos-verified-peer",
            "x-forwarded-client-cert",
            "ssl-client-verify",
        ] {
            request.headers_mut().insert(
                HeaderName::from_bytes(name.as_bytes()).expect("a test header"),
                HeaderValue::from_static("node1.example.test"),
            );
        }
        assert!(
            !peer_of(&request).is_authenticated(),
            "a client-supplied header was accepted as a verified identity",
        );
    }
}
