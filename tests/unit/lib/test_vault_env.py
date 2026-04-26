# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok-specific vault env/policy selection.

Generic vault plumbing (phantom tokens, socket transport, SSH signer) is
tested in terok-executor (test_env_builder.py).  These tests cover the
terok-only Claude OAuth env override, shared config-patch selection, and
leaked-credential scan with exposed-token filtering.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class TestClaudeOAuthOverrides:
    """Verify _apply_claude_oauth_overrides mode selection."""

    def test_proxied_removes_token_keeps_base_url(self) -> None:
        """Claude OAuth proxied → remove phantom token, keep base URL."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {
            "CLAUDE_CODE_OAUTH_TOKEN": "terok-p-abc",
            "ANTHROPIC_BASE_URL": "http://host.containers.internal:18731",
            "ANTHROPIC_UNIX_SOCKET": "/tmp/terok-claude-proxy.sock",
            "TEROK_TOKEN_BROKER_PORT": "18731",
        }
        with patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=True):
            _apply_claude_oauth_overrides(env)

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" in env
        # Socket and proxy port are unrelated to Claude tier — untouched
        assert "ANTHROPIC_UNIX_SOCKET" in env
        assert "TEROK_TOKEN_BROKER_PORT" in env

    def test_skipped_removes_all_claude_vars(self) -> None:
        """Claude OAuth skipped (default) → remove all Claude proxy env vars."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {
            "CLAUDE_CODE_OAUTH_TOKEN": "terok-p-abc",
            "ANTHROPIC_BASE_URL": "http://host.containers.internal:18731",
            "ANTHROPIC_UNIX_SOCKET": "/tmp/terok-claude-proxy.sock",
            "TEROK_TOKEN_BROKER_PORT": "18731",
            "MISTRAL_API_KEY": "terok-p-vibe",
        }
        with patch("terok.lib.core.config.is_claude_oauth_proxied", return_value=False):
            _apply_claude_oauth_overrides(env)

        assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_UNIX_SOCKET" not in env
        # Non-Claude vars untouched
        assert "MISTRAL_API_KEY" in env
        assert "TEROK_TOKEN_BROKER_PORT" in env

    def test_noop_when_no_claude_oauth(self) -> None:
        """No-op when executor didn't inject Claude OAuth token (API key or no Claude)."""
        from terok.lib.orchestration.environment import _apply_claude_oauth_overrides

        env = {
            "ANTHROPIC_API_KEY": "terok-p-abc",
            "ANTHROPIC_BASE_URL": "http://host.containers.internal:18731",
        }
        original = dict(env)
        _apply_claude_oauth_overrides(env)
        assert env == original


class TestVaultPatchProviderSets:
    """Verify Codex shared-config patch selection from terok config."""

    def _roster(self) -> SimpleNamespace:
        return SimpleNamespace(
            vault_routes={
                "codex": SimpleNamespace(shared_config_patch={"file": "config.toml"}),
                "vibe": SimpleNamespace(shared_config_patch={"file": "config.toml"}),
                "gh": SimpleNamespace(shared_config_patch={"file": "config.yml"}),
                "claude": SimpleNamespace(shared_config_patch=None),
            }
        )

    def test_proxied_keeps_codex_patch(self) -> None:
        """Codex shared config patch is enabled only in proxied mode."""
        from terok.lib.orchestration.environment import _vault_patch_provider_sets

        with patch("terok.lib.core.config.is_codex_oauth_proxied", return_value=True):
            enabled, disabled = _vault_patch_provider_sets(self._roster())

        assert enabled == frozenset({"codex", "gh", "vibe"})
        assert disabled == frozenset()

    def test_skipped_omits_codex_patch(self) -> None:
        """Default/exposed modes disable Codex's shared config rewrite."""
        from terok.lib.orchestration.environment import _vault_patch_provider_sets

        with patch("terok.lib.core.config.is_codex_oauth_proxied", return_value=False):
            enabled, disabled = _vault_patch_provider_sets(self._roster())

        assert enabled == frozenset({"gh", "vibe"})
        assert disabled == frozenset({"codex"})

    def test_vault_bypass_disables_all_shared_patches(self) -> None:
        """Vault bypass removes stale managed config for every patch provider."""
        from terok.lib.orchestration.environment import _vault_patch_provider_sets

        enabled, disabled = _vault_patch_provider_sets(self._roster(), vault_bypass=True)

        assert enabled == frozenset()
        assert disabled == frozenset({"codex", "gh", "vibe"})


class TestLeakedCredentialsScan:
    """Verify _warn_leaked_credentials with exposed-token filtering."""

    def test_warns_for_leaked_files(self, caplog: pytest.LogCaptureFixture) -> None:
        """Leaked credential files produce log warnings."""
        from terok.lib.orchestration.environment import _warn_leaked_credentials

        with (
            caplog.at_level(logging.WARNING, logger="terok.lib.orchestration.environment"),
            patch(
                "terok.lib.orchestration.environment.sandbox_live_mounts_dir",
                return_value=Path("/tmp/terok-testing/mounts"),
            ),
            patch(
                "terok_executor.scan_leaked_credentials",
                return_value=[("claude", Path("/tmp/terok-testing/m/.credentials.json"))],
            ),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
        ):
            _warn_leaked_credentials()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("claude" in r.message for r in warnings)
        # Full path must not leak at WARNING — only at DEBUG
        assert not any(".credentials.json" in r.message for r in warnings)

    def test_exposed_token_suppresses_claude_warning(
        self, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exposed Claude OAuth token: Claude warning suppressed, other providers still warned."""
        from terok.lib.orchestration.environment import _warn_leaked_credentials

        with (
            caplog.at_level(logging.WARNING, logger="terok.lib.orchestration.environment"),
            patch(
                "terok.lib.orchestration.environment.sandbox_live_mounts_dir",
                return_value=Path("/tmp/terok-testing/mounts"),
            ),
            patch(
                "terok_executor.scan_leaked_credentials",
                return_value=[
                    ("claude", Path("/tmp/terok-testing/m/.credentials.json")),
                    ("vibe", Path("/tmp/terok-testing/m/config.toml")),
                ],
            ),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=True),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=False),
        ):
            _warn_leaked_credentials()

        # Exposed-token warning printed to stderr
        err = capsys.readouterr().err
        assert "EXPOSED" in err
        # Claude filtered out, vibe still warned via logger
        log_messages = [r.message for r in caplog.records]
        assert not any("claude" in m for m in log_messages)
        assert any("vibe" in m for m in log_messages)

    def test_exposed_codex_token_suppresses_codex_warning(
        self, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exposed Codex OAuth token: Codex warning suppressed, banner printed."""
        from terok.lib.orchestration.environment import _warn_leaked_credentials

        with (
            caplog.at_level(logging.WARNING, logger="terok.lib.orchestration.environment"),
            patch(
                "terok.lib.orchestration.environment.sandbox_live_mounts_dir",
                return_value=Path("/tmp/terok-testing/mounts"),
            ),
            patch(
                "terok_executor.scan_leaked_credentials",
                return_value=[
                    ("codex", Path("/tmp/terok-testing/m/_codex-config/auth.json")),
                    ("vibe", Path("/tmp/terok-testing/m/config.toml")),
                ],
            ),
            patch("terok.lib.core.config.is_claude_oauth_exposed", return_value=False),
            patch("terok.lib.core.config.is_codex_oauth_exposed", return_value=True),
        ):
            _warn_leaked_credentials()

        err = capsys.readouterr().err
        assert "Codex" in err and "EXPOSED" in err
        log_messages = [r.message for r in caplog.records]
        assert not any("codex" in m for m in log_messages)
        assert any("vibe" in m for m in log_messages)
