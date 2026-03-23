# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration test: bypass containers must retain network connectivity.

Verifies that containers started via the shield bypass path
(bypass_firewall_no_protection) have working DNS and outbound
connectivity.  This catches regressions where post-start operations
(e.g. _maybe_drop_shield) accidentally install blocking nftables rules
into containers that were never shielded.

Requires podman on the host.  Outbound internet is needed for the
DNS/HTTP checks.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.testnet import ALLOWED_TARGET_HTTP, GATE_PORT, LOCALHOST, SLIRP_GATEWAY

from .conftest import podman_missing
from .helpers import (
    PODMAN_CONTAINER_PREFIX,
    PODMAN_SLEEP_COMMAND,
    PODMAN_TEST_IMAGE,
    assert_reachable,
    exec_in_container,
)

pytestmark = [pytest.mark.needs_podman, pytest.mark.needs_internet]


def _detect_rootless_network_mode() -> str:
    """Detect whether podman uses pasta or slirp4netns."""
    try:
        out = subprocess.run(
            ["podman", "info", "-f", "{{.Host.RootlessNetworkCmd}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        cmd = out.stdout.strip()
        return cmd if cmd in ("pasta", "slirp4netns") else "pasta"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "pasta"


def _bypass_network_args() -> list[str]:
    """Replicate terok's _bypass_network_args for the test container."""
    if os.geteuid() == 0:
        return []
    if _detect_rootless_network_mode() == "slirp4netns":
        return [
            "--network",
            "slirp4netns:allow_host_loopback=true",
            "--add-host",
            f"host.containers.internal:{SLIRP_GATEWAY}",
        ]
    return [
        "--network",
        f"pasta:-T,{GATE_PORT}",
        "--add-host",
        f"host.containers.internal:{LOCALHOST}",
    ]


def _podman_rm(name: str) -> None:
    """Force-remove a container."""
    try:
        subprocess.run(["podman", "rm", "-f", name], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        pass


@pytest.fixture()
def bypass_container(_pull_image: None) -> Iterator[str]:
    """Start a container using the bypass network path (no shield)."""
    name = f"{PODMAN_CONTAINER_PREFIX}-bypass-{uuid.uuid4().hex[:8]}"
    _podman_rm(name)
    try:
        result = subprocess.run(
            [
                "podman",
                "run",
                "-d",
                "--name",
                name,
                *_bypass_network_args(),
                PODMAN_TEST_IMAGE,
                *PODMAN_SLEEP_COMMAND,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"podman run failed (exit {result.returncode}):\n  stderr: {result.stderr.strip()}"
            )
        yield name
    finally:
        _podman_rm(name)


@podman_missing
class TestBypassContainerConnectivity:
    """Containers started via the bypass path must have working networking."""

    def test_dns_resolves(self, bypass_container: str) -> None:
        """DNS resolution works inside a bypass container."""
        result = exec_in_container(bypass_container, "nslookup", "example.com", timeout=10)
        assert result.returncode == 0, (
            f"DNS resolution failed inside bypass container: {result.stderr}"
        )

    def test_outbound_http_reachable(self, bypass_container: str) -> None:
        """Outbound HTTP works inside a bypass container."""
        assert_reachable(bypass_container, ALLOWED_TARGET_HTTP, timeout=10)

    def test_shield_down_does_not_break_connectivity(self, bypass_container: str) -> None:
        """Calling shield_down on a bypass container must not kill networking.

        This is the regression test for the bug where _maybe_drop_shield()
        installed a bypass nftables ruleset with input policy drop into a
        container that was never shielded, blocking all pasta-forwarded
        traffic.
        """
        pytest.importorskip("terok_sandbox")

        # Simulate what _maybe_drop_shield does: call shield.down() on a
        # container that was started WITHOUT shield pre_start.
        from terok_sandbox import down as shield_down

        with tempfile.TemporaryDirectory() as td:
            task_dir = Path(td)
            try:
                shield_down(bypass_container, task_dir)
            except Exception:
                pass  # nft missing in alpine, nsenter perms, etc. — tolerable

            # Verify networking regardless of whether shield_down succeeded
            # or failed.  A successful shield_down that installs blocking
            # nftables rules is the exact regression we're catching.
            assert_reachable(bypass_container, ALLOWED_TARGET_HTTP, timeout=10)
