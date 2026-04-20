# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for task metadata and archive workflows."""

from __future__ import annotations

import re

import pytest

from tests.test_utils import assert_task_id
from tests.testnet import EXAMPLE_UPSTREAM_URL

from ..helpers import NEW_TASK_MARKER, TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features

PROJECT_CONFIG = f"""
project:
  id: demo
  security_class: online
git:
  upstream_url: {EXAMPLE_UPSTREAM_URL}
"""


def _extract_task_id(stdout: str) -> str:
    """Extract the task ID from 'Created task <id> ...' output."""
    match = re.search(r"Created task ([ghjkmnp-tv-z][0-9][0-9a-hjkmnp-tv-z]{3})", stdout)
    assert match, f"Could not extract task ID from: {stdout!r}"
    return match.group(1)


class TestTaskLifecycle:
    """Verify task creation, status, rename, and archive flows."""

    def test_task_new_creates_workspace_and_lists_tasks(
        self, terok_env: TerokIntegrationEnv
    ) -> None:
        """``task new`` writes the workspace layout used by later container runs."""
        terok_env.write_project("demo", PROJECT_CONFIG)

        # ``task new`` is a terokctl-only verb (scripting building block) —
        # the human-facing ``terok`` surface uses ``task run`` instead.
        created = terok_env.run_cli(
            "task", "new", "demo", "--name", "Fix Login Bug", prog="terokctl"
        )
        terok_env.run_cli("task", "new", "demo", "--name", "Docs Sweep", prog="terokctl")

        tid = _extract_task_id(created.stdout)
        assert_task_id(tid)
        workspace = terok_env.task_workspace("demo", tid)
        assert f"Created task {tid} (fix-login-bug)" in created.stdout
        assert workspace.is_dir()
        assert (workspace / NEW_TASK_MARKER).is_file()
        assert (terok_env.task_dir("demo", tid) / "README.md").is_file()

        listed = terok_env.run_cli("task", "list", "demo")
        assert "fix-login-bug created" in listed.stdout
        assert "docs-sweep created" in listed.stdout

    def test_task_rename_status_and_archive_delete(self, terok_env: TerokIntegrationEnv) -> None:
        """A task can be renamed, inspected, deleted, and listed from the archive."""
        terok_env.write_project("demo", PROJECT_CONFIG)
        created = terok_env.run_cli("task", "new", "demo", "--name", "Draft", prog="terokctl")
        tid = _extract_task_id(created.stdout)

        renamed = terok_env.run_cli("task", "rename", "demo", tid, "Ship It")
        assert f"Renamed task {tid} to ship-it" in renamed.stdout

        status = terok_env.run_cli("task", "status", "demo", tid)
        assert f"Task {tid}:" in status.stdout
        assert "Name:            ship-it" in status.stdout
        assert "[created]" in status.stdout
        assert "Mode:" in status.stdout
        assert "not set" in status.stdout

        terok_env.run_cli("task", "delete", "demo", tid)
        assert not terok_env.task_meta_path("demo", tid).exists()
        assert not terok_env.task_dir("demo", tid).exists()

        archive_root = terok_env.task_archive_root("demo")
        archived_entries = [entry for entry in archive_root.iterdir() if entry.is_dir()]
        assert archived_entries, "Expected task delete to create an archive entry"
        assert any((entry / "task.yml").is_file() for entry in archived_entries)

        archived = terok_env.run_cli("task", "archive", "list", "demo")
        assert f"#{tid}: ship-it" in archived.stdout
