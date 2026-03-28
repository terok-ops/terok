# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for credential proxy environment integration.

These tests exercise the proxy-enabled path by overriding the autouse
bypass fixture and mocking proxy/DB dependencies directly.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest import CaptureFixture


@pytest.fixture()
def _enable_proxy():
    """Override the autouse bypass to test the proxy-enabled path."""
    with patch(
        "terok.lib.core.config.get_credential_proxy_bypass",
        return_value=False,
    ):
        yield


class TestCredentialProxyEnv:
    """Verify _credential_proxy_env_and_volumes."""

    def test_bypass_returns_empty(self) -> None:
        """When bypass is set, returns empty env and volumes."""
        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        project = MagicMock()
        # The autouse fixture sets bypass=True, so this should return empty
        env, volumes = _credential_proxy_env_and_volumes(project, "task-1")
        assert env == {}
        assert volumes == []

    @pytest.mark.usefixtures("_enable_proxy")
    def test_proxy_not_running_raises(self) -> None:
        """When proxy is not running and bypass is off, raises SystemExit."""
        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        project = MagicMock()
        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=False),
            pytest.raises(SystemExit, match="not running"),
        ):
            _credential_proxy_env_and_volumes(project, "task-1")

    @pytest.mark.usefixtures("_enable_proxy")
    def test_proxy_running_injects_phantom_tokens(self, tmp_path: Path) -> None:
        """When proxy runs and credentials exist, injects phantom env vars."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        # Set up a real DB with a credential
        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk-test"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()

        project = MagicMock()
        project.id = "test-project"

        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
            patch("terok_sandbox.SandboxConfig") as mock_cfg_cls,
        ):
            mock_cfg = mock_cfg_cls.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path
            mock_cfg.proxy_port = 18731

            env, volumes = _credential_proxy_env_and_volumes(project, "task-1")

        # Phantom token should be injected for Claude
        assert "ANTHROPIC_API_KEY" in env
        assert len(env["ANTHROPIC_API_KEY"]) == 32  # hex token
        # Base URL override — TCP via host.containers.internal
        assert "ANTHROPIC_BASE_URL" in env
        assert "host.containers.internal:18731" in env["ANTHROPIC_BASE_URL"]
        # No socket mount (TCP transport)
        assert volumes == []

    @pytest.mark.usefixtures("_enable_proxy")
    def test_only_stored_providers_get_phantom_tokens(self, tmp_path: Path) -> None:
        """Providers without stored credentials don't get phantom tokens."""
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
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
            patch("terok_sandbox.SandboxConfig") as mock_cfg_cls,
        ):
            mock_cfg = mock_cfg_cls.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path
            mock_cfg.proxy_port = 18731

            env, _volumes = _credential_proxy_env_and_volumes(project, "task-1")

        # Vibe credential stored → phantom token injected
        assert "MISTRAL_API_KEY" in env
        # Claude NOT stored → no phantom token
        assert "ANTHROPIC_API_KEY" not in env

    @pytest.mark.usefixtures("_enable_proxy")
    def test_leaked_credentials_warning(self, tmp_path: Path, capsys: CaptureFixture[str]) -> None:
        """Leaked credential files in shared mounts trigger a stderr warning."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk-test"})
        db.close()

        # Create a leaked credential file in the shared mount
        from terok_agent import get_registry

        registry = get_registry()
        auth = registry.auth_providers["claude"]
        route = registry.proxy_routes["claude"]
        cred_dir = tmp_path / "envs" / auth.host_dir_name
        cred_dir.mkdir(parents=True)
        (cred_dir / route.credential_file).write_text('{"leaked": true}')

        project = MagicMock()
        project.id = "test-project"

        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
            patch("terok_sandbox.SandboxConfig") as mock_cfg_cls,
        ):
            mock_cfg = mock_cfg_cls.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = tmp_path / "proxy.sock"
            mock_cfg.proxy_port = 18731
            mock_cfg.effective_envs_dir = tmp_path / "envs"

            _credential_proxy_env_and_volumes(project, "task-1")

        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "claude" in err
