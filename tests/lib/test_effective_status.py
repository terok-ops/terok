# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for effective_status(), mode_info(), and batch container state queries."""

import subprocess
import unittest
import unittest.mock

from terok.lib.containers.runtime import get_project_container_states
from terok.lib.containers.task_display import (
    STATUS_DISPLAY,
    WEB_BACKEND_DISPLAY,
    effective_status,
    mode_info,
)
from terok.lib.containers.tasks import TaskMeta, get_all_task_states


def _task(**kwargs) -> TaskMeta:
    """Build a TaskMeta with sensible defaults, overridden by *kwargs*."""
    defaults = {
        "task_id": "1",
        "mode": None,
        "workspace": "",
        "web_port": None,
    }
    defaults.update(kwargs)
    return TaskMeta(**defaults)


class EffectiveStatusTests(unittest.TestCase):
    """Test effective_status() with all input combinations."""

    def test_running_container(self) -> None:
        self.assertEqual(effective_status(_task(container_state="running", mode="cli")), "running")

    def test_running_container_with_exit_code(self) -> None:
        """Running container takes precedence over exit_code."""
        task = _task(container_state="running", mode="run", exit_code=0)
        self.assertEqual(effective_status(task), "running")

    def test_stopped_container_no_exit_code(self) -> None:
        task = _task(container_state="exited", mode="cli", exit_code=None)
        self.assertEqual(effective_status(task), "stopped")

    def test_stopped_container_exit_zero(self) -> None:
        task = _task(container_state="exited", mode="run", exit_code=0)
        self.assertEqual(effective_status(task), "completed")

    def test_stopped_container_exit_nonzero(self) -> None:
        task = _task(container_state="exited", mode="run", exit_code=1)
        self.assertEqual(effective_status(task), "failed")

    def test_no_container_no_mode(self) -> None:
        task = _task(container_state=None, mode=None)
        self.assertEqual(effective_status(task), "created")

    def test_no_container_mode_set_no_exit(self) -> None:
        task = _task(container_state=None, mode="cli", exit_code=None)
        self.assertEqual(effective_status(task), "not found")

    def test_no_container_exit_zero(self) -> None:
        """Container removed after successful run."""
        task = _task(container_state=None, mode="run", exit_code=0)
        self.assertEqual(effective_status(task), "completed")

    def test_no_container_exit_nonzero(self) -> None:
        """Container removed after failed run."""
        task = _task(container_state=None, mode="run", exit_code=2)
        self.assertEqual(effective_status(task), "failed")

    def test_deleting_overrides_everything(self) -> None:
        task = _task(container_state="running", mode="cli", deleting=True)
        self.assertEqual(effective_status(task), "deleting")

    def test_deleting_false_is_ignored(self) -> None:
        task = _task(container_state="running", mode="cli", deleting=False)
        self.assertEqual(effective_status(task), "running")

    def test_defaults_to_created(self) -> None:
        """Minimal TaskMeta with no relevant fields set."""
        self.assertEqual(effective_status(_task()), "created")

    def test_stopped_podman_state(self) -> None:
        """Podman reports 'stopped' (not 'exited') for some containers."""
        task = _task(container_state="stopped", mode="web")
        self.assertEqual(effective_status(task), "stopped")

    # -- Every status is in STATUS_DISPLAY --

    def test_all_returned_statuses_have_display_info(self) -> None:
        """Every value effective_status can return must be in STATUS_DISPLAY."""
        cases = [
            _task(container_state="running", mode="cli"),
            _task(container_state="exited", mode="cli"),
            _task(container_state="exited", mode="run", exit_code=0),
            _task(container_state="exited", mode="run", exit_code=1),
            _task(container_state=None, mode=None),
            _task(container_state=None, mode="cli"),
            _task(deleting=True),
        ]
        for task in cases:
            status = effective_status(task)
            self.assertIn(status, STATUS_DISPLAY, f"Status {status!r} not in STATUS_DISPLAY")


class ModeInfoTests(unittest.TestCase):
    """Test mode_info() for all modes and web backends."""

    def test_cli_mode(self) -> None:
        self.assertEqual(mode_info(_task(mode="cli")).emoji, "💻")

    def test_run_mode(self) -> None:
        self.assertEqual(mode_info(_task(mode="run")).emoji, "🚀")

    def test_none_mode(self) -> None:
        self.assertEqual(mode_info(_task(mode=None)).emoji, "🦗")

    def test_web_mode_claude(self) -> None:
        self.assertEqual(mode_info(_task(mode="web", backend="claude")).emoji, "💠")

    def test_web_mode_codex(self) -> None:
        self.assertEqual(mode_info(_task(mode="web", backend="codex")).emoji, "🌸")

    def test_web_mode_mistral(self) -> None:
        self.assertEqual(mode_info(_task(mode="web", backend="mistral")).emoji, "🏰")

    def test_web_mode_copilot(self) -> None:
        self.assertEqual(mode_info(_task(mode="web", backend="copilot")).emoji, "🤖")

    def test_web_mode_unknown_backend(self) -> None:
        self.assertEqual(mode_info(_task(mode="web", backend="something")).emoji, "🌍")

    def test_web_mode_no_backend(self) -> None:
        self.assertEqual(mode_info(_task(mode="web")).emoji, "🌍")

    def test_all_known_backends_covered(self) -> None:
        for backend, info in WEB_BACKEND_DISPLAY.items():
            self.assertEqual(mode_info(_task(mode="web", backend=backend)).emoji, info.emoji)

    def test_mode_info_returns_mode_info(self) -> None:
        """mode_info() returns a ModeInfo with both emoji and label."""
        m = mode_info(_task(mode="cli"))
        self.assertEqual(m.emoji, "\U0001f4bb")
        self.assertEqual(m.label, "CLI")

    def test_mode_info_web_backend(self) -> None:
        """mode_info() resolves web backends to ModeInfo with label."""
        m = mode_info(_task(mode="web", backend="claude"))
        self.assertEqual(m.emoji, "\U0001f4a0")
        self.assertEqual(m.label, "Claude")


class BatchContainerStateTests(unittest.TestCase):
    """Test get_project_container_states() and get_all_task_states()."""

    def test_get_project_container_states_parses_output(self) -> None:
        output = "proj-cli-1 running\nproj-web-2 exited\nproj-run-3 stopped\n"
        with unittest.mock.patch(
            "terok.lib.containers.runtime.subprocess.check_output",
            return_value=output,
        ):
            result = get_project_container_states("proj")
        self.assertEqual(
            result,
            {
                "proj-cli-1": "running",
                "proj-web-2": "exited",
                "proj-run-3": "stopped",
            },
        )

    def test_get_project_container_states_empty(self) -> None:
        with unittest.mock.patch(
            "terok.lib.containers.runtime.subprocess.check_output",
            return_value="",
        ):
            result = get_project_container_states("proj")
        self.assertEqual(result, {})

    def test_get_project_container_states_podman_missing(self) -> None:
        with unittest.mock.patch(
            "terok.lib.containers.runtime.subprocess.check_output",
            side_effect=FileNotFoundError,
        ):
            result = get_project_container_states("proj")
        self.assertEqual(result, {})

    def test_get_project_container_states_podman_error(self) -> None:
        with unittest.mock.patch(
            "terok.lib.containers.runtime.subprocess.check_output",
            side_effect=subprocess.CalledProcessError(1, "podman"),
        ):
            result = get_project_container_states("proj")
        self.assertEqual(result, {})

    def test_get_all_task_states_maps_correctly(self) -> None:
        tasks = [
            _task(task_id="1", mode="cli"),
            _task(task_id="2", mode="web"),
            _task(task_id="3", mode=None),
        ]
        container_states = {
            "proj-cli-1": "running",
            "proj-web-2": "exited",
        }
        with unittest.mock.patch(
            "terok.lib.containers.tasks.get_project_container_states",
            return_value=container_states,
        ):
            result = get_all_task_states("proj", tasks)
        self.assertEqual(result, {"1": "running", "2": "exited", "3": None})

    def test_get_all_task_states_missing_container(self) -> None:
        tasks = [_task(task_id="1", mode="cli")]
        with unittest.mock.patch(
            "terok.lib.containers.tasks.get_project_container_states",
            return_value={},
        ):
            result = get_all_task_states("proj", tasks)
        self.assertEqual(result, {"1": None})
