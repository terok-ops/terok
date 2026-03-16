# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the new task creation and launch workflow (#296 + #446)."""

from __future__ import annotations

import asyncio
import types
from collections.abc import Callable
from typing import Any
from unittest import mock

import pytest

from tests.unit.tui.tui_test_helpers import import_app, import_screens


def run(coro: object) -> object:
    """Run an async test coroutine."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# TaskCreateScreen
# ---------------------------------------------------------------------------


class TestTaskCreateScreen:
    """Tests for the TaskCreateScreen modal."""

    def test_construction_default_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="my-task")
        assert screen._default_name == "my-task"

    def test_construction_empty_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen()
        assert screen._default_name == ""

    def test_cancel_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="t")
        screen.dismiss = mock.Mock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(None)

    def test_button_cancel_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="t")
        screen.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "btn-cancel"
        screen.on_button_pressed(event)
        screen.dismiss.assert_called_once_with(None)

    def test_submit_validates_and_sanitizes_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="fallback")
        screen.dismiss = mock.Mock()
        screen.notify = mock.Mock()

        # Stub query_one to return mock Input with valid name
        mock_input = mock.Mock()
        mock_input.value = "  My Task  "
        screen.query_one = mock.Mock(return_value=mock_input)

        screen._submit("cli")
        screen.dismiss.assert_called_once()
        name, mode = screen.dismiss.call_args[0][0]
        assert mode == "cli"
        assert name == "my-task"  # sanitized

    def test_submit_rejects_empty_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="")
        screen.dismiss = mock.Mock()
        screen.notify = mock.Mock()

        mock_input = mock.Mock()
        mock_input.value = ""
        screen.query_one = mock.Mock(return_value=mock_input)

        screen._submit("cli")
        screen.dismiss.assert_not_called()
        screen.notify.assert_called_once()

    def test_submit_falls_back_to_default_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="fallback-name")
        screen.dismiss = mock.Mock()
        screen.notify = mock.Mock()

        mock_input = mock.Mock()
        mock_input.value = ""
        screen.query_one = mock.Mock(return_value=mock_input)

        screen._submit("toad")
        screen.dismiss.assert_called_once()
        name, mode = screen.dismiss.call_args[0][0]
        assert name == "fallback-name"
        assert mode == "toad"

    def test_option_list_selection_submits(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskCreateScreen(default_name="t")
        screen._submit = mock.Mock()

        event = mock.Mock()
        event.option_id = "autopilot"
        screen.on_option_list_option_selected(event)
        screen._submit.assert_called_once_with("autopilot")


# ---------------------------------------------------------------------------
# TaskLaunchScreen
# ---------------------------------------------------------------------------


class TestTaskLaunchScreen:
    """Tests for the TaskLaunchScreen modal."""

    def test_construction(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="terok-p-cli-1",
            project_id="p",
            task_id="1",
            task_name="fix-bug",
            default_login="claude",
        )
        assert screen._container_name == "terok-p-cli-1"
        assert screen._project_id == "p"
        assert screen._task_id == "1"
        assert screen._task_name == "fix-bug"
        assert screen._default_login == "claude"
        assert not screen._container_ready

    def test_construction_default_bash(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
        assert screen._default_login == "bash"
        assert screen._task_name == "1"  # falls back to task_id

    def test_dismiss_returns_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
        screen.dismiss = mock.Mock()
        screen.action_dismiss_screen()
        screen.dismiss.assert_called_once_with(None)

    def test_dismiss_via_button(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
        screen.dismiss = mock.Mock()
        event = mock.Mock()
        event.button = mock.Mock()
        event.button.id = "btn-dismiss"
        screen.on_button_pressed(event)
        screen.dismiss.assert_called_once_with(None)

    def test_do_login_returns_agent_and_prompt(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_id="p", task_id="1", task_name="fix-bug"
        )
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "claude"
        mock_input = mock.Mock()
        mock_input.value = "fix the bug"

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_input

        screen.query_one = query_one

        screen._do_login()
        screen.dismiss.assert_called_once_with(("p", "1", "fix-bug", "c", "claude", "fix the bug"))

    def test_do_login_bash_clears_prompt(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_id="p", task_id="1", task_name="my-task"
        )
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "bash"
        mock_input = mock.Mock()
        mock_input.value = "should be ignored"

        def query_one(selector, cls=None):
            if "login-agent" in selector:
                return mock_select
            return mock_input

        screen.query_one = query_one

        screen._do_login()
        screen.dismiss.assert_called_once_with(("p", "1", "my-task", "c", "bash", None))

    def test_login_button_blocked_when_not_ready(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
        screen._do_login = mock.Mock()

        assert not screen._container_ready

        # Simulate Enter in the prompt input — should not login
        event = mock.Mock()
        screen.on_input_submitted(event)
        screen._do_login.assert_not_called()

    def test_login_button_allowed_when_ready(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
        screen._do_login = mock.Mock()
        screen._container_ready = True

        event = mock.Mock()
        screen.on_input_submitted(event)
        screen._do_login.assert_called_once()


# ---------------------------------------------------------------------------
# _build_interactive_agent_command
# ---------------------------------------------------------------------------


class TestBuildInteractiveAgentCommand:
    """Tests for _build_interactive_agent_command helper."""

    def _import_helper(self) -> Callable[..., str]:
        """Import the helper function from the freshly loaded module."""
        _, app_class = import_app()
        return app_class._start_cli_task_background.__globals__["_build_interactive_agent_command"]

    def test_no_prompt_returns_binary(self) -> None:
        build = self._import_helper()
        provider = mock.Mock()
        provider.binary = "claude"
        provider.prompt_flag = "-p"
        assert build(provider, None) == "claude"

    def test_empty_prompt_returns_binary(self) -> None:
        build = self._import_helper()
        provider = mock.Mock()
        provider.binary = "claude"
        provider.prompt_flag = "-p"
        assert build(provider, "") == "claude"

    def test_with_prompt(self) -> None:
        build = self._import_helper()
        provider = mock.Mock()
        provider.binary = "claude"
        result = build(provider, "fix the bug")
        assert result == "claude 'fix the bug'"

    def test_simple_prompt_no_quotes(self) -> None:
        build = self._import_helper()
        provider = mock.Mock()
        provider.binary = "codex"
        result = build(provider, "hello")
        assert result == "codex hello"

    def test_prompt_with_special_chars_is_quoted(self) -> None:
        import shlex

        build = self._import_helper()
        provider = mock.Mock()
        provider.binary = "claude"
        prompt = "fix 'the' bug"
        result = build(provider, prompt)
        expected = f"claude {shlex.quote(prompt)}"
        assert result == expected


# ---------------------------------------------------------------------------
# Config: default_login
# ---------------------------------------------------------------------------


class TestDefaultLoginConfig:
    """Tests for the default_login config field."""

    def test_project_model_has_default_login(self) -> None:
        from terok.lib.core.project_model import ProjectConfig

        fields = ProjectConfig.model_fields
        assert "default_login" in fields

    def test_project_yaml_schema_has_default_login(self) -> None:
        from terok.lib.core.yaml_schema import RawProjectYaml

        fields = RawProjectYaml.model_fields
        assert "default_login" in fields

    def test_global_config_schema_has_default_login(self) -> None:
        from terok.lib.core.yaml_schema import RawGlobalConfig

        fields = RawGlobalConfig.model_fields
        assert "default_login" in fields

    def test_project_yaml_default_login_defaults_none(self) -> None:
        from terok.lib.core.yaml_schema import RawProjectYaml

        raw = RawProjectYaml()
        assert raw.default_login is None

    def test_global_config_default_login_defaults_none(self) -> None:
        from terok.lib.core.yaml_schema import RawGlobalConfig

        raw = RawGlobalConfig()
        assert raw.default_login is None


# ---------------------------------------------------------------------------
# Worker group handlers
# ---------------------------------------------------------------------------


class TestWorkerGroupHandlers:
    """Tests for cli-launch and toad-launch worker group handlers."""

    def test_cli_launch_error_notifies(self) -> None:
        app_mod, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.notify = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "cli-launch"
        worker.result = ("proj1", "5", "terok-proj1-cli-5", "container failed")
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(app_class.handle_worker_state_changed(instance, event))

        instance.notify.assert_called_once_with("CLI task failed: container failed")
        instance.refresh_tasks.assert_awaited_once()

    def test_cli_launch_success_refreshes(self) -> None:
        app_mod, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.notify = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "cli-launch"
        worker.result = ("proj1", "5", "terok-proj1-cli-5", None)
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(app_class.handle_worker_state_changed(instance, event))

        instance.notify.assert_not_called()
        instance.refresh_tasks.assert_awaited_once()

    def test_toad_launch_error_notifies(self) -> None:
        app_mod, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.notify = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "toad-launch"
        worker.result = ("proj1", "6", "terok-proj1-toad-6", "container failed")
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(app_class.handle_worker_state_changed(instance, event))

        instance.notify.assert_called_once_with("Toad task failed: container failed")
        instance.refresh_tasks.assert_awaited_once()

    def test_toad_launch_success_notifies(self) -> None:
        app_mod, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.notify = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "toad-launch"
        worker.result = ("proj1", "6", "terok-proj1-toad-6", None)
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(app_class.handle_worker_state_changed(instance, event))

        instance.notify.assert_called_once_with("Toad task 6 is running")
        instance.refresh_tasks.assert_awaited_once()

    def test_toad_launch_different_project_no_refresh(self) -> None:
        app_mod, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "other"
        instance.notify = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "toad-launch"
        worker.result = ("proj1", "6", "terok-proj1-toad-6", None)
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        run(app_class.handle_worker_state_changed(instance, event))

        instance.refresh_tasks.assert_not_awaited()


# ---------------------------------------------------------------------------
# n binding in TaskList
# ---------------------------------------------------------------------------


class TestTaskListNewBinding:
    """Tests for the n binding in the task list widget."""

    def test_task_list_has_n_binding(self) -> None:
        from tests.unit.tui.tui_test_helpers import import_widgets

        widgets = import_widgets()
        bindings = widgets.TaskList.BINDINGS
        binding_keys = [b[0] if isinstance(b, tuple) else b.key for b in bindings]
        assert "n" in binding_keys


# ---------------------------------------------------------------------------
# TaskLaunchScreen.compose border title
# ---------------------------------------------------------------------------


class TestTaskLaunchScreenCompose:
    """Test that compose() sets the expected border title with the task name."""

    @staticmethod
    def _run_compose(screens_mod: types.ModuleType, screen: Any) -> Any | None:
        """Exhaust compose() and return the Vertical dialog with border_title.

        Patches the stub Vertical's ``__enter__`` to capture the dialog
        instance that ``compose()`` uses via ``with Vertical(...) as dialog:``.
        """
        Vertical = screens_mod.Vertical
        captured: list[Any] = []
        orig_enter = Vertical.__enter__

        def tracking_enter(self: Any) -> Any:
            captured.append(self)
            return orig_enter(self)

        Vertical.__enter__ = tracking_enter
        try:
            list(screen.compose())
        finally:
            Vertical.__enter__ = orig_enter
        return captured[0] if captured else None

    def test_compose_border_title_includes_name(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_id="p", task_id="3", task_name="fix-auth"
        )
        dialog = self._run_compose(screens, screen)
        assert dialog is not None
        assert dialog.border_title == "CLI Task 3 (fix-auth)"

    def test_compose_border_title_fallback_to_id(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="7")
        dialog = self._run_compose(screens, screen)
        assert dialog is not None
        assert dialog.border_title == "CLI Task 7 (7)"


# ---------------------------------------------------------------------------
# _action_login uses _login_title
# ---------------------------------------------------------------------------


class TestActionLoginTitle:
    """Test that _action_login uses the unified _login_title format."""

    def test_action_login_passes_unified_title(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.current_task = mock.Mock()
        instance.current_task.task_id = "5"
        instance.current_task.name = "fix-login-bug"
        instance.current_task.mode = "cli"
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._action_login.__globals__

        with mock.patch.dict(
            action_globals,
            {
                "get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"]),
                "container_name": lambda *a: "proj1-cli-5",
            },
        ):
            run(app_class._action_login(instance))

        instance._launch_terminal_session.assert_awaited_once()
        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:5:fix-login-bug"
        assert call_kwargs["cname"] == "proj1-cli-5"

    def test_action_login_falls_back_to_task_id_when_unnamed(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.current_task = mock.Mock()
        instance.current_task.task_id = "8"
        instance.current_task.name = ""
        instance.current_task.mode = "run"
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._action_login.__globals__

        with mock.patch.dict(
            action_globals,
            {
                "get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"]),
                "container_name": lambda *a: "proj1-run-8",
            },
        ):
            run(app_class._action_login(instance))

        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:8:8"

    def test_action_login_no_task_notifies(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.current_task = None
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        run(app_class._action_login(instance))

        instance.notify.assert_called_once_with("No task selected.")
        instance._launch_terminal_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# _login_title helper
# ---------------------------------------------------------------------------


class TestLoginTitle:
    """Tests for the _login_title helper that unifies terminal/tmux titles."""

    def _import_helper(self) -> Callable[[str, str, str], str]:
        """Import the _login_title helper from the freshly loaded module."""
        _, app_class = import_app()
        return app_class._start_cli_task_background.__globals__["_login_title"]

    def test_basic_format(self) -> None:
        login_title = self._import_helper()
        assert login_title("myproj", "3", "fix-auth-bug") == "myproj:3:fix-auth-bug"

    def test_name_equals_id_when_unnamed(self) -> None:
        login_title = self._import_helper()
        assert login_title("proj", "7", "7") == "proj:7:7"

    @pytest.mark.parametrize(
        ("pid", "tid", "name", "expected"),
        [
            ("a", "1", "x", "a:1:x"),
            ("long-project-name", "42", "refactor-db", "long-project-name:42:refactor-db"),
        ],
        ids=["short", "long"],
    )
    def test_parametrized(self, pid: str, tid: str, name: str, expected: str) -> None:
        login_title = self._import_helper()
        assert login_title(pid, tid, name) == expected


# ---------------------------------------------------------------------------
# TaskLaunchScreen — task_name propagation
# ---------------------------------------------------------------------------


class TestTaskLaunchScreenNamePropagation:
    """Tests for task_name flowing through TaskLaunchScreen."""

    def test_empty_name_falls_back_to_task_id(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_id="p", task_id="42", task_name=""
        )
        assert screen._task_name == "42"

    def test_none_name_falls_back_to_task_id(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_id="p", task_id="5", task_name=None
        )
        assert screen._task_name == "5"

    def test_explicit_name_preserved(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="c", project_id="p", task_id="1", task_name="fix-login"
        )
        assert screen._task_name == "fix-login"

    def test_do_login_result_includes_name(self) -> None:
        """The 6-tuple dismiss result includes task_name at position 2."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(
            container_name="ctr", project_id="proj", task_id="9", task_name="deploy-fix"
        )
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "vibe"
        mock_input = mock.Mock()
        mock_input.value = "refactor auth"

        screen.query_one = lambda sel, cls=None: mock_select if "login-agent" in sel else mock_input

        screen._do_login()
        result = screen.dismiss.call_args[0][0]
        assert len(result) == 6
        assert result == ("proj", "9", "deploy-fix", "ctr", "vibe", "refactor auth")

    def test_do_login_unnamed_task_uses_id(self) -> None:
        """When no task_name given, the result falls back to task_id."""
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="11")
        screen.dismiss = mock.Mock()

        mock_select = mock.Mock()
        mock_select.value = "bash"
        mock_input = mock.Mock()
        mock_input.value = ""

        screen.query_one = lambda sel, cls=None: mock_select if "login-agent" in sel else mock_input

        screen._do_login()
        result = screen.dismiss.call_args[0][0]
        # task_name should fall back to task_id "11"
        assert result[2] == "11"


# ---------------------------------------------------------------------------
# _start_cli_task_background passes name to TaskLaunchScreen
# ---------------------------------------------------------------------------


class TestStartCliTaskBackgroundPassesName:
    """Verify _start_cli_task_background forwards the task name to TaskLaunchScreen."""

    def test_task_name_forwarded_to_launch_screen(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance._last_selected_tasks = {}
        instance._save_selection_state = mock.Mock()
        instance.notify = mock.Mock()
        instance.run_worker = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        instance.refresh_tasks = mock.AsyncMock()

        fake_project = mock.Mock()
        fake_project.default_login = "claude"
        action_globals = app_class._start_cli_task_background.__globals__

        with mock.patch.dict(
            action_globals,
            {
                "task_new": mock.Mock(return_value="7"),
                "load_project": mock.Mock(return_value=fake_project),
                "container_name": lambda *a: "terok-proj1-cli-7",
            },
        ):
            run(app_class._start_cli_task_background(instance, "deploy-hotfix"))

        # Verify push_screen was called with a TaskLaunchScreen
        instance.push_screen.assert_awaited_once()
        launch_screen = instance.push_screen.call_args[0][0]
        assert launch_screen._task_name == "deploy-hotfix"
        assert launch_screen._task_id == "7"
        assert launch_screen._project_id == "proj1"
        assert launch_screen._default_login == "claude"


# ---------------------------------------------------------------------------
# _on_launch_screen_result terminal title
# ---------------------------------------------------------------------------


class TestOnLaunchScreenResultTitle:
    """Verify _on_launch_screen_result uses the unified login title."""

    def test_bash_login_title_includes_task_name(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.refresh_tasks = mock.AsyncMock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._on_launch_screen_result.__globals__

        with mock.patch.dict(
            action_globals,
            {"get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c", "bash"])},
        ):
            result = ("proj1", "3", "fix-auth", "proj1-cli-3", "bash", None)
            run(app_class._on_launch_screen_result(instance, result))

        instance._launch_terminal_session.assert_awaited_once()
        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:3:fix-auth"
        assert call_kwargs["cname"] == "proj1-cli-3"

    def test_agent_login_title_includes_task_name(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.refresh_tasks = mock.AsyncMock()
        instance._launch_terminal_session = mock.AsyncMock()

        fake_provider = mock.Mock()
        fake_provider.binary = "claude"

        action_globals = app_class._on_launch_screen_result.__globals__

        with (
            mock.patch.dict(
                action_globals,
                {"get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"])},
            ),
            mock.patch.dict(
                "terok.lib.containers.headless_providers.HEADLESS_PROVIDERS",
                {"claude": fake_provider},
                clear=True,
            ),
        ):
            result = ("proj1", "5", "my-task", "proj1-cli-5", "claude", "fix it")
            run(app_class._on_launch_screen_result(instance, result))

        call_kwargs = instance._launch_terminal_session.call_args[1]
        assert call_kwargs["title"] == "proj1:5:my-task"

    def test_none_result_refreshes_tasks(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.refresh_tasks = mock.AsyncMock()
        instance._launch_terminal_session = mock.AsyncMock()

        run(app_class._on_launch_screen_result(instance, None))

        instance.refresh_tasks.assert_awaited_once()
        instance._launch_terminal_session.assert_not_awaited()

    def test_unknown_agent_notifies(self) -> None:
        _, app_class = import_app()
        instance = app_class()
        instance.current_project_id = "proj1"
        instance.refresh_tasks = mock.AsyncMock()
        instance.notify = mock.Mock()
        instance._launch_terminal_session = mock.AsyncMock()

        action_globals = app_class._on_launch_screen_result.__globals__

        with (
            mock.patch.dict(
                action_globals,
                {"get_login_command": mock.Mock(return_value=["podman", "exec", "-it", "c"])},
            ),
            mock.patch.dict(
                "terok.lib.containers.headless_providers.HEADLESS_PROVIDERS",
                {},
                clear=True,
            ),
        ):
            result = ("proj1", "5", "my-task", "proj1-cli-5", "nonexistent", "hi")
            run(app_class._on_launch_screen_result(instance, result))

        instance.notify.assert_called_once_with("Unknown agent: nonexistent")
        instance._launch_terminal_session.assert_not_awaited()
