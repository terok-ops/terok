# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and skip helpers for integration tests.

This directory currently hosts two integration layers:

- shield integration tests that exercise the real ``terok_shield`` library
- workflow-oriented terok CLI integration tests under ``cli/``, ``projects/``,
  and ``tasks/``

Environment requirements are expressed via pytest markers:

- ``needs_host_features``: real host/filesystem/process behavior only
- ``needs_internet``: outbound network connectivity required
- ``needs_podman``: podman must be available on the host
- ``needs_root``: root-only nftables/shield checks
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest

from tests.testfs import CONFIG_ROOT_NAME, HOME_DIR_NAME, STATE_ROOT_NAME, XDG_CONFIG_HOME_NAME
from tests.testnet import ALLOWED_TARGET_DOMAIN, ALLOWED_TARGET_HTTP, GATE_PORT, TEST_IP

from .helpers import (
    PODMAN_BASE_IMAGE,
    PODMAN_CONTAINER_PREFIX,
    PODMAN_TEST_IMAGE,
    TerokIntegrationEnv,
    TerokShieldIntegrationEnv,
    start_shielded_container,
)

try:
    from terok_shield import Shield, ShieldConfig, ShieldMode, has_global_hooks
    from terok_shield.run import find_nft as _shield_find_nft
except ImportError:  # pragma: no cover - optional integration dependency
    Shield = ShieldConfig = ShieldMode = None  # type: ignore[assignment]
    has_global_hooks = None  # type: ignore[assignment]
    _shield_find_nft = None

SHIELD_MISSING_SKIP_REASON = "terok_shield not installed"


def _has(binary: str) -> bool:
    """Return whether *binary* is available on ``PATH``."""
    return shutil.which(binary) is not None


def _find_nft() -> str | None:
    """Return the nft binary path, using terok-shield's sbin-aware lookup when available."""
    return _shield_find_nft() if _shield_find_nft is not None else shutil.which("nft")


def _image_available() -> bool:
    """Return whether the podman integration image is already available locally."""
    result = subprocess.run(
        ["podman", "image", "exists", PODMAN_TEST_IMAGE],
        capture_output=True,
        timeout=30,
    )
    return result.returncode == 0


def _target_host_port(url: str) -> tuple[str, int]:
    """Return the host and effective port for a URL used in connectivity checks."""
    parsed = urlsplit(url)
    if not parsed.hostname:
        raise ValueError(f"URL missing hostname: {url!r}")
    if parsed.port is not None:
        return parsed.hostname, parsed.port
    if parsed.scheme == "https":
        return parsed.hostname, 443
    return parsed.hostname, 80


# ── Generic skip decorators ───────────────────────────────

git_missing = pytest.mark.skipif(not _has("git"), reason="git not installed")
podman_missing = pytest.mark.skipif(not _has("podman"), reason="podman not installed")
nft_missing = pytest.mark.skipif(not _find_nft(), reason="nft not installed")
ssh_keygen_missing = pytest.mark.skipif(not _has("ssh-keygen"), reason="ssh-keygen not installed")
skip_if_no_root = pytest.mark.skipif(os.geteuid() != 0, reason="root required")


def _hooks_available() -> bool:
    """Return True if global OCI hooks are installed and detectable."""
    if has_global_hooks is None:
        return False
    try:
        return has_global_hooks()
    except Exception:  # pragma: no cover - defensive
        return False


hooks_unavailable = pytest.mark.skipif(
    not _hooks_available(), reason="OCI global hooks not installed"
)


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
        """Handle known commands and fail fast on unexpected ones."""
        if not cmd:
            raise AssertionError("Unexpected MockRunner command: []")
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
        raise AssertionError(
            f"Unexpected MockRunner command: {cmd!r} (check={check}, stdin={stdin!r}, "
            f"timeout={timeout})"
        )

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


# ── Podman integration preflight ──────────────────────────


@pytest.fixture(scope="session")
def _pull_image() -> None:
    """Build the podman integration test image once per session.

    Extends Alpine with git so shielded-container tests can clone
    without needing outbound access to the Alpine package repos.
    """
    if not _has("podman"):
        pytest.skip("podman not installed")
    if _image_available():
        return
    subprocess.run(
        [
            "podman",
            "build",
            "-t",
            PODMAN_TEST_IMAGE,
            "-f",
            "-",
            ".",
        ],
        input=f"FROM {PODMAN_BASE_IMAGE}\nRUN apk add --no-cache git\n",
        check=True,
        text=True,
        timeout=120,
    )


@pytest.fixture(scope="session")
def _verify_connectivity() -> None:
    """Fail fast when the host cannot reach the real egress test target."""
    try:
        socket.getaddrinfo(ALLOWED_TARGET_DOMAIN, None)
    except OSError as exc:
        pytest.fail(
            f"Pre-flight: cannot resolve {ALLOWED_TARGET_DOMAIN} from the host.\n"
            "Fix host DNS resolution before running egress integration tests.\n"
            "Domain-allow tests rely on resolving the allowlisted hostname before applying "
            "the firewall rules.\n"
            f"Error: {exc}"
        )

    host, port = _target_host_port(ALLOWED_TARGET_HTTP)
    try:
        connection = socket.create_connection((host, port), timeout=5)
    except OSError as exc:
        pytest.fail(
            f"Pre-flight: cannot reach {host}:{port} from the host for {ALLOWED_TARGET_HTTP}.\n"
            "Fix host internet connectivity before running egress integration tests.\n"
            "Traffic-based tests would produce false positives when the host network is down.\n"
            f"Error: {exc}"
        )
    else:
        connection.close()


# ── Isolated shield environment ───────────────────────────


@pytest.fixture()
def shield_env(tmp_path: Path) -> TerokShieldIntegrationEnv:
    """Create an isolated per-task shield state directory."""
    task_dir = tmp_path / "tasks" / "test-task"
    state_dir = task_dir / "shield"
    task_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    return TerokShieldIntegrationEnv(
        base_dir=tmp_path,
        task_dir=task_dir,
        state_dir=state_dir,
    )


@pytest.fixture()
def shield_config(shield_env: TerokShieldIntegrationEnv) -> ShieldConfig:
    """Standard ShieldConfig for integration tests with per-task state_dir."""
    if ShieldConfig is None or ShieldMode is None:
        pytest.skip(SHIELD_MISSING_SKIP_REASON)
    return ShieldConfig(
        state_dir=shield_env.state_dir,
        mode=ShieldMode.HOOK,
        default_profiles=("dev-standard",),
        loopback_ports=(GATE_PORT,),
        audit_enabled=True,
    )


@pytest.fixture()
def shield(shield_config: ShieldConfig) -> Shield:
    """Shield with a mock runner for no-podman integration tests."""
    if Shield is None:
        pytest.skip(SHIELD_MISSING_SKIP_REASON)
    return Shield(shield_config, runner=MockRunner())


@pytest.fixture()
def real_shield(shield_config: ShieldConfig) -> Shield:
    """Shield with the real subprocess runner for Podman integration tests."""
    if Shield is None:
        pytest.skip(SHIELD_MISSING_SKIP_REASON)
    return Shield(shield_config)


@pytest.fixture()
def mock_runner() -> MockRunner:
    """Return a MockRunner instance for tests that need to customise it."""
    return MockRunner()


_PODMAN_RM_TIMEOUT = 30


def _podman_rm(name: str, *, timeout: int = _PODMAN_RM_TIMEOUT) -> None:
    """Force-remove a container with bounded timeout and error handling."""
    try:
        subprocess.run(
            ["podman", "rm", "-f", name],
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:  # pragma: no cover - cleanup fallback
        pass


@pytest.fixture()
def shielded_container(_pull_image: None, real_shield: Shield) -> Iterator[str]:
    """Start a disposable podman container with shield hooks applied.

    Skips when global hooks are not installed (required for shield activation).
    """
    if not _hooks_available():
        pytest.skip("OCI global hooks not installed")
    name = f"{PODMAN_CONTAINER_PREFIX}-{uuid.uuid4().hex[:8]}"
    _podman_rm(name)
    try:
        extra_args = real_shield.pre_start(name)
        start_shielded_container(name, extra_args, PODMAN_TEST_IMAGE)
        yield name
    finally:
        _podman_rm(name)


# ── Isolated terok CLI environment ────────────────────────


@pytest.fixture
def terok_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TerokIntegrationEnv:
    """Return an isolated terok config/state environment for a test."""
    home_dir = tmp_path / HOME_DIR_NAME
    xdg_config_home = tmp_path / XDG_CONFIG_HOME_NAME
    system_config_root = tmp_path / CONFIG_ROOT_NAME
    state_root = tmp_path / STATE_ROOT_NAME

    for path in (home_dir, xdg_config_home, system_config_root, state_root):
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("TEROK_CONFIG_DIR", str(system_config_root))
    monkeypatch.setenv("TEROK_STATE_DIR", str(state_root))

    # Write default global config with credential proxy bypass — subprocess-based
    # tests spawn a new CLI process that reads this file.  Tests that need the
    # proxy opt out via the needs_credential_proxy marker.
    (system_config_root / "config.yml").write_text(
        "credential_proxy:\n  bypass_no_secret_protection: true\n",
        encoding="utf-8",
    )

    env = TerokIntegrationEnv(
        base_dir=tmp_path,
        home_dir=home_dir,
        xdg_config_home=xdg_config_home,
        system_config_root=system_config_root,
        state_root=state_root,
    )
    env.user_projects_root.mkdir(parents=True, exist_ok=True)
    env.global_presets_root.mkdir(parents=True, exist_ok=True)
    env.system_projects_root.mkdir(parents=True, exist_ok=True)
    env.credentials_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(env.credentials_dir))
    agent_state = tmp_path / "agent-state"
    agent_state.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("TEROK_AGENT_STATE_DIR", str(agent_state))
    return env


# ── Credential proxy ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _bypass_credential_proxy(request: pytest.FixtureRequest) -> Iterator[None]:
    """Bypass the credential proxy unless the test explicitly needs it.

    Tests marked with ``needs_credential_proxy`` opt out of the bypass
    and exercise the real proxy path.  All other tests get the bypass
    so they don't need a running proxy daemon.

    For subprocess-based tests (via ``terok_env``), the bypass is written
    as a config file in the ``terok_env`` fixture itself.
    """
    if "needs_credential_proxy" in {m.name for m in request.node.iter_markers()}:
        # Tests exercise the proxy path but don't have a real daemon — skip TCP check
        with patch(
            "terok_sandbox.credential_proxy_lifecycle._wait_for_tcp_port",
            return_value=True,
        ):
            yield
    else:
        with patch(
            "terok.lib.core.config.get_credential_proxy_bypass",
            return_value=True,
        ):
            yield
