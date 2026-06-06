# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""Global runtime configuration — security-sensitive switches.

This module holds process-wide flags that alter *security-relevant*
behaviour of the NMOS Node implementation. Consolidating them here
makes the attack surface auditable: each flag has a single definition,
a single accessor, and a single documented test use-case.

NMOS With OAuth2.0 (spec line 110) and NMOS With Node Reservation
(spec line 57) both require TLS v1.2/v1.3. Production deployments MUST
never bypass those requirements. The only legitimate reason to relax
TLS enforcement is in-process unit tests that intentionally exercise
non-TLS code paths — for those cases, tests opt in by setting
``ALLOW_NON_TLS_FOR_TESTING = True`` at the beginning of setup and
restoring the default afterwards.

Flags default to the secure, spec-conformant value. Any call site
that reads a flag via this module documents *why* a non-default value
is acceptable in comments or the referring docstring.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# TLS test-mode bypass
# ---------------------------------------------------------------------------

ALLOW_NON_TLS_FOR_TESTING: bool = False
"""When True, security checks that would otherwise reject a request
because no TLS transport is present (or ``tls_server_cert_names`` is
empty) will fall through to "allow".

**Default: False** — production behaviour; matches NMOS With OAuth2.0
line 110 ("TLS v1.2 or v1.3 MUST be used when serving HTTP") and NMOS
With Node Reservation line 57 ("bare HTTP MUST NOT be used").

Legitimate callers that set this to True:

  * ``nmos/api/tests/test_oauth2_e2e.py`` — exercises OAuth2 claim
    processing end-to-end against an in-process ``aiohttp_client`` that
    does not run TLS. The tests set the flag so the OAuth2 aud↔cert
    cross-check and mTLS gate don't block the non-TLS transport.
  * ``nmos/api/tests/test_reservation.py`` /
    ``test_oauth2_reservation.py`` — same rationale for the
    Reservation API.

Tests MUST restore the default in teardown (or scope the change with
the ``allow_non_tls_for_testing`` context manager) so one test's
relaxation doesn't leak into unrelated tests.
"""


def allow_non_tls_for_testing() -> bool:
    """Return the current value of the non-TLS test-mode flag."""
    return ALLOW_NON_TLS_FOR_TESTING


class allow_non_tls_for_testing_context:
    """Context manager that sets ``ALLOW_NON_TLS_FOR_TESTING`` for the
    duration of a ``with`` block and restores the prior value on exit.

    Use in tests that need to exercise non-TLS OAuth2 / Reservation
    paths without leaking the relaxed state to other tests::

        with allow_non_tls_for_testing_context():
            # OAuth2 / reservation calls without real TLS
            ...
    """

    def __init__(self) -> None:
        self._prior: bool = False

    def __enter__(self) -> "allow_non_tls_for_testing_context":
        global ALLOW_NON_TLS_FOR_TESTING
        self._prior = ALLOW_NON_TLS_FOR_TESTING
        ALLOW_NON_TLS_FOR_TESTING = True
        return self

    def __exit__(self, *_: object) -> None:
        global ALLOW_NON_TLS_FOR_TESTING
        ALLOW_NON_TLS_FOR_TESTING = self._prior
