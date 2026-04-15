# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Story test: credential proxy integration through build_task_env_and_volumes.

Simulates what happens when a user runs ``terok task new`` on a project
with stored API credentials and SSH keys.  Exercises the full assembly
path: project config -> services.mode -> credential DB -> phantom tokens ->
container env vars and volumes.
"""

from __future__ import annotations

import json
import types
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox import CredentialDB

from terok.lib.core.projects import load_project
from terok.lib.orchestration.environment import build_task_env_and_volumes
from tests.test_utils import mock_git_config, project_env

_ONLINE_YAML = """\
project:
  id: story-proj
  security_class: online
git:
  upstream_url: https://github.com/example/repo.git
  default_branch: main
"""

_GATEKEEPING_YAML = """\
project:
  id: story-proj
  security_class: gatekeeping
git:
  upstream_url: https://github.com/example/repo.git
  default_branch: main
"""

pytestmark = pytest.mark.needs_credential_proxy


def _setup_credentials(ctx: types.SimpleNamespace, cfg: MagicMock) -> None:
    """Populate a mock SandboxConfig with a real credential DB and SSH keys."""
    cfg.proxy_db_path = ctx.state_dir / "proxy" / "credentials.db"
    cfg.proxy_socket_path = ctx.state_dir / "proxy.sock"
    cfg.proxy_port = 18731
    cfg.ssh_keys_json_path = ctx.credentials_dir / "ssh-keys.json"
    cfg.ssh_agent_port = 18732
    cfg.runtime_dir = ctx.state_dir / "runtime"
    cfg.gate_socket_path = cfg.runtime_dir / "gate-server.sock"
    cfg.ssh_agent_socket_path = cfg.runtime_dir / "ssh-agent.sock"
    cfg.credentials_dir = ctx.credentials_dir
    cfg.proxy_db_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.credentials_dir.mkdir(parents=True, exist_ok=True)

    db = CredentialDB(cfg.proxy_db_path)
    db.store_credential("default", "claude", {"type": "api_key", "key": "sk-real-secret"})
    db.close()

    cfg.ssh_keys_json_path.write_text(
        json.dumps(
            {
                "story-proj": [
                    {"private_key": "/tmp/terok-testing/id", "public_key": "ssh-ed25519 AAAA k"}
                ]
            }
        )
    )


class TestProxyEnvStory:
    """Story: ``terok task new`` assembles proxy env for the container."""

    def test_tcp_mode_injects_phantom_tokens(self) -> None:
        """TCP mode (default): phantom tokens + base URL via host.containers.internal."""
        with (
            mock_git_config(),
            project_env(_ONLINE_YAML, project_id="story-proj") as ctx,
            patch("terok.lib.core.config.get_services_mode", return_value="tcp"),
            patch("terok_sandbox.is_proxy_socket_active", return_value=True),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False),
        ):
            cfg = mock_cfg_fn.return_value
            _setup_credentials(ctx, cfg)
            # Executor creates its own SandboxConfig — route to the same mock
            with patch("terok_sandbox.SandboxConfig", return_value=cfg):
                env, volumes = build_task_env_and_volumes(load_project("story-proj"), "abc123")

        # Phantom token for Claude (not the real secret)
        assert env["ANTHROPIC_API_KEY"].startswith("terok-p-")
        assert "sk-real-secret" not in str(env)
        # Base URL points to host via TCP
        assert "host.containers.internal" in env["ANTHROPIC_BASE_URL"]
        # SSH agent token + TCP port
        assert env["TEROK_SSH_AGENT_TOKEN"].startswith("terok-p-")
        assert "TEROK_SSH_AGENT_PORT" in env
        assert "TEROK_SSH_AGENT_SOCKET" not in env
        # No runtime dir mount in TCP mode
        assert not any(str(v.container_path) == "/run/terok" for v in volumes)

    def test_socket_mode_injects_socket_paths(self) -> None:
        """Socket mode: phantom tokens + socket env vars, runtime dir mounted."""
        with (
            mock_git_config(),
            project_env(_ONLINE_YAML, project_id="story-proj") as ctx,
            patch("terok.lib.core.config.get_services_mode", return_value="socket"),
            patch("terok_sandbox.is_proxy_socket_active", return_value=True),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False),
        ):
            cfg = mock_cfg_fn.return_value
            _setup_credentials(ctx, cfg)
            with patch("terok_sandbox.SandboxConfig", return_value=cfg):
                env, volumes = build_task_env_and_volumes(load_project("story-proj"), "abc123")

        # Phantom token (same as TCP -- transport doesn't change auth)
        assert env["ANTHROPIC_API_KEY"].startswith("terok-p-")
        # Socket transport: socket_env injected for Claude
        assert "ANTHROPIC_UNIX_SOCKET" in env
        # SSH agent: socket path (container-visible once executor is updated)
        assert env["TEROK_SSH_AGENT_TOKEN"].startswith("terok-p-")
        assert env["TEROK_SSH_AGENT_SOCKET"].endswith("/ssh-agent.sock")
        assert "TEROK_SSH_AGENT_PORT" not in env
        # Runtime dir mounted for socket access
        assert any(str(v.container_path) == "/run/terok" for v in volumes)

    def test_socket_mode_gate_uses_container_path(self) -> None:
        """Socket mode with gate: TEROK_GATE_SOCKET uses container-visible path."""
        from tests.testnet import GATE_PORT

        with (
            mock_git_config(),
            project_env(_GATEKEEPING_YAML, project_id="story-proj", with_gate=True) as ctx,
        ):
            # Load project BEFORE mock_sandbox_config to get real gate_path
            project = load_project("story-proj")
            gate_base = ctx.base / "sandbox-state" / "gate"

            with (
                patch("terok.lib.core.config.get_services_mode", return_value="socket"),
                patch("terok_sandbox.is_proxy_socket_active", return_value=True),
                patch("terok_sandbox.ensure_proxy_reachable"),
                patch("terok.lib.orchestration.environment.ensure_server_reachable"),
                patch(
                    "terok.lib.orchestration.environment.get_gate_server_port",
                    return_value=GATE_PORT,
                ),
                patch(
                    "terok.lib.orchestration.environment.get_gate_base_path",
                    return_value=gate_base,
                ),
                patch(
                    "terok.lib.orchestration.environment.create_token",
                    return_value="deadbeef" * 4,
                ),
                patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
                patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False),
            ):
                cfg = mock_cfg_fn.return_value
                cfg.gate_socket_path.name = "gate-server.sock"
                cfg.runtime_dir = ctx.state_dir / "runtime"
                _setup_credentials(ctx, cfg)
                with patch("terok_sandbox.SandboxConfig", return_value=cfg):
                    env, volumes = build_task_env_and_volumes(project, "abc123")

        # Gate URL points to localhost (socat bridge), not host.containers.internal
        assert "localhost" in env["CODE_REPO"]
        assert "host.containers.internal" not in env["CODE_REPO"]
        # Gate socket: container-visible path, not host path
        assert env["TEROK_GATE_SOCKET"] == "/run/terok/gate-server.sock"
