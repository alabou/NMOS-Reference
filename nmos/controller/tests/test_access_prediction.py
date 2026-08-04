# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""The access indicator must not promise more than the token permits.

The Controller paints a per-Device indicator before the operator clicks
anything: red when reads are blocked, amber when only writes are, green
otherwise. Green is therefore the *absence* of a recorded objection, which
makes it only as trustworthy as the set of objections the Controller knows
how to raise.

It used to know one: whether the token's ``aud`` covered the Device's Node.
That let a scope-only token — which authorises reads and nothing else — show
green with the tooltip "controller can read and write to this device", and the
first IS-11 or IS-05 write then returned 403 "insufficient permissions". The
information needed to predict that refusal was already in hand; the token was
sitting in the admin session and only one of its claims was being read.

These tests pin the prediction against :func:`nmos.oauth2.validate_access` —
the function the Node itself uses — so the two cannot drift apart silently
again.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from nmos.controller.handlers import _token_privilege_reasons
from nmos.oauth2 import validate_access

#: What the reference Controller requests (``DEFAULT_SCOPES``).
SCOPE = "openid node connection streamcompatibility channelmapping manufacturer"

#: The APIs the Controller drives, and so the ones it must predict for.
APIS = ("node", "connection", "streamcompatibility", "manufacturer")

SERIAL = "SNX00001"
CERT_NAMES = ["XYZ-SNX00001"]


def _claims(**extra: Any) -> dict[str, Any]:
    """A valid, audience-matching token. ``exp`` matters.

    Without it ``validate_access`` reports the token invalid, which looks
    exactly like a permission denial and would make these comparisons
    meaningless.
    """
    claims: dict[str, Any] = {
        "iss": "https://XYZ-SNX00000:9443/realms/TR-10-SEC",
        "sub": "tr-10-sec-operator",
        "client_id": "Example.Company.Device.Client.ABC.SNX00001.example.com",
        "azp": "Example.Company.Device.Client.ABC.SNX00001.example.com",
        "aud": [CERT_NAMES[0]],
        "scope": SCOPE,
        "exp": int(time.time()) + 3600,
    }
    claims.update(extra)
    return claims


def _privileges(**attrs: Any) -> dict[str, Any]:
    """An ``ext`` claim granting ``attrs`` on every API the Controller uses."""
    return {f"x-nmos-{api}": dict(attrs) for api in APIS}


def _node_allows(claims: dict[str, Any], api: str, *, write: bool) -> bool:
    """What the Node will actually decide, via its own function."""
    allowed, valid = validate_access(claims, write, api, SERIAL, CERT_NAMES)
    return bool(allowed and valid)


# ---------------------------------------------------------------------------
# The regression itself
# ---------------------------------------------------------------------------

def test_scope_only_token_is_reported_as_read_only() -> None:
    """The exact shape that produced an unexplained 403.

    ``aud`` covers the Node and ``scope`` names every API, so the audience
    check passes and the old prediction found nothing to report. But no
    ``x-nmos-*`` claim is present, and per "NMOS With OAuth2.0" the absence
    of a ``write`` attribute prevents write access.
    """
    reasons = _token_privilege_reasons(_claims())
    assert reasons["read"] == [], "scope alone does grant read"
    assert reasons["write"], (
        "a scope-only token authorises reads only; predicting write access "
        "for it is what sent operators into an unexplained 403")
    assert "x-nmos-" in reasons["write"][0]


def test_full_privileges_raise_no_objection() -> None:
    """The fixed Authorization Server's tokens must show green.

    The mirror of the case above: over-reporting would be its own bug,
    greying out controls that work.
    """
    reasons = _token_privilege_reasons(
        _claims(ext=_privileges(read=["*"], write=["*"])))
    assert reasons["read"] == []
    assert reasons["write"] == []


def test_explicit_write_denial_is_reported() -> None:
    """``write: [""]`` is an explicit denial, not an omission."""
    reasons = _token_privilege_reasons(
        _claims(ext=_privileges(read=["*"], write=[""])))
    assert reasons["read"] == []
    assert reasons["write"]
    assert "denies write" in reasons["write"][0]


def test_missing_scope_blocks_reads_too() -> None:
    """A scope that omits an API blocks reading it, not just writing.

    Worth distinguishing: this is a red indicator, not amber.
    """
    reasons = _token_privilege_reasons(
        _claims(scope="openid node connection manufacturer",
                ext=_privileges(read=["*"], write=["*"])))
    assert any("streamcompatibility" in r for r in reasons["read"])


def test_read_denial_is_reported() -> None:
    """An ``x-nmos-*`` claim replaces the scope's read grant.

    "The presence of an `x-nmos-*` claim MUST remove the default Read access
    from the `scope` claim for the associated API" — so a claim carrying only
    ``write`` denies reads, however odd that looks.
    """
    reasons = _token_privilege_reasons(_claims(ext=_privileges(write=["*"])))
    assert reasons["read"]
    assert "no read access" in reasons["read"][0]


# ---------------------------------------------------------------------------
# Agreement with the Node
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,claims", [
    ("scope only", _claims()),
    ("read+write", _claims(ext=_privileges(read=["*"], write=["*"]))),
    ("read only", _claims(ext=_privileges(read=["*"]))),
    ("write denied", _claims(ext=_privileges(read=["*"], write=[""]))),
])
def test_prediction_agrees_with_the_node(
    label: str, claims: dict[str, Any],
) -> None:
    """Whatever the indicator implies, the Node must do.

    A disagreement in either direction is a defect: predicting a refusal that
    does not happen greys out working controls, and predicting success that
    does not happen is the 403 this module exists to prevent.
    """
    reasons = _token_privilege_reasons(claims)
    for api in APIS:
        for write in (False, True):
            axis = "write" if write else "read"
            predicted = not reasons[axis]
            actual = _node_allows(claims, api, write=write)
            assert predicted == actual, (
                f"{label}: {api} {axis} — indicator says {predicted}, "
                f"Node says {actual}")


def test_indexed_privileges_are_left_to_the_node() -> None:
    """Indexed forms must not be guessed at.

    ``read``/``write`` may be arrays of signed integers indexing ``aud``.
    Resolving those needs the Node's TLS certificate names, which the
    Controller does not have — so it stays silent rather than risk greying
    out controls that would in fact work. Silence here is deliberate.
    """
    reasons = _token_privilege_reasons(
        _claims(ext=_privileges(read=[0], write=[0])))
    assert reasons["read"] == []
    assert reasons["write"] == []
