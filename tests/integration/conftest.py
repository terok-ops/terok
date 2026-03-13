# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for shield integration tests.

Overrides the root autouse ``_mock_shield_helpers`` so the real
``terok_shield`` library is exercised, and provides isolated shield
environments via temporary per-task state directories.
"""

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from terok_shield import Shield, ShieldConfig, ShieldMode

from constants import GATE_PORT, TEST_IP

# ── Skip decorators ────────────────────────────────────────

skip_if_no_podman = pytest.mark.skipif(shutil.which("podman") is None, reason="podman not found")
skip_if_no_root = pytest.mark.skipif(os.geteuid() != 0, reason="root required")


# ── Autouse override ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_shield_helpers() -> Iterator[None]:
    """Override root conftest: let real shield helpers execute."""
    yield


# ── Mock CommandRunner ────────────────────────────────────


class MockRunner:
    """Fake CommandRunner that handles known commands for testing."""

    def __init__(self, rootless_mode: str = "pasta") -> None:
        """Create a mock runner with the given rootless network mode."""
        self._rootless_mode = rootless_mode

    def run(
        self,
        cmd: list[str],
        *,
        check: bool = True,
        stdin: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Handle known commands, return empty string for others."""
        if cmd[:2] == ["podman", "info"]:
            return json.dumps({"host": {"rootlessNetworkCmd": self._rootless_mode}})
        if cmd[0] == "dig":
            return f"{TEST_IP}\n"
        if cmd[0] == "nft" or cmd[:2] == ["podman", "inspect"]:
            return ""
        if cmd[:2] == ["podman", "unshare"]:
            return ""
        return ""

    def has(self, name: str) -> bool:
        """Return True for nft, False otherwise."""
        return name == "nft"

    def nft(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        """No-op nft command."""
        return ""

    def nft_via_nsenter(
        self,
        container: str,
        *args: str,
        pid: str | None = None,
        stdin: str | None = None,
        check: bool = True,
    ) -> str:
        """No-op nft via nsenter."""
        return ""

    def podman_inspect(self, container: str, fmt: str) -> str:
        """Return fake PID."""
        return "12345"

    def dig_all(self, domain: str, *, timeout: int = 10) -> list[str]:
        """Return test IP for any domain."""
        return [TEST_IP]


# ── Isolated shield environment ───────────────────────────


@pytest.fixture()
def shield_env(tmp_path: Path) -> dict[str, Path]:
    """Create an isolated per-task shield state directory.

    Returns a dict with the task_dir and state_dir paths.
    The shield state_dir is ``task_dir / "shield"`` matching the
    production ``_state_dir()`` layout.
    """
    task_dir = tmp_path / "tasks" / "test-task"
    state_dir = task_dir / "shield"
    state_dir.mkdir(parents=True)
    return {
        "task_dir": task_dir,
        "state_dir": state_dir,
    }


# ── Standard test config ─────────────────────────────────


@pytest.fixture()
def shield_config(shield_env: dict[str, Path]) -> ShieldConfig:
    """Standard ShieldConfig for integration tests with per-task state_dir."""
    return ShieldConfig(
        state_dir=shield_env["state_dir"],
        mode=ShieldMode.HOOK,
        default_profiles=("dev-standard",),
        loopback_ports=(GATE_PORT,),
        audit_enabled=True,
    )


# ── Shield instances ──────────────────────────────────────


@pytest.fixture()
def shield(shield_config: ShieldConfig) -> Shield:
    """Shield with a mock runner — for no-podman integration tests."""
    return Shield(shield_config, runner=MockRunner())


@pytest.fixture()
def real_shield(shield_config: ShieldConfig) -> Shield:
    """Shield with the real subprocess runner — for Podman integration tests."""
    return Shield(shield_config)


@pytest.fixture()
def mock_runner() -> MockRunner:
    """Return a MockRunner instance for tests that need to customise it."""
    return MockRunner()
