# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ensure_credential_proxy() — the reattach-path proxy startup."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terok.lib.orchestration.environment import ensure_credential_proxy

_CFG = "terok.lib.core.config"
_ENV = "terok.lib.orchestration.environment"


class TestEnsureCredentialProxy:
    """Verify ensure_credential_proxy respects bypass and delegates correctly."""

    def test_noop_when_bypass_enabled(self) -> None:
        """Does nothing when bypass_no_secret_protection is set."""
        with (
            patch(f"{_CFG}.get_credential_proxy_bypass", return_value=True),
            patch("terok_sandbox.ensure_proxy_reachable") as mock_reach,
        ):
            ensure_credential_proxy()
        mock_reach.assert_not_called()

    def test_calls_ensure_proxy_reachable(self) -> None:
        """Delegates to sandbox ensure_proxy_reachable with correct config."""
        mock_cfg = MagicMock()
        with (
            patch(f"{_CFG}.get_credential_proxy_bypass", return_value=False),
            patch(f"{_ENV}.make_sandbox_config", return_value=mock_cfg),
            patch("terok_sandbox.ensure_proxy_reachable") as mock_reach,
        ):
            ensure_credential_proxy()
        mock_reach.assert_called_once_with(mock_cfg)

    def test_propagates_system_exit(self) -> None:
        """SystemExit from ensure_proxy_reachable propagates to caller."""
        with (
            patch(f"{_CFG}.get_credential_proxy_bypass", return_value=False),
            patch(f"{_ENV}.make_sandbox_config"),
            patch(
                "terok_sandbox.ensure_proxy_reachable",
                side_effect=SystemExit("proxy down"),
            ),
        ):
            with pytest.raises(SystemExit, match="proxy down"):
                ensure_credential_proxy()
