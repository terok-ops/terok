# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration test: shield must remain active after container stop/start.

Verifies the fail-close guarantee: a container created with shield
protection must never run unprotected, even after a stop/start cycle.

Requires podman and nft on the host.  All operations are rootless —
nftables runs in the container's network namespace via
``podman unshare nsenter``.

See: https://github.com/terok-ai/terok-shield/issues/122
"""

import subprocess
import uuid
from collections.abc import Iterator

import pytest

terok_shield = pytest.importorskip("terok_shield")
Shield = terok_shield.Shield
ShieldState = terok_shield.ShieldState

from .conftest import _podman_rm, hooks_unavailable, nft_missing, podman_missing
from .helpers import PODMAN_CONTAINER_PREFIX, PODMAN_TEST_IMAGE, start_shielded_container

pytestmark = pytest.mark.needs_podman


def _container_running(name: str) -> bool:
    """Return True if *name* is a running container."""
    r = subprocess.run(
        ["podman", "inspect", "--format", "{{.State.Running}}", name],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def _podman_stop(name: str, timeout: int = 3) -> None:
    """Stop a container, waiting at most *timeout* seconds."""
    subprocess.run(
        ["podman", "stop", "--time", str(timeout), name],
        capture_output=True,
        timeout=timeout + 15,
    )


def _podman_start(name: str) -> None:
    """Start a stopped container."""
    subprocess.run(
        ["podman", "start", name],
        check=True,
        capture_output=True,
        timeout=30,
    )


@podman_missing
@nft_missing
@hooks_unavailable
@pytest.mark.needs_hooks
class TestShieldRestart:
    """Shield must survive a container stop/start cycle.

    This is an end-to-end behavioural test for the fail-close guarantee
    documented in terok-shield#122.  The test creates a shielded
    container, verifies protection is active, stops and restarts the
    container, then checks whether protection is still enforced.

    Expected results with the *current* (unfixed) code:
    - Initial creation: shield active, rules present         → PASS
    - After restart: shield active, rules present             → FAIL

    """

    @pytest.fixture()
    def container(
        self,
        _pull_image: None,
        real_shield: Shield,  # noqa: PT019
    ) -> Iterator[str]:
        """Shielded container that persists across stop/start (not auto-removed)."""
        name = f"{PODMAN_CONTAINER_PREFIX}-restart-{uuid.uuid4().hex[:8]}"
        _podman_rm(name)
        extra_args = real_shield.pre_start(name)
        start_shielded_container(name, extra_args, PODMAN_TEST_IMAGE)
        yield name
        _podman_rm(name)

    # ── Phase 1: initial creation (should all pass) ──────

    def test_01_container_running_after_creation(self, container: str) -> None:
        """Container is running after initial creation."""
        assert _container_running(container)

    def test_02_shield_active_after_creation(
        self,
        container: str,
        real_shield: Shield,
    ) -> None:
        """Shield state is UP or DOWN (not INACTIVE/ERROR) after creation."""
        state = real_shield.state(container)
        assert state in {ShieldState.UP, ShieldState.DOWN, ShieldState.DOWN_ALL}, (
            f"Shield state after creation: {state!r} (expected UP, DOWN, or DOWN_ALL)"
        )

    def test_03_rules_present_after_creation(
        self,
        container: str,
        real_shield: Shield,
    ) -> None:
        """nft ruleset contains terok_shield table after creation."""
        output = real_shield.rules(container)
        assert "terok_shield" in output, "terok_shield table missing from nft ruleset"

    # ── Phase 2: stop/start cycle (restart) ──────────────

    def test_04_container_stopped(self, container: str) -> None:
        """Container is not running after stop."""
        _podman_stop(container)
        assert not _container_running(container)

    def test_05_container_running_after_restart(self, container: str) -> None:
        """Container is running after restart."""
        _podman_stop(container)
        _podman_start(container)
        assert _container_running(container)

    def test_06_shield_active_after_restart(
        self,
        container: str,
        real_shield: Shield,
    ) -> None:
        """Shield state is UP or DOWN after restart (terok-shield#122)."""
        _podman_stop(container)
        _podman_start(container)
        state = real_shield.state(container)
        assert state in {ShieldState.UP, ShieldState.DOWN, ShieldState.DOWN_ALL}, (
            f"Shield state after restart: {state!r} (expected UP, DOWN, or DOWN_ALL)"
        )

    def test_07_rules_present_after_restart(
        self,
        container: str,
        real_shield: Shield,
    ) -> None:
        """nft ruleset contains terok_shield table after restart (terok-shield#122)."""
        _podman_stop(container)
        _podman_start(container)
        output = real_shield.rules(container)
        assert "terok_shield" in output, "terok_shield table missing from nft ruleset after restart"
