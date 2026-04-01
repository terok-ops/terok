# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok.lib.core.paths — platform-aware path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from terok.lib.core import paths
from tests.testfs import MOCK_BASE


class TestCredentialsRoot:
    """Verify ``credentials_root()`` resolution across all priority tiers."""

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """TEROK_CREDENTIALS_DIR takes first priority."""
        target = tmp_path / "my-creds"
        monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(target))
        assert paths.credentials_root() == target

    def test_env_override_with_tilde(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tilde in TEROK_CREDENTIALS_DIR is expanded."""
        monkeypatch.setenv("TEROK_CREDENTIALS_DIR", "~/my-creds")
        result = paths.credentials_root()
        assert "~" not in str(result)
        assert result == Path.home() / "my-creds"

    def test_root_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Root user gets /var/lib/terok-credentials."""
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: True)
        assert paths.credentials_root() == Path("/var/lib/terok-credentials")

    def test_platformdirs_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root with platformdirs delegates to user_data_dir."""
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", lambda name: f"{MOCK_BASE}/data/{name}")
        assert paths.credentials_root() == Path(f"{MOCK_BASE}/data/terok-credentials")

    def test_xdg_data_home_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without platformdirs, XDG_DATA_HOME is honored."""
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", None)
        monkeypatch.setenv("XDG_DATA_HOME", str(MOCK_BASE / "xdg-data"))
        assert paths.credentials_root() == MOCK_BASE / "xdg-data" / "terok-credentials"

    def test_bare_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Last resort: ~/.local/share/terok-credentials."""
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", None)
        assert paths.credentials_root() == Path.home() / ".local" / "share" / "terok-credentials"


class TestCredentialsAppName:
    """Verify the credentials namespace constant."""

    def test_value(self) -> None:
        """CREDENTIALS_APP_NAME is separate from APP_NAME."""
        assert paths.CREDENTIALS_APP_NAME == "terok-credentials"
        assert paths.CREDENTIALS_APP_NAME != paths.APP_NAME


class TestConfigRoot:
    """Verify ``config_root()`` resolution."""

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """TEROK_CONFIG_DIR takes first priority."""
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path))
        assert paths.config_root() == tmp_path

    def test_root_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Root user gets /etc/terok."""
        monkeypatch.delenv("TEROK_CONFIG_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: True)
        assert paths.config_root() == Path("/etc/terok")

    def test_platformdirs_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root with platformdirs delegates to user_config_dir."""
        monkeypatch.delenv("TEROK_CONFIG_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_config_dir", lambda name: f"{MOCK_BASE}/config/{name}")
        assert paths.config_root() == Path(f"{MOCK_BASE}/config/terok")

    def test_bare_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Last resort: ~/.config/terok."""
        monkeypatch.delenv("TEROK_CONFIG_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_config_dir", None)
        assert paths.config_root() == Path.home() / ".config" / "terok"


class TestStateRoot:
    """Verify ``state_root()`` resolution."""

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """TEROK_STATE_DIR takes first priority."""
        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path))
        assert paths.state_root() == tmp_path

    def test_root_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Root user gets /var/lib/terok."""
        monkeypatch.delenv("TEROK_STATE_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: True)
        assert paths.state_root() == Path("/var/lib/terok")

    def test_xdg_data_home_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without platformdirs, XDG_DATA_HOME is honored."""
        monkeypatch.delenv("TEROK_STATE_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", None)
        monkeypatch.setenv("XDG_DATA_HOME", str(MOCK_BASE / "xdg-data"))
        assert paths.state_root() == MOCK_BASE / "xdg-data" / "terok"

    def test_bare_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Last resort: ~/.local/share/terok."""
        monkeypatch.delenv("TEROK_STATE_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", None)
        assert paths.state_root() == Path.home() / ".local" / "share" / "terok"


class TestRuntimeRoot:
    """Verify ``runtime_root()`` resolution."""

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """TEROK_RUNTIME_DIR takes first priority."""
        monkeypatch.setenv("TEROK_RUNTIME_DIR", str(tmp_path))
        assert paths.runtime_root() == tmp_path

    def test_root_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Root user gets /run/terok."""
        monkeypatch.delenv("TEROK_RUNTIME_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: True)
        assert paths.runtime_root() == Path("/run/terok")

    def test_user_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root fallback: ~/.cache/terok."""
        monkeypatch.delenv("TEROK_RUNTIME_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        assert paths.runtime_root() == Path.home() / ".cache" / "terok"
