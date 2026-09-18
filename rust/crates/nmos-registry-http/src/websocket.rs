// Copyright (C) 2025-2026 Alain Bouchard
// SPDX-License-Identifier: Apache-2.0

//! The Query API WebSocket endpoint.
//!
//! Serves the `ws_href` handed out by `POST /subscriptions`. There is no
//! mandated path -- `Behaviour - Querying.md:29`: "There is no mandated URL
//! base path for servers to use to provide WebSocket connections. Instead
//! clients SHOULD observe the value of `ws_href`" -- so the path mirrors the
//! subscription's HTTP path purely because that is the least surprising choice.
//!
//! # One `select!` loop, not two tasks
//!
//! Python runs a reader coroutine and a sender coroutine and waits on the
//! first to finish. Here it is a single `tokio::select!` with four branches,
//! which is the same shape with less to tear down:
//!
//! * **`recv()`** -- the reader. Nothing a client sends carries meaning in this
//!   protocol, so frames are read and dropped; the branch exists to notice the
//!   client going away. Without it a closed socket would not be detected until
//!   the next write, which for an idle subscription could be indefinitely.
//! * **`wait()`** -- there may be a grain to send.
//! * **`wait_closed()`** -- the server dropped the subscription. A DELETE of a
//!   persistent subscription must forcibly close its clients (`:19`), and
//!   reaping a non-persistent one does the same. Waiting only on the reader
//!   would leave a deleted subscription serving its socket until the client
//!   happened to go away on its own.
//! * **a 30 s interval** -- the keepalive ping.
//!
//! # axum does not ping for you
//!
//! aiohttp's `WebSocketResponse(heartbeat=30.0)` sends pings on its own.
//! `axum::extract::ws` does not, so the interval branch is not a nicety: a
//! subscription with no traffic sits silent, and an idle NAT or proxy will
//! eventually drop it with neither side noticing. This is the single easiest
//! thing to leave out of a port of this file.
//!
//! # Rate limiting
//!
//! `max_update_rate_ms` is the minimum interval between grains for one
//! connection. The loop waits for work, sends, then sleeps out the remainder of
//! the window before looking again; anything arriving during the sleep
//! accumulates in the buffer and is coalesced per resource. A client asking for
//! 100 ms updates receives at most ten grains a second no matter how busy the
//! registry is, and each carries the net change rather than a replay.
//!
//! The AMWA mock does not implement this at all -- it writes a grain per event.

use std::sync::Arc;
use std::time::Duration;

use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode, Uri};
use axum::response::Response;

use nmos_registry::connection::ConnectionBuffer;
use nmos_registry::grain::build_grain;
use nmos_registry_core::cursor::TaiCursor;

use crate::query::QueryState;
use crate::response::{self, RequestView};

/// How often to ping an otherwise silent connection.
///
/// Matches aiohttp's `heartbeat=30.0`, which is what the Python listener uses.
const PING_INTERVAL: Duration = Duration::from_secs(30);

/// `GET` (upgrade) `/x-nmos/query/v1.3/subscriptions/{subscriptionId}`.
///
/// The 404 is answered **before** the upgrade, deliberately: a client that
/// mistyped a `ws_href` then sees a plain HTTP 404 rather than a socket that
/// opens and immediately dies.
pub async fn subscription_socket(
    State(state): State<QueryState>,
    Path(subscription_id): Path<String>,
    upgrade: WebSocketUpgrade,
    uri: Uri,
    headers: HeaderMap,
) -> Response {
    let path = uri.path().to_owned();
    let view = RequestView::new(&path, &headers);

    if state.subscriptions.get(&subscription_id).is_none() {
        return response::error(
            StatusCode::NOT_FOUND,
            &format!("subscription {subscription_id} was not found"),
            &[],
            Some(&view),
        );
    }

    // Attaching queues the synchronisation burst for THIS connection before any
    // live event can be enqueued, so a client cannot miss a change that lands
    // between its connect and its sync. It is also why `connect` is called here
    // rather than inside the upgraded task: the gap would otherwise be as long
    // as the handshake.
    let Some(connection) = state
        .subscriptions
        .connect(&state.registry, &subscription_id)
    else {
        // Deleted between the check above and here.
        return response::error(
            StatusCode::NOT_FOUND,
            &format!("subscription {subscription_id} was not found"),
            &[],
            Some(&view),
        );
    };

    upgrade.on_upgrade(move |socket| serve(socket, state, connection))
}

/// The lifetime of one connected client.
async fn serve(mut socket: WebSocket, state: QueryState, connection: Arc<ConnectionBuffer>) {
    let interval = Duration::from_millis(u64::from(connection.subscription().max_update_rate_ms));
    let mut ping = tokio::time::interval(PING_INTERVAL);
    // The first tick of a tokio interval fires immediately; skipping it stops
    // every connection pinging the instant it opens.
    ping.tick().await;

    loop {
        tokio::select! {
            // Read and drop. The branch exists to observe the close.
            incoming = socket.recv() => {
                match incoming {
                    None | Some(Err(_)) => break,
                    Some(Ok(Message::Close(_))) => break,
                    Some(Ok(_)) => {}
                }
            }
            () = connection.wait() => {
                if connection.is_closed() {
                    break;
                }
                let pending = connection.drain();
                if pending.is_empty() {
                    // Woken by a close, or by work another drain already took.
                    // An empty grain would violate
                    // `queryapi-subscriptions-websocket.json`, whose `data`
                    // array has `minItems: 1`.
                    continue;
                }
                let Ok(grain) = build_grain(
                    connection.subscription(),
                    &pending,
                    &state.query_id,
                    TaiCursor::now(),
                ) else {
                    // A stored body that is not JSON cannot be spliced. It
                    // cannot be registered either, so this is unreachable
                    // through the Registration API -- but dropping the
                    // connection is a better answer than a malformed grain.
                    break;
                };
                if socket.send(Message::Text(grain.into())).await.is_err() {
                    break;
                }
                if !interval.is_zero() {
                    // Sleep out the rest of the window. Events arriving during
                    // it accumulate and coalesce, which is what makes
                    // `max_update_rate_ms` a real bound rather than a hint.
                    tokio::time::sleep(interval).await;
                }
            }
            () = connection.wait_closed() => break,
            _ = ping.tick() => {
                if socket.send(Message::Ping(Vec::new().into())).await.is_err() {
                    break;
                }
            }
        }
    }

    state.subscriptions.disconnect(&connection);
    // Best effort: the peer may already be gone, which is the common case.
    let _ = socket.send(Message::Close(None)).await;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_ping_interval_matches_the_python_listener() {
        // aiohttp's `WebSocketResponse(heartbeat=30.0)`. axum sends no pings of
        // its own, so this number is the whole keepalive.
        assert_eq!(PING_INTERVAL, Duration::from_secs(30));
    }
}
