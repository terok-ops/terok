# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tier 2 integration tests: full end-to-end with real Podman.

These tests require ``podman`` on PATH and are auto-skipped when it
is absent.  Some additionally require root for nftables operations.

Uses the per-task Shield class API.
"""

import json
import subprocess
import uuid

import pytest

terok_shield = pytest.importorskip("terok_shield")
Shield = terok_shield.Shield

from constants import EGRESS_DOMAIN, TEST_EGRESS_URL, TEST_IP_RFC5737

from .conftest import skip_if_no_podman, skip_if_no_root

pytestmark = pytest.mark.needs_podman


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture()
def podman_container(real_shield: Shield) -> str:
    """Start a real Podman container with shield args, yield its name, cleanup."""
    cname = f"terok-shield-integ-{uuid.uuid4().hex[:8]}"
    args = real_shield.pre_start(cname)

    cmd = ["podman", "run", "-d", "--name", cname, *args, "alpine:latest", "sleep", "300"]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except BaseException:
        subprocess.run(["podman", "rm", "-f", cname], capture_output=True)
        raise

    yield cname

    subprocess.run(["podman", "rm", "-f", cname], capture_output=True)


# ── TestShieldEndToEnd ───────────────────────────────────


@skip_if_no_podman
class TestShieldEndToEnd:
    """End-to-end tests with a real container."""

    def test_container_starts_with_annotations(self, podman_container: str) -> None:
        """Inspect container and verify shield annotations are present."""
        result = subprocess.run(
            ["podman", "inspect", podman_container, "--format", "json"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        info = json.loads(result.stdout)
        annotations = info[0].get("Config", {}).get("Annotations", {})
        assert "terok.shield.profiles" in annotations
        assert "dev-standard" in annotations["terok.shield.profiles"]

    @skip_if_no_root
    def test_shield_rules_returns_ruleset(self, podman_container: str, real_shield: Shield) -> None:
        """shield.rules returns a non-empty ruleset containing terok_shield."""
        output = real_shield.rules(podman_container)
        assert "terok_shield" in output

    @skip_if_no_root
    def test_allow_then_deny(self, podman_container: str, real_shield: Shield) -> None:
        """shield.allow adds an IP; shield.deny removes it."""
        allowed = real_shield.allow(podman_container, TEST_IP_RFC5737)
        assert TEST_IP_RFC5737 in allowed
        rules_after_allow = real_shield.rules(podman_container)
        assert TEST_IP_RFC5737 in rules_after_allow

        denied = real_shield.deny(podman_container, TEST_IP_RFC5737)
        assert TEST_IP_RFC5737 in denied
        rules_after_deny = real_shield.rules(podman_container)
        assert TEST_IP_RFC5737 not in rules_after_deny


# ── TestShieldEgress ──────────────────────────────────────


@skip_if_no_podman
@skip_if_no_root
class TestShieldEgress:
    """Egress filtering tests (requires network + root)."""

    def test_egress_blocked_by_default(self, podman_container: str) -> None:
        """Container cannot reach an external host by default."""
        result = subprocess.run(
            [
                "podman",
                "exec",
                podman_container,
                "wget",
                "-q",
                "-O",
                "/dev/null",
                "--timeout=5",
                TEST_EGRESS_URL,
            ],
            capture_output=True,
            timeout=30,
        )
        assert result.returncode != 0

    def test_egress_allowed_after_allow(self, podman_container: str, real_shield: Shield) -> None:
        """Container can reach a host after shield.allow."""
        real_shield.allow(podman_container, EGRESS_DOMAIN)

        result = subprocess.run(
            [
                "podman",
                "exec",
                podman_container,
                "wget",
                "-q",
                "-O",
                "/dev/null",
                "--timeout=10",
                TEST_EGRESS_URL,
            ],
            capture_output=True,
            timeout=45,
        )
        assert result.returncode == 0
