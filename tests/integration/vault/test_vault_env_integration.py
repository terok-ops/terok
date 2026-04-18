# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for vault environment wiring.

Exercises the full path: CredentialDB → phantom tokens →
_vault_env_and_volumes() → env vars and volume mounts.

These tests create real sqlite DBs and verify the output matches
what a task container would receive.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.needs_vault


@pytest.fixture
def vault_env(tmp_path: Path) -> SimpleNamespace:
    """Set up the common integration scaffolding used by ``TestVaultEnvIntegration``.

    Returns a namespace with:

    - ``tmp_path``: the per-test tmp directory
    - ``db_path``: the planned sqlite DB path (CredentialDB creates parents)
    - ``sock_path``: an existing socket-path stub (touched file)
    - ``project``: a ``MagicMock`` project with ``id="test-project"``
    - ``setup_mock_cfg(mock_cfg_fn)``: wires the standard field values onto
      the ``make_sandbox_config`` patched mock — caller invokes inside the
      existing ``with (patch(...)) as mock_cfg_fn`` block.
    """
    db_path = tmp_path / "proxy" / "credentials.db"
    sock_path = tmp_path / "proxy.sock"
    sock_path.touch()
    ssh_keys_path = tmp_path / "ssh-keys.json"

    project = MagicMock()
    project.id = "test-project"

    def setup_mock_cfg(mock_cfg_fn: MagicMock) -> MagicMock:
        mock_cfg = mock_cfg_fn.return_value
        mock_cfg.db_path = db_path
        mock_cfg.vault_socket_path = sock_path
        mock_cfg.token_broker_port = 18731
        mock_cfg.ssh_keys_json_path = ssh_keys_path
        return mock_cfg

    return SimpleNamespace(
        tmp_path=tmp_path,
        db_path=db_path,
        sock_path=sock_path,
        project=project,
        setup_mock_cfg=setup_mock_cfg,
    )


class TestVaultEnvIntegration:
    """Verify _vault_env_and_volumes with real DB."""

    def test_phantom_tokens_injected_for_stored_provider(self, vault_env: SimpleNamespace) -> None:
        """Stored API key credentials produce phantom env vars (direct transport)."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _vault_env_and_volumes

        db = CredentialDB(vault_env.db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk-test"})
        db.store_credential("default", "vibe", {"type": "api_key", "key": "vibe-key"})
        db.close()

        with (
            patch(
                "terok_sandbox.credentials.lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_vault_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_vault_transport", return_value="direct"),
        ):
            vault_env.setup_mock_cfg(mock_cfg_fn)
            env, volumes = _vault_env_and_volumes(vault_env.project, "task-1")

        # Claude phantom token
        assert "ANTHROPIC_API_KEY" in env
        assert env["ANTHROPIC_API_KEY"].startswith("terok-p-")
        # Claude base URL override (TCP transport via host.containers.internal)
        assert "ANTHROPIC_BASE_URL" in env
        assert "host.containers.internal" in env["ANTHROPIC_BASE_URL"]
        # Vibe phantom token (per-provider — distinct from Claude's)
        assert "MISTRAL_API_KEY" in env
        assert env["MISTRAL_API_KEY"].startswith("terok-p-")
        # TCP transport — no socket volume mounts
        assert volumes == []

    def test_unstored_providers_excluded(self, vault_env: SimpleNamespace) -> None:
        """Providers without stored credentials get no phantom tokens."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _vault_env_and_volumes

        db = CredentialDB(vault_env.db_path)
        db.store_credential("default", "vibe", {"type": "api_key", "key": "k"})
        db.close()

        with (
            patch(
                "terok_sandbox.credentials.lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_vault_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_vault_transport", return_value="direct"),
        ):
            vault_env.setup_mock_cfg(mock_cfg_fn)
            env, _ = _vault_env_and_volumes(vault_env.project, "task-1")

        assert "MISTRAL_API_KEY" in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "OPENAI_API_KEY" not in env

    def test_vault_not_running_raises(self, vault_env: SimpleNamespace) -> None:
        """SystemExit when vault is not running and bypass is off."""
        from terok.lib.orchestration.environment import _vault_env_and_volumes

        with (
            patch("terok_sandbox.credentials.lifecycle.is_socket_active", return_value=False),
            patch(
                "terok_sandbox.credentials.lifecycle.is_daemon_running",
                return_value=False,
            ),
            pytest.raises(SystemExit, match="not reachable"),
        ):
            _vault_env_and_volumes(vault_env.project, "task-1")

    def test_phantom_token_is_unique_per_task(self, vault_env: SimpleNamespace) -> None:
        """Each task gets a distinct phantom token."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _vault_env_and_volumes

        db = CredentialDB(vault_env.db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk"})
        db.close()
        vault_env.project.id = "proj"

        tokens = []
        for task_id in ("task-1", "task-2"):
            with (
                patch(
                    "terok_sandbox.credentials.lifecycle.is_daemon_running",
                    return_value=True,
                ),
                patch("terok_sandbox.ensure_vault_reachable"),
                patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
                patch(
                    "terok.lib.core.config.get_vault_transport",
                    return_value="direct",
                ),
            ):
                vault_env.setup_mock_cfg(mock_cfg_fn)
                env, _ = _vault_env_and_volumes(vault_env.project, task_id)
                tokens.append(env["ANTHROPIC_API_KEY"])

        assert tokens[0] != tokens[1]
        assert tokens[0].startswith("terok-p-")

    def test_oauth_credential_uses_base_url_only(self, vault_env: SimpleNamespace) -> None:
        """Claude OAuth skips phantom token env vars; only ANTHROPIC_BASE_URL is set.

        Claude Code determines subscription tier from .credentials.json, not env
        vars — so the proxy injects a static marker file instead of a phantom token.
        """
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _vault_env_and_volumes

        db = CredentialDB(vault_env.db_path)
        db.store_credential("default", "claude", {"type": "oauth", "access_token": "tok"})
        db.close()

        with (
            patch(
                "terok_sandbox.credentials.lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_vault_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_vault_transport", return_value="socket"),
        ):
            vault_env.setup_mock_cfg(mock_cfg_fn)
            env, _ = _vault_env_and_volumes(vault_env.project, "task-1")

        # Claude OAuth: no phantom token env vars — marker file handles auth
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env
        # Base URL is still set so the SDK routes through the proxy
        assert "ANTHROPIC_BASE_URL" in env

    def test_oauth_credential_direct_transport(self, vault_env: SimpleNamespace) -> None:
        """OAuth + direct transport → ANTHROPIC_BASE_URL only (no phantom token)."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _vault_env_and_volumes

        db = CredentialDB(vault_env.db_path)
        db.store_credential("default", "claude", {"type": "oauth", "access_token": "tok"})
        db.close()

        with (
            patch(
                "terok_sandbox.credentials.lifecycle.is_daemon_running",
                return_value=True,
            ),
            patch("terok_sandbox.ensure_vault_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_vault_transport", return_value="direct"),
        ):
            vault_env.setup_mock_cfg(mock_cfg_fn)
            env, _ = _vault_env_and_volumes(vault_env.project, "task-1")

        # Claude OAuth: no phantom token env vars — marker file handles auth
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env


class TestVaultBypassConfig:
    """Verify the bypass flag skips vault entirely."""

    def test_bypass_returns_empty(self) -> None:
        """When bypass is set, no vault interaction occurs."""
        from terok.lib.orchestration.environment import _vault_env_and_volumes

        project = MagicMock()
        with patch(
            "terok.lib.core.config.get_vault_bypass",
            return_value=True,
        ):
            env, volumes = _vault_env_and_volumes(project, "task-1")

        assert env == {}
        assert volumes == []
