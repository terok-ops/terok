# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for container lifecycle helpers: state, stop, restart, and status."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from terok_sandbox import get_container_state

from terok.lib.orchestration.task_runners import task_restart
from terok.lib.orchestration.tasks import get_task_container_state, task_new, task_status, task_stop
from terok.lib.util.yaml import dump as yaml_dump, load as yaml_load
from tests.test_utils import mock_git_config, project_env


def project_config(project_id: str, *, shutdown_timeout: int | None = None) -> str:
    """Build a minimal project config, optionally overriding shutdown timeout."""
    lines = [f"project:\n  id: {project_id}"]
    if shutdown_timeout is not None:
        lines.append(f"run:\n  shutdown_timeout: {shutdown_timeout}")
    return "\n".join(lines) + "\n"


def task_meta_path(ctx: SimpleNamespace, project_id: str, task_id: str) -> Path:
    """Return the metadata path for *task_id* inside the temporary project env."""
    return ctx.state_dir / "projects" / project_id / "tasks" / f"{task_id}.yml"


def update_task_meta(
    ctx: SimpleNamespace, project_id: str, task_id: str, **changes: object
) -> None:
    """Patch selected metadata keys for a generated task."""
    meta_path = task_meta_path(ctx, project_id, task_id)
    meta = yaml_load(meta_path.read_text()) or {}
    meta.update(changes)
    meta_path.write_text(yaml_dump(meta), encoding="utf-8")


def create_task_with_mode(ctx: SimpleNamespace, project_id: str, *, mode: str = "cli") -> str:
    """Create a new task and persist the requested mode in its metadata."""
    task_id = task_new(project_id)
    update_task_meta(ctx, project_id, task_id, mode=mode)
    return task_id


def completed_process() -> subprocess.CompletedProcess[None]:
    """Return a successful ``subprocess.run`` result."""
    return subprocess.CompletedProcess(args=[], returncode=0)


def capture_stdout(func: Callable[..., object], /, *args: object, **kwargs: object) -> str:
    """Run *func* and return its captured stdout."""
    output = StringIO()
    with redirect_stdout(output):
        func(*args, **kwargs)
    return output.getvalue()


def run_podman_args(run_mock: Mock, *, call_index: int = 0) -> list[str]:
    """Return the Podman argv for a mocked ``subprocess.run`` invocation."""
    return run_mock.call_args_list[call_index].args[0]


@pytest.mark.parametrize(
    ("output", "error", "expected"),
    [
        pytest.param("running\n", None, "running", id="running"),
        pytest.param("exited\n", None, "exited", id="exited"),
        pytest.param(None, subprocess.CalledProcessError(1, "podman"), None, id="not-found"),
        pytest.param(None, FileNotFoundError("podman"), None, id="podman-missing"),
    ],
)
def test_get_container_state_handles_success_and_errors(
    output: str | None,
    error: Exception | None,
    expected: str | None,
) -> None:
    """Container state lookup lowercases successful output and ignores Podman errors."""
    patch_kwargs = {"side_effect": error} if error else {"return_value": output}
    with patch("terok_sandbox.runtime.subprocess.check_output", **patch_kwargs):
        assert get_container_state("test-container") == expected


@pytest.mark.parametrize(
    ("project_id", "shutdown_timeout", "timeout_override", "expected_timeout"),
    [
        pytest.param("proj_stop", None, None, "10", id="default-timeout"),
        pytest.param("proj_stop_cfg", 30, None, "30", id="config-timeout"),
        pytest.param("proj_stop_ovr", 30, 60, "60", id="cli-timeout-override"),
    ],
)
def test_task_stop_uses_expected_timeout(
    project_id: str,
    shutdown_timeout: int | None,
    timeout_override: int | None,
    expected_timeout: str,
) -> None:
    """Stopping a task uses the default, configured, or explicit timeout."""
    with project_env(
        project_config(project_id, shutdown_timeout=shutdown_timeout),
        project_id=project_id,
    ) as ctx:
        task_id = create_task_with_mode(ctx, project_id)

        with (
            mock_git_config(),
            patch("terok.lib.orchestration.tasks.get_container_state", return_value="running"),
            patch("terok.lib.orchestration.tasks.subprocess.run") as run_mock,
        ):
            run_mock.return_value = completed_process()
            capture_stdout(
                task_stop,
                project_id,
                task_id,
                **({"timeout": timeout_override} if timeout_override is not None else {}),
            )

        assert run_podman_args(run_mock) == [
            "podman",
            "stop",
            "--time",
            expected_timeout,
            f"{project_id}-cli-{task_id}",
        ]


def test_task_stop_unknown_task_raises_system_exit() -> None:
    """Stopping a missing task raises a user-facing ``SystemExit``."""
    project_id = "proj_stop_missing"
    with project_env(project_config(project_id), project_id=project_id):
        with mock_git_config(), pytest.raises(SystemExit, match="Unknown task"):
            task_stop(project_id, "999")


def test_task_restart_starts_exited_container() -> None:
    """Restarting an exited task uses ``podman start``."""
    project_id = "proj_restart"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)
        container_name = f"{project_id}-cli-{task_id}"

        with (
            mock_git_config(),
            patch(
                "terok.lib.orchestration.task_runners.get_container_state",
                side_effect=["exited", "running"],
            ),
            patch("terok.lib.orchestration.task_runners.subprocess.run") as run_mock,
        ):
            run_mock.return_value = completed_process()
            capture_stdout(task_restart, project_id, task_id)

        assert run_podman_args(run_mock) == ["podman", "start", container_name]


def test_task_restart_running_container_stops_then_starts() -> None:
    """Restarting a running task stops it first and then starts it again."""
    project_id = "proj_restart_running"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)
        container_name = f"{project_id}-cli-{task_id}"

        with (
            mock_git_config(),
            patch(
                "terok.lib.orchestration.task_runners.get_container_state",
                side_effect=["running", "running"],
            ),
            patch("terok.lib.orchestration.task_runners.subprocess.run") as run_mock,
        ):
            run_mock.return_value = completed_process()
            output = capture_stdout(task_restart, project_id, task_id)

        assert run_podman_args(run_mock, call_index=0) == [
            "podman",
            "stop",
            "--time",
            "10",
            container_name,
        ]
        assert run_podman_args(run_mock, call_index=1) == ["podman", "start", container_name]
        assert "Restarted" in output


def test_task_status_reports_live_container_state() -> None:
    """Task status shows both live container state and derived effective status."""
    project_id = "proj_status"
    with project_env(project_config(project_id), project_id=project_id) as ctx:
        task_id = create_task_with_mode(ctx, project_id)

        with (
            mock_git_config(),
            patch("terok.lib.orchestration.tasks.get_container_state", return_value="exited"),
        ):
            output = capture_stdout(task_status, project_id, task_id)

    assert "exited" in output
    assert "stopped" in output


def test_get_task_container_state_returns_none_without_mode() -> None:
    """Task container lookup is skipped when no mode is configured."""
    assert get_task_container_state("proj", "1", None) is None


def test_get_task_container_state_uses_project_id_and_mode() -> None:
    """Task container lookup resolves the canonical container name."""
    with patch(
        "terok.lib.orchestration.tasks.get_container_state", return_value="running"
    ) as mock_state:
        assert get_task_container_state("proj", "1", "cli") == "running"
        mock_state.assert_called_once_with("proj-cli-1")
