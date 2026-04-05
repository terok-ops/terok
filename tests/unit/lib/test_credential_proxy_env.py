# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for credential proxy environment integration.

These tests exercise the proxy-enabled path by overriding the autouse
bypass fixture and mocking proxy/DB dependencies directly.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest import CaptureFixture


@pytest.fixture()
def _enable_proxy() -> Iterator[None]:
    """Override the autouse bypass to test the proxy-enabled path."""
    with (
        patch("terok.lib.core.config.get_credential_proxy_bypass", return_value=False),
        patch("terok_sandbox.credential_proxy_lifecycle._wait_for_ready", return_value=True),
        patch("terok_sandbox.credential_proxy_lifecycle._wait_for_tcp_port", return_value=True),
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
            patch("terok_sandbox.credential_proxy_lifecycle.is_socket_active", return_value=False),
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=False),
            pytest.raises(SystemExit, match="not reachable"),
        ):
            _credential_proxy_env_and_volumes(project, "task-1")

    @pytest.mark.usefixtures("_enable_proxy")
    def test_proxy_running_injects_phantom_tokens(self, tmp_path: Path) -> None:
        """When proxy runs and API key credentials exist, injects phantom env vars."""
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

        # Phantom token should be injected for Claude
        assert "ANTHROPIC_API_KEY" in env
        assert env["ANTHROPIC_API_KEY"].startswith("terok-p-")  # prefixed phantom token
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
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok.lib.core.config.get_credential_proxy_transport", return_value="direct"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = sock_path
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"

            env, _volumes = _credential_proxy_env_and_volumes(project, "task-1")

        # Vibe credential stored → phantom token injected
        assert "MISTRAL_API_KEY" in env
        # Claude NOT stored → no phantom token
        assert "ANTHROPIC_API_KEY" not in env

    @pytest.mark.usefixtures("_enable_proxy")
    def test_claude_oauth_only_base_url(self, tmp_path: Path) -> None:
        """Claude OAuth → only ANTHROPIC_BASE_URL (no token env vars, no socket flag)."""
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
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
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

        # Claude OAuth uses the static marker in .credentials.json — no env var token
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env
        # Only base URL for HTTP routing to the proxy
        assert "ANTHROPIC_BASE_URL" in env
        assert "host.containers.internal:18731" in env["ANTHROPIC_BASE_URL"]

    @pytest.mark.usefixtures("_enable_proxy")
    def test_non_claude_oauth_still_uses_phantom_env(self, tmp_path: Path) -> None:
        """Non-Claude OAuth provider still gets phantom token env vars."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "codex", {"type": "oauth", "access_token": "tok"})
        db.close()

        sock_path = tmp_path / "proxy.sock"
        sock_path.touch()
        project = MagicMock()
        project.id = "test-project"

        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
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

        # Codex OAuth still gets phantom token env var (static marker is Claude-only)
        assert "OPENAI_API_KEY" in env
        assert env["OPENAI_API_KEY"].startswith("terok-p-")

    @pytest.mark.usefixtures("_enable_proxy")
    def test_claude_oauth_socket_transport_still_only_base_url(self, tmp_path: Path) -> None:
        """Claude OAuth + socket transport → still only ANTHROPIC_BASE_URL (no socket flag)."""
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
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
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

        # Claude OAuth bypasses socket/token env vars even with socket transport
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env
        assert "ANTHROPIC_API_KEY" not in env
        assert "ANTHROPIC_BASE_URL" in env

    @pytest.mark.usefixtures("_enable_proxy")
    def test_api_key_socket_transport(self, tmp_path: Path) -> None:
        """API key + socket → ANTHROPIC_API_KEY + ANTHROPIC_UNIX_SOCKET + ANTHROPIC_BASE_URL."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

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

        assert "ANTHROPIC_API_KEY" in env
        assert env["ANTHROPIC_API_KEY"].startswith("terok-p-")
        assert env["ANTHROPIC_UNIX_SOCKET"] == "/tmp/terok-claude-proxy.sock"
        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        # Socket flag AND base URL — SDK needs base URL for HTTP, socket is a mode flag
        assert "ANTHROPIC_BASE_URL" in env

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
        from terok_agent import get_roster

        roster = get_roster()
        auth = roster.auth_providers["claude"]
        route = roster.proxy_routes["claude"]
        mounts_base = tmp_path / "mounts"
        cred_dir = mounts_base / auth.host_dir_name
        cred_dir.mkdir(parents=True)
        (cred_dir / route.credential_file).write_text('{"leaked": true}')

        project = MagicMock()
        project.id = "test-project"

        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
            patch("terok_agent.mounts_dir", return_value=mounts_base),
            patch("terok.lib.core.config.get_credential_proxy_transport", return_value="direct"),
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = tmp_path / "proxy.sock"
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "ssh-keys.json"

            _credential_proxy_env_and_volumes(project, "task-1")

        err = capsys.readouterr().err
        assert "WARNING" in err
        assert "claude" in err

    @pytest.mark.usefixtures("_enable_proxy")
    def test_ssh_agent_token_when_keys_registered(self, tmp_path: Path) -> None:
        """SSH agent token and port injected when project has keys in ssh-keys.json."""
        import json

        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk"})
        db.close()

        keys_json = tmp_path / "ssh-keys.json"
        keys_json.write_text(
            json.dumps({"test-project": [{"private_key": "/k/id", "public_key": "/k/id.pub"}]})
        )

        project = MagicMock()
        project.id = "test-project"

        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = tmp_path / "proxy.sock"
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = keys_json
            mock_cfg.ssh_agent_port = 18732

            env, _ = _credential_proxy_env_and_volumes(project, "task-1")

        assert "TEROK_SSH_AGENT_TOKEN" in env
        assert env["TEROK_SSH_AGENT_TOKEN"].startswith("terok-p-")
        assert env["TEROK_SSH_AGENT_PORT"] == "18732"

    @pytest.mark.usefixtures("_enable_proxy")
    def test_no_ssh_token_when_no_keys(self, tmp_path: Path) -> None:
        """No SSH agent env vars when project has no keys registered."""
        from terok_sandbox import CredentialDB

        from terok.lib.orchestration.environment import _credential_proxy_env_and_volumes

        db_path = tmp_path / "proxy" / "credentials.db"
        db = CredentialDB(db_path)
        db.store_credential("default", "claude", {"type": "api_key", "key": "sk"})
        db.close()

        project = MagicMock()
        project.id = "no-ssh-project"

        with (
            patch("terok_sandbox.credential_proxy_lifecycle.is_daemon_running", return_value=True),
            patch("terok_sandbox.ensure_proxy_reachable"),
            patch("terok.lib.orchestration.environment.make_sandbox_config") as mock_cfg_fn,
        ):
            mock_cfg = mock_cfg_fn.return_value
            mock_cfg.proxy_db_path = db_path
            mock_cfg.proxy_socket_path = tmp_path / "proxy.sock"
            mock_cfg.proxy_port = 18731
            mock_cfg.ssh_keys_json_path = tmp_path / "nonexistent.json"

            env, _ = _credential_proxy_env_and_volumes(project, "task-1")

        assert "TEROK_SSH_AGENT_TOKEN" not in env
        assert "TEROK_SSH_AGENT_PORT" not in env


class TestLoadSshKeysJson:
    """Verify _load_ssh_keys_json edge cases."""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """Non-existent file returns empty dict."""
        from terok.lib.orchestration.environment import _load_ssh_keys_json

        assert _load_ssh_keys_json(tmp_path / "nope.json") == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path) -> None:
        """Corrupt JSON returns empty dict."""
        from terok.lib.orchestration.environment import _load_ssh_keys_json

        bad = tmp_path / "bad.json"
        bad.write_text("{not valid")
        assert _load_ssh_keys_json(bad) == {}

    def test_valid_json_parsed(self, tmp_path: Path) -> None:
        """Valid JSON is parsed correctly."""
        import json

        from terok.lib.orchestration.environment import _load_ssh_keys_json

        kf = tmp_path / "keys.json"
        kf.write_text(json.dumps({"proj": {"private_key": "/a", "public_key": "/b"}}))
        assert _load_ssh_keys_json(kf) == {"proj": {"private_key": "/a", "public_key": "/b"}}

    def test_string_payload_returns_empty(self, tmp_path: Path) -> None:
        """JSON string payload (e.g. a project name) returns empty dict."""
        from terok.lib.orchestration.environment import _load_ssh_keys_json

        f = tmp_path / "keys.json"
        f.write_text('"test-project"')
        assert _load_ssh_keys_json(f) == {}

    def test_list_payload_returns_empty(self, tmp_path: Path) -> None:
        """JSON list payload returns empty dict."""
        import json

        from terok.lib.orchestration.environment import _load_ssh_keys_json

        f = tmp_path / "keys.json"
        f.write_text(json.dumps(["test-project"]))
        assert _load_ssh_keys_json(f) == {}
