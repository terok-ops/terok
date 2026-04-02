# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for credential proxy environment wiring.

Exercises the full path: CredentialDB → phantom tokens →
_credential_proxy_env_and_volumes() → env vars and volume mounts.

These tests create real sqlite DBs and verify the output matches
what a task container would receive.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.needs_credential_proxy


class TestProxyEnvIntegration:
    """Verify _credential_proxy_env_and_volumes with real DB."""

    def test_phantom_tokens_injected_for_stored_provider(self, tmp_path: Path) -> None:
        """Stored API key credentials produce phantom env vars (direct transport)."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk-test"})
        db.store_credential("default", "vibe", {"type": "api_key", "key": "vibe-key"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "test-project"

        with (
            patch(
                "terok_sandbox.credential_proxy_lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_credential_proxy_transport", return_value="direct"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"

            env, volumes = _credential_proxy_env_and_volumes(project, "task-1")

        # Claude phantom token
        assert "ANTHROPIC_API_KEY" in env
        assert len(env["ANTHROPIC_API_KEY"]) == 32
        # Claude base URL override (TCP transport via host.containers.internal)
        assert "ANTHROPIC_BASE_URL" in env
        assert "host.containers.internal" in env["ANTHROPIC_BASE_URL"]
        # Vibe phantom token (per-provider — distinct from Claude's)
        assert "MISTRAL_API_KEY" in env
        assert len(env["MISTRAL_API_KEY"]) == 32
        # TCP transport — no socket volume mounts
        assert volumes == []

    def test_unstored_providers_excluded(self, tmp_path: Path) -> None:
        """Providers without stored credentials get no phantom tokens."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "vibe", {"type": "api_key", "key": "k"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "test-project"

        with (
            patch(
                "terok_sandbox.credential_proxy_lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_credential_proxy_transport", return_value="direct"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"

            env, _ = _credential_proxy_env_and_volumes(project, "task-1")

        assert "MISTRAL_API_KEY" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    def test_proxy_not_running_raises(self, tmp_path: Path) -> None:
        """SystemExit when proxy is not running and bypass is off."""
        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        project = MagicMock()
        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_socket_active", return_value=False),
            patch(
                "terok_sandbox.credential_proxy_lifecycle.is_daemon_running",
                return_value=False,
            ),
            pytest.raises(SystemExit, match="not running"),
        ):
            _credential_proxy_env_and_volumes(project, "task-1")

    def test_phantom_token_is_unique_per_task(self, tmp_path: Path) -> None:
        """Each task gets a distinct phantom token."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "proj"

        tokens = []
        for task_id in ("task-1", "task-2"):
            with (
                patch(
                    "terok_sandbox.credential_proxy_lifecycle.is_daemon_running",
                    return_value=True,
                ),
                patch("terok_sandbox.ensure_proxy_reachable"),
                patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
                patch(
                    "terok.lib.core.config.get_credential_proxy_transport",
                    return_value="direct",
                ),
            ):
                mock_cfg = mock_cfg_fn.return_value
                mock_cfg.proxy_db_path = db_path
                mock_cfg.proxy_socket_path = sock_path
                mock_cfg.proxy_port = 18731
                mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"

                env, _ = _credential_proxy_env_and_volumes(project, task_id)
                tokens.append(env["ANTHROPIC_API_KEY"])

        assert tokens[0] != tokens[1]
        assert len(tokens[0]) == 32

    def test_oauth_credential_uses_oauth_phantom_env(self, tmp_path: Path) -> None:
        """OAuth credentials inject CLAUDE_CODE_OAUTH_TOKEN, not ANTHROPIC_API_KEY."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "oauth", "access_token": "tok"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "test-project"

        with (
            patch(
                "terok_sandbox.credential_proxy_lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_credential_proxy_transport", return_value="socket"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"

            env, _ = _credential_proxy_env_and_volumes(project, "task-1")

        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert len(env["CLAUDE_CODE_OAUTH_TOKEN"]) == 32
        assert "ANTHROPIC_API_KEY" not in env
        assert env["ANTHROPIC_UNIX_SOCKET"] == "/tmp/terok-claude-proxy.sock"

    def test_oauth_credential_direct_transport(self, tmp_path: Path) -> None:
        """OAuth + direct transport → CLAUDE_CODE_OAUTH_TOKEN + ANTHROPIC_BASE_URL."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "oauth", "access_token": "tok"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "test-project"

        with (
            patch(
                "terok_sandbox.credential_proxy_lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_credential_proxy_transport", return_value="direct"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"

            env, _ = _credential_proxy_env_and_volumes(project, "task-1")

        assert "CLAUDE_CODE_OAUTH_TOKEN" in env
        assert len(env["CLAUDE_CODE_OAUTH_TOKEN"]) == 32
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env


class TestProxyBypassConfig:
    """Verify the bypass flag skips proxy entirely."""

    def test_bypass_returns_empty(self) -> None:
        """When bypass is set, no proxy interaction occurs."""
        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        project = MagicMock()
        with patch(
            "terok.lib.core.config.get_credential_proxy_bypass",
            return_value=True,
        ):
            env, volumes = _credential_proxy_env_and_volumes(project, "task-1")

        assert env == {}
        assert volumes == []
