# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for task status/mode display helpers and batch state lookup."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from terok.lib.containers.runtime import get_project_container_states
from terok.lib.containers.task_display import (
    STATUS_DISPLAY,
    effective_status,
    mode_info,
)
from terok.lib.containers.tasks import TaskMeta, get_all_task_states


def _task(**kwargs: object) -> TaskMeta:
    """Build a ``TaskMeta`` with sensible defaults overridden by *kwargs*."""
    defaults = {
        "task_id": "1",
        "mode": None,
        "workspace": "",
        "web_port": None,
    }
    defaults.update(kwargs)
    return TaskMeta(**defaults)


EFFECTIVE_STATUS_CASES = [
    ({"container_state": "running", "mode": "cli"}, "running"),
    ({"container_state": "running", "mode": "run", "exit_code": 0}, "running"),
    ({"container_state": "exited", "mode": "cli", "exit_code": None}, "stopped"),
    ({"container_state": "exited", "mode": "run", "exit_code": 0}, "completed"),
    ({"container_state": "exited", "mode": "run", "exit_code": 1}, "failed"),
    ({"container_state": None, "mode": None}, "created"),
    ({"container_state": None, "mode": "cli", "exit_code": None}, "not found"),
    ({"container_state": None, "mode": "run", "exit_code": 0}, "completed"),
    ({"container_state": None, "mode": "run", "exit_code": 2}, "failed"),
    ({"container_state": "running", "mode": "cli", "deleting": True}, "deleting"),
    ({"container_state": "running", "mode": "cli", "deleting": False}, "running"),
    ({}, "created"),
]

MODE_INFO_CASES = [
    ({"mode": "cli"}, "💻", "CLI"),
    ({"mode": "run"}, "🚀", "Autopilot"),
    ({"mode": None}, "🦗", ""),
]


@pytest.mark.parametrize(
    ("task_kwargs", "expected"),
    EFFECTIVE_STATUS_CASES,
    ids=[
        "running",
        "running-beats-exit-code",
        "exited-no-exit-code",
        "exited-success",
        "exited-failure",
        "no-container-no-mode",
        "no-container-mode-set",
        "no-container-success",
        "no-container-failure",
        "deleting-overrides-all",
        "deleting-false-ignored",
        "minimal-defaults",
    ],
)
def test_effective_status_cases(task_kwargs: dict[str, object], expected: str) -> None:
    """``effective_status`` handles the supported task/container combinations."""
    assert effective_status(_task(**task_kwargs)) == expected


def test_all_effective_status_values_have_display_info() -> None:
    """Every effective status returned by the tested cases has display metadata."""
    assert {expected for _, expected in EFFECTIVE_STATUS_CASES} <= set(STATUS_DISPLAY)


@pytest.mark.parametrize(
    ("task_kwargs", "emoji", "label"),
    MODE_INFO_CASES,
    ids=[
        "cli",
        "run",
        "unset",
    ],
)
def test_mode_info_cases(task_kwargs: dict[str, object], emoji: str, label: str) -> None:
    """``mode_info`` resolves direct modes and web backends into display metadata."""
    info = mode_info(_task(**task_kwargs))
    assert info.emoji == emoji
    assert info.label == label


@pytest.mark.parametrize(
    ("output", "error", "expected"),
    [
        pytest.param(
            "proj-cli-1 running\nproj-web-2 exited\nproj-run-3 stopped\n",
            None,
            {
                "proj-cli-1": "running",
                "proj-web-2": "exited",
                "proj-run-3": "stopped",
            },
            id="parsed-output",
        ),
        pytest.param(
            "proj-cli-1 Running\nproj-web-2 Exited\n",
            None,
            {"proj-cli-1": "running", "proj-web-2": "exited"},
            id="parsed-output-normalizes-case",
        ),
        pytest.param("", None, {}, id="empty-output"),
        pytest.param(None, FileNotFoundError(), {}, id="podman-missing"),
        pytest.param(
            None,
            subprocess.CalledProcessError(1, "podman"),
            {},
            id="podman-error",
        ),
    ],
)
def test_get_project_container_states_handles_output_and_errors(
    output: str | None,
    error: Exception | None,
    expected: dict[str, str],
) -> None:
    """Project-wide state lookup parses output and degrades cleanly on errors."""
    patch_kwargs = {"side_effect": error} if error else {"return_value": output}
    with patch("terok.lib.containers.runtime.subprocess.check_output", **patch_kwargs):
        assert get_project_container_states("proj") == expected


@pytest.mark.parametrize(
    ("tasks", "container_states", "expected"),
    [
        pytest.param(
            [
                _task(task_id="1", mode="cli"),
                _task(task_id="2", mode="web"),
                _task(task_id="3", mode=None),
            ],
            {"proj-cli-1": "running", "proj-web-2": "exited"},
            {"1": "running", "2": "exited", "3": None},
            id="mixed-task-modes",
        ),
        pytest.param(
            [_task(task_id="1", mode="cli")],
            {},
            {"1": None},
            id="missing-container",
        ),
    ],
)
def test_get_all_task_states_maps_project_container_lookup(
    tasks: list[TaskMeta],
    container_states: dict[str, str],
    expected: dict[str, str | None],
) -> None:
    """Task-state lookup maps batch project container states back to task IDs."""
    with patch(
        "terok.lib.containers.tasks.get_project_container_states",
        return_value=container_states,
    ) as mocked_get_states:
        assert get_all_task_states("proj", tasks) == expected
    mocked_get_states.assert_called_once_with("proj")
