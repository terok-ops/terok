# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for task status/mode display helpers and batch state lookup."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from terok_sandbox import PodmanRuntime

from terok.lib.core.task_display import (
    STATUS_DISPLAY,
    TaskState,
    effective_status,
    mode_info,
)
from terok.lib.orchestration.tasks import TaskMeta, get_all_task_states


def _task(**kwargs: object) -> TaskMeta:
    """Build a ``TaskMeta`` with sensible defaults overridden by *kwargs*."""
    defaults: dict[str, object] = {
        "task_id": "1",
        "mode": None,
        "workspace": "",
        "web_port": None,
    }
    defaults.update(kwargs)
    defaults.setdefault("initialized", defaults["mode"] is not None)
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
    ({"container_state": None, "mode": None, "starting": True}, "starting"),
    ({"container_state": "running", "mode": None, "starting": True}, "init"),
    ({"container_state": None, "mode": None, "starting": True, "deleting": True}, "deleting"),
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
        "starting-fills-pre-container-gap",
        "starting-yields-to-init-once-container-running",
        "deleting-overrides-starting",
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
    info = mode_info(_task(**task_kwargs).mode)
    assert info.emoji == emoji
    assert info.label == label


# ── TaskState / TaskMeta inheritance tests ────────────────────────


class TestTaskStateInheritance:
    """Verify the TaskState → TaskMeta inheritance contract."""

    def test_task_meta_is_task_state(self) -> None:
        """TaskMeta instances are valid TaskState instances."""
        meta = _task(mode="cli")
        assert isinstance(meta, TaskState)

    def test_effective_status_accepts_bare_task_state(self) -> None:
        """effective_status works with a plain TaskState, not just TaskMeta."""
        state = TaskState(container_state="running", initialized=True)
        assert effective_status(state) == "running"

    def test_task_state_defaults(self) -> None:
        """TaskState fields default to 'not yet started' values."""
        state = TaskState()
        assert state.container_state is None
        assert state.exit_code is None
        assert state.deleting is False
        assert state.initialized is False
        assert effective_status(state) == "created"

    def test_initialized_controls_init_vs_running(self) -> None:
        """A running container shows 'init' until initialized is set."""
        uninit = TaskState(container_state="running", initialized=False)
        assert effective_status(uninit) == "init"

        ready = TaskState(container_state="running", initialized=True)
        assert effective_status(ready) == "running"

    def test_task_meta_status_property_delegates(self) -> None:
        """TaskMeta.status property delegates to effective_status."""
        meta = _task(mode="run", container_state="running")
        assert meta.status == effective_status(meta)

    def test_mode_info_with_unknown_mode(self) -> None:
        """Unknown mode strings fall back to the None display entry."""
        info = mode_info("nonexistent")
        assert info == mode_info(None)


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
def test_container_states_handles_output_and_errors(
    output: str | None,
    error: Exception | None,
    expected: dict[str, str],
) -> None:
    """Project-wide state lookup parses output and degrades cleanly on errors."""
    patch_kwargs = {"side_effect": error} if error else {"return_value": output}
    with patch("terok_sandbox.runtime.podman.subprocess.check_output", **patch_kwargs):
        assert PodmanRuntime().container_states("proj") == expected


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
    mock_runtime,
) -> None:
    """Task-state lookup maps batch project container states back to task IDs."""
    mock_runtime.container_states.return_value = container_states
    assert get_all_task_states("proj", tasks) == expected
    mock_runtime.container_states.assert_called_once_with("proj")
