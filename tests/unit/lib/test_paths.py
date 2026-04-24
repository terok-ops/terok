# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok.lib.core.paths — platform-aware path resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from terok.lib.core import paths
from tests.testfs import MOCK_BASE


class TestVaultRoot:
    """Verify ``vault_root()`` resolution across all priority tiers."""

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """TEROK_VAULT_DIR takes first priority."""
        target = tmp_path / "my-creds"
        monkeypatch.setenv("TEROK_VAULT_DIR", str(target))
        assert paths.vault_root() == target

    def test_env_override_with_tilde(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tilde in TEROK_VAULT_DIR is expanded."""
        monkeypatch.setenv("TEROK_VAULT_DIR", "~/my-creds")
        result = paths.vault_root()
        assert "~" not in str(result)
        assert result == Path.home() / "my-creds"

    def test_root_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Root user gets /var/lib/terok/vault."""
        monkeypatch.delenv("TEROK_VAULT_DIR", raising=False)
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: True)
        assert paths.vault_root() == Path("/var/lib/terok/vault")

    def test_platformdirs_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root with platformdirs delegates to user_data_dir."""
        monkeypatch.delenv("TEROK_VAULT_DIR", raising=False)
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", lambda name: f"{MOCK_BASE}/data/{name}")
        assert paths.vault_root() == MOCK_BASE / "data" / "terok" / "vault"

    def test_xdg_data_home_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without platformdirs, XDG_DATA_HOME is honored."""
        monkeypatch.delenv("TEROK_VAULT_DIR", raising=False)
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", None)
        monkeypatch.setenv("XDG_DATA_HOME", str(MOCK_BASE / "xdg-data"))
        assert paths.vault_root() == MOCK_BASE / "xdg-data" / "terok" / "vault"

    def test_bare_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Last resort: ~/.local/share/terok/vault."""
        monkeypatch.delenv("TEROK_VAULT_DIR", raising=False)
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", None)
        assert paths.vault_root() == Path.home() / ".local" / "share" / "terok" / "vault"


class TestVaultLegacyEnvVar:
    """Verify ``TEROK_CREDENTIALS_DIR`` is honoured as a deprecated fallback."""

    def test_legacy_env_var_returns_its_value(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without TEROK_VAULT_DIR, TEROK_CREDENTIALS_DIR is read — eases migration."""
        legacy = tmp_path / "old-credentials"
        monkeypatch.delenv("TEROK_VAULT_DIR", raising=False)
        monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(legacy))
        with pytest.warns(DeprecationWarning, match="TEROK_CREDENTIALS_DIR is deprecated"):
            result = paths.vault_root()
        assert result == legacy

    def test_legacy_env_var_expands_tilde(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tilde in the legacy env var is expanded too."""
        monkeypatch.delenv("TEROK_VAULT_DIR", raising=False)
        monkeypatch.setenv("TEROK_CREDENTIALS_DIR", "~/legacy-creds")
        with pytest.warns(DeprecationWarning):
            result = paths.vault_root()
        assert "~" not in str(result)
        assert result == Path.home() / "legacy-creds"

    def test_new_env_var_takes_precedence_over_legacy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """When both are set, TEROK_VAULT_DIR wins and no warning is emitted."""
        new_path = tmp_path / "new-vault"
        legacy = tmp_path / "old-credentials"
        monkeypatch.setenv("TEROK_VAULT_DIR", str(new_path))
        monkeypatch.setenv("TEROK_CREDENTIALS_DIR", str(legacy))
        # pytest.warns with match=None + empty list confirms no warning was raised.
        import warnings

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # any warning → raises
            result = paths.vault_root()
        assert result == new_path


class TestVaultNamespace:
    """Verify vault root lives under the terok/ namespace."""

    def test_default_is_under_namespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Default vault_root() nests under the terok/ namespace, not a sibling."""
        monkeypatch.delenv("TEROK_VAULT_DIR", raising=False)
        monkeypatch.delenv("TEROK_CREDENTIALS_DIR", raising=False)
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setattr(paths, "_is_root", lambda: False)
        monkeypatch.setattr(paths, "_user_data_dir", None)
        result = paths.vault_root()
        assert result.parent.name == "terok"
        assert result.name == "vault"


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
    """Verify ``state_root()`` delegates to the sandbox namespace resolver."""

    def test_terok_root_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """TEROK_ROOT pins the namespace root."""
        monkeypatch.setenv("TEROK_ROOT", str(tmp_path))
        monkeypatch.delenv("TEROK_CONFIG_FILE", raising=False)
        assert paths.state_root() == tmp_path.resolve()

    def test_paths_root_from_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """config.yml ``paths.root`` is honored when TEROK_ROOT is unset."""
        from terok_sandbox import paths as sandbox_paths

        sandbox_paths._config_section_cache.clear()
        monkeypatch.delenv("TEROK_ROOT", raising=False)
        custom_root = tmp_path / "custom-root"
        cfg = tmp_path / "config.yml"
        cfg.write_text(f"paths:\n  root: {custom_root}\n")
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(cfg))
        try:
            assert paths.state_root() == custom_root.resolve()
        finally:
            sandbox_paths._config_section_cache.clear()


class TestCoreStateDir:
    """Verify ``core_state_dir()`` derives from ``state_root()``."""

    def test_defaults_under_state_root(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without TEROK_STATE_DIR, core state lives at ``$state_root/core``."""
        monkeypatch.delenv("TEROK_STATE_DIR", raising=False)
        monkeypatch.setenv("TEROK_ROOT", str(tmp_path))
        monkeypatch.delenv("TEROK_CONFIG_FILE", raising=False)
        assert paths.core_state_dir() == (tmp_path / "core").resolve()

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """TEROK_STATE_DIR is the per-package escape hatch."""
        override = tmp_path / "override"
        monkeypatch.setenv("TEROK_STATE_DIR", str(override))
        monkeypatch.setenv("TEROK_ROOT", str(tmp_path / "ignored"))
        assert paths.core_state_dir() == override.resolve()


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
