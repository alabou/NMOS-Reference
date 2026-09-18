// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! Fetching the Authorization Server's signing keys.
//!
//! Port of `discover_metadata`, `discover_jwks` and `fetch_jwks` from
//! `nmos/oauth2/__init__.py`. This is the only outbound HTTP the registry makes.
//!
//! # Why there is no HTTP-client dependency here
//!
//! `reqwest` would be the obvious reach, and it would bring its own TLS stack --
//! `rustls` by default, or `native-tls` wrapping a second configuration surface.
//! Either way the connection to the Authorization Server would be governed by
//! something other than [`crate::tls`], and TR-10-SEC section 8 applies to it
//! just as much as to an inbound connection. `tr10_tls.py` is explicit that
//! client contexts are restricted too.
//!
//! So the request is assembled on the `hyper` and `tokio-openssl` already in the
//! binary, over a context from [`crate::tls::client_context`]. It is perhaps
//! sixty lines more than `reqwest.get(...)`, and in exchange every TLS
//! connection this process makes or accepts is governed by one policy and one
//! library -- which is the whole reason the port is on OpenSSL.

use std::pin::Pin;
use std::sync::Arc;

use http_body_util::BodyExt as _;
use hyper::body::Bytes;
use hyper::{Request, Uri};
use hyper_util::rt::TokioIo;
use nmos_registry_http::jwks_cache::FetchJwks;
use nmos_registry_http::oauth2::Jwks;
use openssl::ssl::{Ssl, SslContext};
use serde_json::Value;
use tokio::net::TcpStream;

/// Where the Authorization Server is, and how to reach it.
#[derive(Debug, Clone)]
pub struct AuthorizationServer {
    /// `https`, unless `--oauth2DisableTLS` was given.
    pub scheme: String,
    /// `--oauth2Host`.
    pub host: String,
    /// `--oauth2Port`.
    pub port: u16,
    /// `--oauth2ApiSelector`, empty for a Hydra-style deployment.
    pub api_selector: String,
    /// The client TLS policy, absent when the scheme is `http`.
    pub tls: Option<Arc<SslContext>>,
}

impl AuthorizationServer {
    /// The metadata URLs to try, in order.
    ///
    /// Three forms, because Authorization Servers disagree about where the
    /// document lives, and the specification's normative form is not the one
    /// Keycloak uses:
    ///
    /// 1. `/.well-known/oauth-authorization-server[/<selector>]` -- IS-10 and
    ///    RFC 8414 §3.1, the normative placement;
    /// 2. `[/<selector>]/.well-known/oauth-authorization-server` -- Keycloak's,
    ///    with the well-known suffix appended to the issuer;
    /// 3. `[/<selector>]/.well-known/openid-configuration` -- OpenID Connect
    ///    Discovery 1.0, which virtually every compliant server answers.
    ///
    /// With no selector the first two collapse to the same string, and the
    /// duplicate is dropped rather than fetched twice.
    #[must_use]
    pub fn metadata_urls(&self) -> Vec<String> {
        let selector = self.api_selector.trim_matches('/');
        let base = format!("{}://{}:{}", self.scheme, self.host, self.port);

        let mut urls = Vec::with_capacity(3);
        let mut push = |url: String| {
            if !urls.contains(&url) {
                urls.push(url);
            }
        };

        if selector.is_empty() {
            push(format!("{base}/.well-known/oauth-authorization-server"));
            push(format!("{base}/.well-known/openid-configuration"));
        } else {
            push(format!(
                "{base}/.well-known/oauth-authorization-server/{selector}"
            ));
            push(format!(
                "{base}/{selector}/.well-known/oauth-authorization-server"
            ));
            push(format!(
                "{base}/{selector}/.well-known/openid-configuration"
            ));
        }
        urls
    }
}

impl FetchJwks for AuthorizationServer {
    async fn fetch(&self) -> Result<Jwks, String> {
        // Discovery first: the JWKS location is named normatively only by the
        // metadata document's `jwks_uri`, never by a fixed path.
        let mut last_error = String::from("no URL forms were tried");
        let mut metadata = None;
        for url in self.metadata_urls() {
            match get_json(&url, self.tls.as_deref()).await {
                Ok(document) => {
                    metadata = Some(document);
                    break;
                }
                Err(error) => last_error = format!("{url} \u{2192} {error}"),
            }
        }
        let Some(metadata) = metadata else {
            return Err(format!(
                "AS metadata discovery failed (tried {} URL forms); last: {last_error}",
                self.metadata_urls().len(),
            ));
        };

        let jwks_uri = metadata
            .get("jwks_uri")
            .and_then(Value::as_str)
            .filter(|uri| !uri.is_empty())
            .ok_or_else(|| {
                "AS metadata document is missing the 'jwks_uri' field; cannot \
                 locate Public Keys."
                    .to_owned()
            })?;

        let document = get_json(jwks_uri, self.tls.as_deref()).await?;
        Ok(Jwks::from_value(&document))
    }
}

/// How much of a response body will be read.
///
/// A metadata document or keyset is a few kilobytes. The cap is here because
/// this body arrives from a host the registry has not finished authenticating
/// the *contents* of, and an unbounded read is a way for it to exhaust memory.
const MAX_BODY_BYTES: u64 = 1024 * 1024;

/// `GET` a URL and parse the body as JSON.
///
/// A non-200 status is an error, matching `discover_metadata`, which treats
/// anything else as a reason to try the next URL form.
async fn get_json(url: &str, tls: Option<&SslContext>) -> Result<Value, String> {
    let uri: Uri = url.parse().map_err(|error| format!("bad URL: {error}"))?;
    let host = uri.host().ok_or("URL has no host")?.to_owned();
    let https = uri.scheme_str() == Some("https");
    let port = uri.port_u16().unwrap_or(if https { 443 } else { 80 });

    let stream = TcpStream::connect((host.as_str(), port))
        .await
        .map_err(|error| format!("connect: {error}"))?;

    let body = if https {
        let tls = tls.ok_or("https URL but no TLS context was configured")?;
        let mut ssl = Ssl::new(tls).map_err(|error| format!("ssl: {error}"))?;
        // SNI, and the name the certificate is checked against. Without
        // `set_hostname` the server may answer with the wrong certificate, and
        // without verification below, any certificate would do.
        ssl.set_hostname(&host)
            .map_err(|error| format!("sni: {error}"))?;
        ssl.param_mut()
            .set_host(&host)
            .map_err(|error| format!("hostname verification: {error}"))?;
        let mut stream =
            tokio_openssl::SslStream::new(ssl, stream).map_err(|e| format!("tls: {e}"))?;
        Pin::new(&mut stream)
            .connect()
            .await
            .map_err(|error| format!("tls handshake: {error}"))?;
        send(stream, &uri, &host).await?
    } else {
        send(stream, &uri, &host).await?
    };

    serde_json::from_slice(&body).map_err(|error| format!("body is not JSON: {error}"))
}

/// Send the request and read the body, over whatever transport.
async fn send<S>(stream: S, uri: &Uri, host: &str) -> Result<Bytes, String>
where
    S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Send + Unpin + 'static,
{
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(stream))
        .await
        .map_err(|error| format!("http handshake: {error}"))?;
    // The connection drives itself on its own task and ends when the response
    // is done; its error is the request's error, reported below.
    tokio::spawn(async move {
        let _ = connection.await;
    });

    let path = uri.path_and_query().map_or("/", |p| p.as_str());
    let request = Request::builder()
        .uri(path)
        .header(hyper::header::HOST, host)
        .header(hyper::header::ACCEPT, "application/json")
        .body(String::new())
        .map_err(|error| format!("request: {error}"))?;

    let response = sender
        .send_request(request)
        .await
        .map_err(|error| format!("request failed: {error}"))?;
    let status = response.status();
    if status != hyper::StatusCode::OK {
        return Err(format!("HTTP {}", status.as_u16()));
    }

    let body = response.into_body();
    http_body_util::Limited::new(body, usize::try_from(MAX_BODY_BYTES).unwrap_or(usize::MAX))
        .collect()
        .await
        .map_err(|error| format!("reading body: {error}"))
        .map(http_body_util::Collected::to_bytes)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn server(selector: &str) -> AuthorizationServer {
        AuthorizationServer {
            scheme: "https".to_owned(),
            host: "as.example.com".to_owned(),
            port: 4444,
            api_selector: selector.to_owned(),
            tls: None,
        }
    }

    #[test]
    fn without_a_selector_the_duplicate_form_is_dropped() {
        // Forms 1 and 2 are the same string when there is no selector, and
        // fetching the same URL twice before falling back would double the
        // delay a misconfigured Authorization Server costs.
        let urls = server("").metadata_urls();
        assert_eq!(
            urls,
            [
                "https://as.example.com:4444/.well-known/oauth-authorization-server",
                "https://as.example.com:4444/.well-known/openid-configuration",
            ],
        );
    }

    #[test]
    fn a_selector_produces_all_three_forms_in_specification_order() {
        // Order matters: the normative RFC 8414 placement is tried before the
        // Keycloak one, and the OIDC fallback last.
        let urls = server("myapi").metadata_urls();
        assert_eq!(
            urls,
            [
                "https://as.example.com:4444/.well-known/oauth-authorization-server/myapi",
                "https://as.example.com:4444/myapi/.well-known/oauth-authorization-server",
                "https://as.example.com:4444/myapi/.well-known/openid-configuration",
            ],
        );
    }

    #[test]
    fn surrounding_slashes_on_the_selector_are_ignored() {
        assert_eq!(
            server("/myapi/").metadata_urls(),
            server("myapi").metadata_urls()
        );
    }

    #[test]
    fn the_scheme_follows_the_tls_setting() {
        let mut plain = server("");
        plain.scheme = "http".to_owned();
        assert!(
            plain
                .metadata_urls()
                .iter()
                .all(|url| url.starts_with("http://")),
        );
    }

    #[tokio::test]
    async fn an_https_url_without_a_tls_context_is_refused_rather_than_downgraded() {
        // The failure mode this guards: silently falling back to plain HTTP for
        // a fetch whose whole purpose is obtaining token-signing keys.
        let error = get_json("https://127.0.0.1:1/x", None)
            .await
            .expect_err("should not succeed");
        // Either it never reaches TLS (connection refused on port 1) or it
        // reports the missing context; what it must never do is succeed.
        assert!(!error.is_empty());
    }

    #[tokio::test]
    async fn a_malformed_url_is_an_error() {
        assert!(get_json("not a url", None).await.is_err());
        assert!(get_json("https:///no-host", None).await.is_err());
    }
}
