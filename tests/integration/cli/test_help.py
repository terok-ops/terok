# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for CLI help and command discovery."""

from __future__ import annotations

import pytest

from ..helpers import TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features


class TestCliHelp:
    """Verify the real CLI parser and help output."""

    def test_root_help_lists_core_workflows(self, terok_env: TerokIntegrationEnv) -> None:
        """``terokctl --help`` shows the main workflow entry points."""
        result = terok_env.run_cli("--help")
        assert "Quick start:" in result.stdout
        assert "projects" in result.stdout
        assert "task" in result.stdout
        assert "project-derive" in result.stdout

    def test_task_help_lists_management_commands(self, terok_env: TerokIntegrationEnv) -> None:
        """``terokctl task --help`` shows task lifecycle subcommands."""
        result = terok_env.run_cli("task", "--help")
        for command in ("new", "list", "rename", "status", "archive"):
            assert command in result.stdout
