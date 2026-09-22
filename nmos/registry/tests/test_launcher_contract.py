# Copyright (C) 2025-2026 Alain Bouchard
# SPDX-License-Identifier: Apache-2.0

"""What the launchers refuse, asserted against the launcher this platform runs.

Every case below runs ``start-*.sh`` on Linux and ``start-*.bat`` on Windows,
because those are the files each platform actually uses. It is one contract, not
two: the same argument gets the same refusal, the same wording and the same exit
code on both.

Why this is not simply the ``.sh`` tests
---------------------------------------
It used to be. On Windows ``subprocess`` cannot execute a ``.sh`` at all
(``WinError 193``), and the ``bash`` that would run one is
``C:\\WINDOWS\\system32\\bash.exe`` -- WSL. Pointing the Windows gate at WSL
would have made it green by testing the Linux launchers inside Linux, which is
not what anybody runs on Windows and not what this project uses WSL for:
bringing up an etcd member is the single exception, because etcd rates
windows/amd64 Tier 3.

So the Windows gate has to test the ``.bat`` files. That only became possible
once they refused what their ``.sh`` counterparts refuse -- before it, of the
five refusals ``start-registry-dist-secure.sh`` makes, the ``.bat`` made one,
defaulted a missing member index to 0, and silently ignored ``--oauth2``
entirely. The person most likely to mistype a member index is on the
entry-level platform, so that is the last place the answer should be a
certificate-not-found error rather than a statement of what is wrong.

``--rust`` and ``--managed`` are the two places the platforms genuinely differ,
and both are refusals rather than omissions: the Rust registry is not supported
on Windows, and no etcd member runs there. Silently doing the other thing would
make a mixed-cluster run look like it proved something it never exercised.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

_IS_WINDOWS = sys.platform == "win32"
SUFFIX = ".bat" if _IS_WINDOWS else ".sh"


def _run(name: str, argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Invoke the platform's launcher for ``name`` and capture its refusal.

    ``cmd /c`` because ``CreateProcess`` cannot execute a batch file directly.
    ``encoding`` is pinned rather than left to ``text=True``, which decodes with
    the locale codepage -- the thing that makes a test pass in one console and
    fail in another.
    """
    script = REPO_ROOT / f"{name}{SUFFIX}"
    assert script.is_file(), f"{script} does not exist"
    command = [str(script), *argv]
    if _IS_WINDOWS:
        command = ["cmd", "/c", *command]
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_ROOT,
    )


class TestSecuredDistributedLauncher:
    """``start-registry-dist-secure`` -- the rig with the most to get wrong."""

    @pytest.mark.parametrize("argv,expected", [
        ([], "first argument must be the member index"),
        (["9", "3"], "member index must be 0..2"),
        (["0", "4"], "members must be 1, 3 or 5"),
        # Names its sibling, so the operator is told where to go rather than
        # only where not to be. The sibling is per-platform.
        (["0", "3", "0"], f"start-registry-dist{SUFFIX}"),
        (["0", "3", "9"], "unsupported RAP=9"),
        (["0", "3", "2", "--nap=7"], "unsupported --nap=7"),
        (["0", "3", "2", "--bogus"], "unknown arg"),
    ])
    def test_it_refuses(self, argv: list[str], expected: str) -> None:
        result = _run("start-registry-dist-secure", argv)
        assert result.returncode == 64, result.stderr
        assert expected in result.stderr

    def test_unrestricted_read_only_is_refused_with_oauth2(self) -> None:
        """TR-10-SEC: read access MUST be granted by the OAuth 2.0
        authorizations, so NAP=1 cannot be claimed alongside ``--oauth2``.

        The ``.bat`` ignored both flags entirely until this test existed, which
        is the worse failure of the two: the operator reads the flag they typed
        and believes they got it.
        """
        result = _run(
            "start-registry-dist-secure",
            ["0", "3", "2", "--nap=1", "--oauth2"],
        )
        assert result.returncode == 64, result.stderr
        assert "not allowed" in result.stderr

    @pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only refusal")
    def test_managed_is_refused_on_windows(self) -> None:
        """No etcd member runs on native Windows, so supervising one cannot be
        offered -- and must not be quietly downgraded to external mode."""
        result = _run("start-registry-dist-secure", ["0", "3", "2", "--managed"])
        assert result.returncode == 64, result.stderr
        assert "--managed is not available" in result.stderr


class TestUnsecuredDistributedLauncher:
    @pytest.mark.parametrize("argv", [["x", "3"], ["9", "3"]])
    def test_a_bad_member_index_is_refused(self, argv: list[str]) -> None:
        """Exit 1 here, not 64: this launcher's contract is the looser one, and
        matching it exactly is the point -- the two platforms agree with each
        other, not with a tidier rule invented for one of them."""
        result = _run("start-registry-dist", argv)
        assert result.returncode == 1, result.stderr
        assert "member index must be 0..2" in result.stderr


class TestRaftLauncher:
    """The backend that needs nothing brought up first, on either platform."""

    @pytest.mark.parametrize("argv,expected", [
        (["x"], "first argument must be the member index"),
        (["0", "4"], "members must be 1, 3 or 5"),
        (["9", "3"], "member index must be 0..2"),
        (["0", "3", "0"], "RAP=0"),
        (["0", "3", "7"], "unsupported RAP=7"),
        # A RAP without --secure would be Restricted Registration asked for on
        # a listener that is plain HTTP. Silence would grant neither and say so.
        (["0", "3", "2"], "a RAP only means something with --secure"),
        (["0", "3", "--bogus"], "unknown arg"),
    ])
    def test_it_refuses(self, argv: list[str], expected: str) -> None:
        result = _run("start-registry-raft", argv)
        assert result.returncode == 64, result.stderr
        assert expected in result.stderr

    @pytest.mark.skipif(not _IS_WINDOWS, reason="Windows-only refusal")
    def test_rust_is_refused_on_windows(self) -> None:
        """Starting the Python registry instead would make a mixed-cluster run
        report success for a combination it never ran."""
        result = _run("start-registry-raft", ["0", "3", "--rust"])
        assert result.returncode == 64, result.stderr
        assert "--rust is not available" in result.stderr


class TestStandaloneRegistryLauncher:
    """``start-registry`` -- the launcher with the most policy in it.

    RAP and NAP are security postures, not preferences, so every unsupported
    value is refused by name rather than falling back to a default. A silent
    default here is a deployment that reports a policy it does not have.
    """

    @pytest.mark.parametrize("argv,expected", [
        (["--bogus"], "unknown arg"),
        (["--tct=9"], "unsupported --tct=9"),
        (["9"], "unsupported RAP=9"),
        (["2", "--nap=9"], "unsupported --nap=9"),
        # Both plain-HTTP postures name the launcher that does offer them.
        (["0"], f"start-registry-bare{SUFFIX}"),
        (["2", "--nap=0"], f"start-registry-bare{SUFFIX}"),
        # TR-10-SEC again: NAP=1 cannot be claimed alongside --oauth2.
        (["2", "--nap=1", "--oauth2"], "not allowed"),
        # The second positional is the registration port on both platforms.
        (["2", "0"], "between 2 and 65531"),
    ])
    def test_it_refuses(self, argv: list[str], expected: str) -> None:
        result = _run("start-registry", argv)
        assert result.returncode == 64, result.stderr
        assert expected in result.stderr


@pytest.mark.parametrize(
    "launcher", ["start-node1", "start-node2", "start-node3"],
)
class TestNodeLaunchers:
    """The three Node rigs share one option vocabulary, so they share one test.

    Running each case against all three is the point: they are near-copies, and
    a refusal added to one and forgotten in the others is exactly the drift
    that would otherwise go unnoticed until a rig started with a policy nobody
    asked for.
    """

    @pytest.mark.parametrize("argv,expected", [
        (["--bogus"], "unknown arg"),
        (["--tct=9"], "unsupported --tct=9"),
        (["--oaim=9"], "unsupported --oaim=9"),
        (["--rap=9"], "unsupported --rap=9"),
    ])
    def test_it_refuses(
        self, launcher: str, argv: list[str], expected: str,
    ) -> None:
        result = _run(launcher, argv)
        assert result.returncode == 64, result.stderr
        assert expected in result.stderr


class TestBareNodeLauncher:
    """``start-node1-bare`` validates only ``--rap``, on both platforms.

    Pinned as it is rather than as it arguably should be. ``--tct`` and
    ``--oaim`` are accepted unchecked here by the ``.sh`` as well, so making
    the ``.bat`` stricter would be the two platforms disagreeing, which is the
    thing this file exists to prevent. Tightening both is a separate change to
    the Linux launcher, not a Windows port.
    """

    @pytest.mark.parametrize("argv,expected", [
        (["--bogus"], "unknown arg"),
        (["--rap=9"], "unsupported --rap=9"),
    ])
    def test_it_refuses(self, argv: list[str], expected: str) -> None:
        result = _run("start-node1-bare", argv)
        assert result.returncode == 64, result.stderr
        assert expected in result.stderr


class TestFakeAuthorizationServerLauncher:
    @pytest.mark.parametrize("argv,expected", [
        (["--bogus"], "unknown arg"),
        (["--tct=9"], "unsupported --tct=9"),
    ])
    def test_it_refuses(self, argv: list[str], expected: str) -> None:
        result = _run("start-fake-as", argv)
        assert result.returncode == 64, result.stderr
        assert expected in result.stderr
