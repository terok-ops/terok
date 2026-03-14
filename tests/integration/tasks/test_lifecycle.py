# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for task metadata and archive workflows."""

from __future__ import annotations

import pytest

from constants import EXAMPLE_UPSTREAM_URL

from ..helpers import NEW_TASK_MARKER, TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features

PROJECT_CONFIG = f"""
project:
  id: demo
  security_class: online
git:
  upstream_url: {EXAMPLE_UPSTREAM_URL}
"""


class TestTaskLifecycle:
    """Verify task creation, status, rename, and archive flows."""

    def test_task_new_creates_workspace_and_lists_tasks(
        self, terok_env: TerokIntegrationEnv
    ) -> None:
        """``task new`` writes the workspace layout used by later container runs."""
        terok_env.write_project("demo", PROJECT_CONFIG)

        created = terok_env.run_cli("task", "new", "demo", "--name", "Fix Login Bug")
        terok_env.run_cli("task", "new", "demo", "--name", "Docs Sweep")

        workspace = terok_env.task_workspace("demo", "1")
        assert "Created task 1 (fix-login-bug)" in created.stdout
        assert workspace.is_dir()
        assert (workspace / NEW_TASK_MARKER).is_file()
        assert (terok_env.task_dir("demo", "1") / "README.md").is_file()

        listed = terok_env.run_cli("task", "list", "demo")
        assert "fix-login-bug created" in listed.stdout
        assert "docs-sweep created" in listed.stdout

    def test_task_rename_status_and_archive_delete(self, terok_env: TerokIntegrationEnv) -> None:
        """A task can be renamed, inspected, deleted, and listed from the archive."""
        terok_env.write_project("demo", PROJECT_CONFIG)
        terok_env.run_cli("task", "new", "demo", "--name", "Draft")

        renamed = terok_env.run_cli("task", "rename", "demo", "1", "Ship It")
        assert "Renamed task 1 to ship-it" in renamed.stdout

        status = terok_env.run_cli("task", "status", "demo", "1")
        assert "Task 1:" in status.stdout
        assert "Name:            ship-it" in status.stdout
        assert "[created]" in status.stdout
        assert "Mode:" in status.stdout
        assert "not set" in status.stdout

        terok_env.run_cli("task", "delete", "demo", "1")
        assert not terok_env.task_meta_path("demo", "1").exists()
        assert not terok_env.task_dir("demo", "1").exists()

        archive_root = terok_env.task_archive_root("demo")
        archived_entries = list(archive_root.iterdir())
        assert archived_entries, "Expected task delete to create an archive entry"
        assert (archived_entries[0] / "task.yml").is_file()

        archived = terok_env.run_cli("task", "archive", "list", "demo")
        assert "#1: ship-it" in archived.stdout
