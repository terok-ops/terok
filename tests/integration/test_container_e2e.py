# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""End-to-end integration test: shielded container lifecycle.

Starts a real gate server, creates a shielded container with the same
networking terok uses, clones a repo through the gate, and verifies
egress filtering.  This bridges the gap between the fake-podman task
launch tests and the direct shield API tests.

Requires podman, nft, git, and global OCI hooks on the host.
Outbound internet is needed for the egress checks.
"""

from __future__ import annotations

import json
import subprocess
import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.testgit import create_bare_repo_with_branches
from tests.testnet import ALLOWED_TARGET_DOMAIN, ALLOWED_TARGET_HTTP, LOCALHOST

from .conftest import hooks_unavailable, nft_missing, podman_missing
from .helpers import (
    PODMAN_CONTAINER_PREFIX,
    PODMAN_TEST_IMAGE,
    assert_blocked,
    assert_reachable,
    exec_in_container,
)

pytestmark = [pytest.mark.needs_podman, pytest.mark.needs_internet]


# ── In-process gate server ────────────────────────────────


def _start_gate_server(base_path: Path, token_file: Path, port: int) -> threading.Thread:
    """Start the gate HTTP server in a daemon thread on *port*."""
    from terok_sandbox.gate.server import (
        TokenStore,
        _make_handler_class,
        _ThreadingHTTPServer,
    )

    token_store = TokenStore(token_file)
    handler_class = _make_handler_class(base_path, token_store)
    server = _ThreadingHTTPServer((LOCALHOST, port), handler_class)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _find_free_port() -> int:
    """Bind to port 0 and return the OS-assigned port number."""
    import socket

    with socket.socket() as s:
        s.bind((LOCALHOST, 0))
        return s.getsockname()[1]


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture()
def gate_env(tmp_path: Path) -> dict:
    """Set up a gate server with a test repo and return connection info."""
    # Create a bare repo
    project_id = "e2e-test"
    base_path = tmp_path / "gate"
    base_path.mkdir()
    repo_path = base_path / f"{project_id}.git"
    create_bare_repo_with_branches(repo_path, default_branch="main", other_branches=[])

    # Write token file
    token = uuid.uuid4().hex
    token_file = tmp_path / "tokens.json"
    token_file.write_text(
        json.dumps({token: {"project": project_id, "task": "1"}}),
        encoding="utf-8",
    )

    # Start gate server on a free port
    port = _find_free_port()
    _start_gate_server(base_path, token_file, port)

    return {
        "project_id": project_id,
        "token": token,
        "port": port,
        "clone_url": f"http://{token}@host.containers.internal:{port}/{project_id}.git",
    }


@dataclass
class ShieldedContainer:
    """A running shielded container with its associated shield instance."""

    name: str
    shield: object  # terok_shield.Shield


@pytest.fixture()
def shielded_e2e(_pull_image: None, gate_env: dict) -> Iterator[ShieldedContainer]:
    """Start a shielded container with gate port forwarding."""
    _terok_shield = pytest.importorskip("terok_shield")

    port = gate_env["port"]
    name = f"{PODMAN_CONTAINER_PREFIX}-e2e-{uuid.uuid4().hex[:8]}"

    # Create shield with the test gate port as loopback port
    state_dir = Path(f"/tmp/terok-e2e-shield-{uuid.uuid4().hex[:8]}")
    state_dir.mkdir(parents=True)

    config = _terok_shield.ShieldConfig(
        state_dir=state_dir,
        mode=_terok_shield.ShieldMode.HOOK,
        default_profiles=("dev-standard",),
        loopback_ports=(port,),
        audit_enabled=True,
    )
    shield = _terok_shield.Shield(config)

    try:
        extra_args = shield.pre_start(name)
        result = subprocess.run(
            [
                "podman",
                "run",
                "-d",
                "--name",
                name,
                *extra_args,
                PODMAN_TEST_IMAGE,
                "sleep",
                "300",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"podman run failed (exit {result.returncode}):\n  stderr: {result.stderr.strip()}"
            )
        # Install git in the alpine container
        subprocess.run(
            ["podman", "exec", name, "apk", "add", "--no-cache", "git"],
            capture_output=True,
            timeout=60,
        )
        yield ShieldedContainer(name=name, shield=shield)
    finally:
        try:
            subprocess.run(["podman", "rm", "-f", name], capture_output=True, timeout=30)
        except subprocess.TimeoutExpired:
            pass
        import shutil

        shutil.rmtree(state_dir, ignore_errors=True)


# ── Tests ─────────────────────────────────────────────────


@podman_missing
@nft_missing
@hooks_unavailable
@pytest.mark.needs_hooks
class TestShieldedContainerLifecycle:
    """Full lifecycle: shielded container starts, clones via gate, has filtered egress."""

    def test_clone_via_gate(self, shielded_e2e: ShieldedContainer, gate_env: dict) -> None:
        """Container can clone a repo through the HTTP gate server."""
        clone_url = gate_env["clone_url"]
        result = exec_in_container(
            shielded_e2e.name,
            "git",
            "clone",
            clone_url,
            "/tmp/repo",
            timeout=30,
        )
        assert result.returncode == 0, (
            f"git clone via gate failed:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
        )
        # Verify the clone has content
        ls_result = exec_in_container(shielded_e2e.name, "ls", "/tmp/repo/README.md", timeout=5)
        assert ls_result.returncode == 0, "Cloned repo missing README.md"

    def test_dns_resolves(self, shielded_e2e: ShieldedContainer) -> None:
        """DNS resolution works inside a shielded container."""
        result = exec_in_container(shielded_e2e.name, "nslookup", "example.com", timeout=10)
        assert result.returncode == 0, f"DNS resolution failed: {result.stderr}"

    def test_allowed_domain_reachable(self, shielded_e2e: ShieldedContainer) -> None:
        """After shield.allow, the allowed domain is reachable."""
        shielded_e2e.shield.allow(shielded_e2e.name, ALLOWED_TARGET_DOMAIN)
        assert_reachable(shielded_e2e.name, ALLOWED_TARGET_HTTP, timeout=10)

    def test_blocked_by_default(self, shielded_e2e: ShieldedContainer) -> None:
        """Egress to non-allowed hosts is blocked by default."""
        assert_blocked(shielded_e2e.name, ALLOWED_TARGET_HTTP, timeout=5)
