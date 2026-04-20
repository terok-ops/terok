# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for task-login helpers."""

from __future__ import annotations

import types
import unittest.mock

import pytest

from terok.lib.orchestration.tasks import get_login_command, task_login, task_new
from terok.lib.util.yaml import dump as yaml_dump, load as yaml_load
from tests.test_utils import mock_git_config, project_env


def project_yaml(project_id: str, extra: str = "") -> str:
    """Build a minimal project config for login tests."""
    return f"project:\n  id: {project_id}\n{extra}"


def setup_task_with_mode(
    ctx: types.SimpleNamespace,
    project_id: str,
    *,
    mode: str | None = None,
) -> str:
    """Create a task and optionally set its execution mode in metadata.

    Returns the task ID.
    """
    task_id = task_new(project_id)
    if mode:
        meta_path = ctx.state_dir / "projects" / project_id / "tasks" / f"{task_id}.yml"
        meta = yaml_load(meta_path.read_text())
        meta["mode"] = mode
        meta_path.write_text(yaml_dump(meta), encoding="utf-8")
    return task_id


def _login_command(container: str) -> list[str]:
    """Return the expected podman exec command for a given container name."""
    return [
        "podman",
        "exec",
        "-it",
        container,
        "tmux",
        "new-session",
        "-A",
        "-s",
        "main",
    ]


class TestLogin:
    """Tests for task_login, get_login_command, and validation."""

    @pytest.mark.parametrize(
        ("project_id", "mode", "container_state", "error_text"),
        [
            ("proj_login_unknown", None, None, "Unknown task"),
            ("proj_login_nomode", None, None, "never been run"),
            ("proj_login_nf", "cli", None, "does not exist"),
            ("proj_login_nr", "cli", "exited", "not running"),
        ],
        ids=["unknown-task", "no-mode", "container-missing", "container-not-running"],
    )
    def test_task_login_errors(
        self,
        project_id: str,
        mode: str | None,
        container_state: str | None,
        error_text: str,
        mock_runtime,
    ) -> None:
        mock_runtime.container.return_value.state = container_state
        with project_env(project_yaml(project_id), project_id=project_id) as ctx:
            task_id = "k3v8h"  # nonexistent by default
            if project_id != "proj_login_unknown":
                task_id = setup_task_with_mode(ctx, project_id, mode=mode)
            with pytest.raises(SystemExit) as exc_ctx:
                task_login(project_id, "k3v8h" if project_id == "proj_login_unknown" else task_id)
            assert error_text in str(exc_ctx.value)

    def test_task_login_success(self, mock_runtime) -> None:
        """task_login calls os.execvp with the correct podman+tmux command."""
        project_id = "proj-cli"
        with project_env(project_yaml(project_id), project_id=project_id) as ctx:
            task_id = setup_task_with_mode(ctx, project_id, mode="cli")
            expected_container = f"{project_id}-cli-{task_id}"
            mock_runtime.container.return_value.state = "running"
            mock_runtime.container.return_value.login_command.return_value = _login_command(
                expected_container
            )
            with unittest.mock.patch("terok.lib.orchestration.tasks.os.execvp") as mock_exec:
                task_login(project_id, task_id)
        mock_exec.assert_called_once_with("podman", _login_command(expected_container))

    @pytest.mark.parametrize(
        "mode",
        ["cli", "web"],
        ids=["cli", "web"],
    )
    def test_get_login_command_returns_expected_container_name(
        self,
        mode: str,
        mock_runtime,
    ) -> None:
        project_id = "proj_logincmd" if mode == "cli" else "proj_loginweb"
        with project_env(project_yaml(project_id), project_id=project_id) as ctx:
            task_id = setup_task_with_mode(ctx, project_id, mode=mode)
            expected_container = f"{project_id}-{mode}-{task_id}"
            mock_runtime.container.return_value.state = "running"
            mock_runtime.container.return_value.login_command.return_value = _login_command(
                expected_container
            )
            command = get_login_command(project_id, task_id)
        assert command[3] == expected_container
        assert command[-5:] == ["tmux", "new-session", "-A", "-s", "main"]

    def test_login_no_longer_injects_agent_config(self, mock_runtime) -> None:
        """get_login_command does NOT inject agent config (handled via mount)."""
        project_id = "proj_login_cfg"
        yaml_text = f"project:\n  id: {project_id}\nagent:\n  model: sonnet\n"
        with project_env(yaml_text, project_id=project_id) as ctx:
            task_id = setup_task_with_mode(ctx, project_id, mode="cli")
            expected_container = f"{project_id}-cli-{task_id}"
            mock_runtime.container.return_value.state = "running"
            mock_runtime.container.return_value.login_command.return_value = _login_command(
                expected_container
            )
            with mock_git_config():
                command = get_login_command(project_id, task_id)

        assert command[3] == expected_container
        assert "tmux" in command
