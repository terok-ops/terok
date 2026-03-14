# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and skip helpers for integration tests.

This directory currently hosts two integration layers:

- shield integration tests that exercise the real ``terok_shield`` library
- workflow-oriented terok CLI integration tests under ``cli/``, ``projects/``,
  and ``tasks/``

Environment requirements are expressed via pytest markers:

- ``needs_host_features``: real host/filesystem/process behavior only
- ``needs_network``: outbound network connectivity required
- ``needs_podman``: podman must be available on the host
- ``needs_root``: root-only nftables/shield checks
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from constants import GATE_PORT, TEST_IP

from .helpers import TerokIntegrationEnv

try:
    from terok_shield import Shield, ShieldConfig, ShieldMode
except ImportError:  # pragma: no cover - optional integration dependency
    Shield = ShieldConfig = ShieldMode = None  # type: ignore[assignment]


def _has(binary: str) -> bool:
    """Return whether *binary* is available on ``PATH``."""
    return shutil.which(binary) is not None


# ── Generic skip decorators ───────────────────────────────

git_missing = pytest.mark.skipif(not _has("git"), reason="git not installed")
skip_if_no_podman = pytest.mark.skipif(not _has("podman"), reason="podman not found")
podman_missing = pytest.mark.skipif(not _has("podman"), reason="podman not installed")
skip_if_no_root = pytest.mark.skipif(os.geteuid() != 0, reason="root required")


# ── Autouse override for shield tests ─────────────────────


@pytest.fixture(autouse=True)
def _mock_shield_helpers() -> Iterator[None]:
    """Override root conftest so real shield helpers execute."""
    yield


# ── Mock shield CommandRunner ─────────────────────────────


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
            return json.dumps(
                {
                    "host": {"rootlessNetworkCmd": self._rootless_mode},
                    "version": {"Version": "5.6.0"},
                }
            )
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
        """Return the test IP for any domain."""
        return [TEST_IP]


# ── Isolated shield environment ───────────────────────────


@pytest.fixture()
def shield_env(tmp_path: Path) -> dict[str, Path]:
    """Create an isolated per-task shield state directory."""
    task_dir = tmp_path / "tasks" / "test-task"
    state_dir = task_dir / "shield"
    state_dir.mkdir(parents=True)
    return {
        "task_dir": task_dir,
        "state_dir": state_dir,
    }


@pytest.fixture()
def shield_config(shield_env: dict[str, Path]) -> ShieldConfig:
    """Standard ShieldConfig for integration tests with per-task state_dir."""
    if ShieldConfig is None or ShieldMode is None:
        pytest.skip("terok_shield not installed")
    return ShieldConfig(
        state_dir=shield_env["state_dir"],
        mode=ShieldMode.HOOK,
        default_profiles=("dev-standard",),
        loopback_ports=(GATE_PORT,),
        audit_enabled=True,
    )


@pytest.fixture()
def shield(shield_config: ShieldConfig) -> Shield:
    """Shield with a mock runner for no-podman integration tests."""
    if Shield is None:
        pytest.skip("terok_shield not installed")
    return Shield(shield_config, runner=MockRunner())


@pytest.fixture()
def real_shield(shield_config: ShieldConfig) -> Shield:
    """Shield with the real subprocess runner for Podman integration tests."""
    if Shield is None:
        pytest.skip("terok_shield not installed")
    return Shield(shield_config)


@pytest.fixture()
def mock_runner() -> MockRunner:
    """Return a MockRunner instance for tests that need to customise it."""
    return MockRunner()


# ── Isolated terok CLI environment ────────────────────────


@pytest.fixture
def terok_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TerokIntegrationEnv:
    """Return an isolated terok config/state environment for a test."""
    home_dir = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg-config"
    system_config_root = tmp_path / "config"
    state_root = tmp_path / "state"

    for path in (home_dir, xdg_config_home, system_config_root, state_root):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("TEROK_CONFIG_DIR", str(system_config_root))
    monkeypatch.setenv("TEROK_STATE_DIR", str(state_root))

    env = TerokIntegrationEnv(
        base_dir=tmp_path,
        home_dir=home_dir,
        xdg_config_home=xdg_config_home,
        system_config_root=system_config_root,
        state_root=state_root,
    )
    env.user_projects_root.mkdir(parents=True, exist_ok=True)
    env.global_presets_root.mkdir(parents=True, exist_ok=True)
    return env
