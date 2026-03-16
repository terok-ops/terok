# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the new task creation and launch workflow (#296 + #446)."""

import asyncio
from unittest import mock

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
            default_login="claude",
        )
        assert screen._container_name == "terok-p-cli-1"
        assert screen._project_id == "p"
        assert screen._task_id == "1"
        assert screen._default_login == "claude"
        assert not screen._container_ready

    def test_construction_default_bash(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
        assert screen._default_login == "bash"

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
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
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
        screen.dismiss.assert_called_once_with(("p", "1", "c", "claude", "fix the bug"))

    def test_do_login_bash_clears_prompt(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskLaunchScreen(container_name="c", project_id="p", task_id="1")
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
        screen.dismiss.assert_called_once_with(("p", "1", "c", "bash", None))

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

    def _import_helper(self):
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
