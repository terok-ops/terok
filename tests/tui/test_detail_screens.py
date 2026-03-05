# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for TUI detail screens (Phase 2) and rendering helpers."""

import asyncio
import contextlib
from unittest import TestCase, main, mock

from rich.text import Text
from tui_test_helpers import import_app, import_screens, import_widgets, make_key_event


class RenderHelpersTests(TestCase):
    """Tests for the extracted render_* helper functions."""

    def test_render_project_details_returns_text(self) -> None:
        widgets = import_widgets()

        project = mock.Mock()
        project.id = "test-proj"
        project.upstream_url = "https://example.com/repo.git"
        project.security_class = "online"
        project.agents = ["codex"]
        state = {
            "ssh": True,
            "dockerfiles": True,
            "images": True,
            "gate": True,
        }

        result = widgets.render_project_details(project, state, task_count=5)

        self.assertIsInstance(result, Text)
        text_str = str(result)
        self.assertIn("test-proj", text_str)

    def test_render_project_details_none_project(self) -> None:
        widgets = import_widgets()

        result = widgets.render_project_details(None, None)

        self.assertIsInstance(result, Text)
        self.assertIn("No project", str(result))

    def test_render_task_details_returns_text(self) -> None:
        widgets = import_widgets()

        task = widgets.TaskMeta(
            task_id="42",
            mode="cli",
            workspace="/tmp/ws",
            web_port=None,
            backend="codex",
            container_state="running",
        )

        result = widgets.render_task_details(task, project_id="proj1")

        self.assertIsInstance(result, Text)
        text_str = str(result)
        self.assertIn("42", text_str)

    def test_render_task_details_none_shows_empty_message(self) -> None:
        widgets = import_widgets()

        result = widgets.render_task_details(None, empty_message="Nothing here")

        self.assertIsInstance(result, Text)
        self.assertIn("Nothing here", str(result))

    def test_render_project_loading(self) -> None:
        widgets = import_widgets()

        project = mock.Mock()
        project.id = "myproj"
        project.upstream_url = "https://example.com"
        project.security_class = "online"

        result = widgets.render_project_loading(project, task_count=3)

        self.assertIsInstance(result, Text)
        text_str = str(result)
        self.assertIn("myproj", text_str)

    def test_render_project_loading_none_project(self) -> None:
        widgets = import_widgets()

        result = widgets.render_project_loading(None)

        self.assertIsInstance(result, Text)
        self.assertIn("No project", str(result))

    def test_render_task_details_autopilot_mode(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="5",
            mode="run",
            workspace="/tmp/ws",
            web_port=None,
            container_state="running",
        )
        result = widgets.render_task_details(task, project_id="proj1")
        self.assertIsInstance(result, Text)
        text_str = str(result)
        self.assertIn("Autopilot", text_str)
        self.assertIn("terokctl task logs", text_str)

    def test_render_task_details_autopilot_with_exit_code(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="5",
            mode="run",
            workspace="/tmp/ws",
            web_port=None,
            exit_code=0,
        )
        result = widgets.render_task_details(task, project_id="proj1")
        text_str = str(result)
        self.assertIn("Exit code: 0", text_str)

    def test_render_task_details_with_work_status(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="10",
            mode="run",
            workspace="/tmp/ws",
            web_port=None,
            container_state="running",
            work_status="coding",
            work_message="Implementing JWT validation",
        )
        result = widgets.render_task_details(task, project_id="proj1")
        text_str = str(result)
        self.assertIn("Work:", text_str)
        self.assertIn("coding", text_str)
        self.assertIn("Implementing JWT validation", text_str)

    def test_render_task_details_work_status_without_message(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="11",
            mode="run",
            workspace="/tmp/ws",
            web_port=None,
            container_state="running",
            work_status="testing",
        )
        result = widgets.render_task_details(task, project_id="proj1")
        text_str = str(result)
        self.assertIn("Work:", text_str)
        self.assertIn("testing", text_str)

    def test_render_task_details_no_work_status(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="12",
            mode="cli",
            workspace="/tmp/ws",
            web_port=None,
            container_state="running",
        )
        result = widgets.render_task_details(task, project_id="proj1")
        text_str = str(result)
        self.assertNotIn("Work:", text_str)

    def test_format_task_label_with_work_status(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="13",
            mode="run",
            workspace="/tmp/ws",
            web_port=None,
            container_state="running",
            work_status="debugging",
        )
        task_list = widgets.TaskList()
        label = task_list._format_task_label(task)
        self.assertIn("work=debugging", label)

    def test_format_task_label_no_work_status(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="14",
            mode="cli",
            workspace="/tmp/ws",
            web_port=None,
            container_state="running",
        )
        task_list = widgets.TaskList()
        label = task_list._format_task_label(task)
        self.assertNotIn("work=", label)

    def test_format_task_label_autopilot(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="3",
            mode="run",
            workspace="/tmp/ws",
            web_port=None,
            container_state="running",
        )
        task_list = widgets.TaskList()
        label = task_list._format_task_label(task)
        self.assertIn("🚀", label)

    def test_task_meta_exit_code_field(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="1",
            mode="run",
            workspace="/tmp/ws",
            web_port=None,
            exit_code=1,
        )
        self.assertEqual(task.exit_code, 1)

    def test_task_meta_exit_code_default_none(self) -> None:
        widgets = import_widgets()
        task = widgets.TaskMeta(
            task_id="1",
            mode="cli",
            workspace="/tmp/ws",
            web_port=None,
        )
        self.assertIsNone(task.exit_code)


class ScreenConstructionTests(TestCase):
    """Tests that screen classes can be instantiated with correct arguments."""

    def test_project_details_screen_construction(self) -> None:
        screens, _ = import_screens()

        project = mock.Mock()
        project.id = "proj1"
        staleness = mock.Mock()

        screen = screens.ProjectDetailsScreen(
            project=project,
            state={"ssh": True},
            task_count=5,
            staleness=staleness,
        )
        self.assertEqual(screen._project, project)
        self.assertEqual(screen._state, {"ssh": True})
        self.assertEqual(screen._task_count, 5)
        self.assertEqual(screen._staleness, staleness)

    def test_task_details_screen_construction(self) -> None:
        screens, widgets = import_screens()

        task = widgets.TaskMeta(
            task_id="7",
            mode="cli",
            workspace="/tmp/ws",
            web_port=None,
            backend="codex",
            container_state="running",
        )

        screen = screens.TaskDetailsScreen(
            task=task,
            has_tasks=True,
            project_id="proj1",
            image_old=False,
        )
        self.assertEqual(screen._task_meta, task)
        self.assertTrue(screen._has_tasks)
        self.assertEqual(screen._project_id, "proj1")
        self.assertFalse(screen._image_old)

    def test_auth_actions_screen_construction(self) -> None:
        screens, _ = import_screens()

        screen = screens.AuthActionsScreen()
        self.assertIsNotNone(screen)

    def test_autopilot_prompt_screen_construction(self) -> None:
        screens, _ = import_screens()
        screen = screens.AutopilotPromptScreen()
        self.assertIsNotNone(screen)

    def test_agent_selection_screen_construction(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen()
        self.assertIsNotNone(screen)
        self.assertEqual(screen._default_agent, "claude")
        self.assertEqual(screen._subagents, [])

    def test_agent_selection_screen_custom_default(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="codex")
        self.assertEqual(screen._default_agent, "codex")

    def test_agent_selection_screen_with_subagents(self) -> None:
        screens, _ = import_screens()
        subagents = [
            {"name": "reviewer", "description": "Code reviewer", "default": True},
            {"name": "debugger", "description": "Debugger", "default": False},
        ]
        screen = screens.AgentSelectionScreen(subagents=subagents)
        self.assertIsNotNone(screen)
        self.assertEqual(len(screen._subagents), 2)

    def test_agent_selection_screen_no_subagents(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(subagents=None)
        self.assertIsNotNone(screen)
        self.assertEqual(screen._subagents, [])

    def test_agent_selection_screen_invalid_default_falls_back(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="nonexistent")
        # Should fall back to first registered provider, not keep invalid name
        self.assertNotEqual(screen._default_agent, "nonexistent")
        self.assertEqual(screen._selected_agent, screen._default_agent)

    def test_agent_selection_screen_cancel_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen()
        screen.dismiss = mock.Mock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(None)

    def test_agent_selection_screen_submit_returns_tuple(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="codex")
        screen.dismiss = mock.Mock()
        # Simulate submit without subagents — should return (agent, None)
        screen._submit()
        screen.dismiss.assert_called_once()
        result = screen.dismiss.call_args[0][0]
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "codex")
        self.assertIsNone(result[1])

    def test_agent_selection_screen_number_key_updates_selection(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="claude")
        # Stub query_one to return a mock OptionList
        mock_option_list = mock.Mock()
        screen.query_one = mock.Mock(return_value=mock_option_list)
        event = make_key_event("2")
        event.character = "2"
        screen.on_key(event)
        # Agent should have changed from default
        self.assertNotEqual(screen._selected_agent, "claude")
        event.stop.assert_called_once()


class TaskScreenKeyBindingTests(TestCase):
    """Tests for TaskDetailsScreen.on_key case-sensitive dispatch."""

    def test_shift_n_dismisses_task_start_cli(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("N")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("task_start_cli")
        event.stop.assert_called_once()

    def test_shift_w_dismisses_task_start_web(self) -> None:
        from terok.lib.core.config import set_experimental

        set_experimental(True)
        try:
            screens, _ = import_screens()
            screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
            screen.dismiss = mock.Mock()
            event = make_key_event("W")
            screen.on_key(event)
            screen.dismiss.assert_called_once_with("task_start_web")
        finally:
            set_experimental(False)

    def test_shift_w_blocked_without_experimental(self) -> None:
        from terok.lib.core.config import is_experimental, set_experimental

        previous = is_experimental()
        set_experimental(False)
        try:
            screens, _ = import_screens()
            screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
            screen.dismiss = mock.Mock()
            event = make_key_event("W")
            screen.on_key(event)
            screen.dismiss.assert_not_called()
        finally:
            set_experimental(previous)

    def test_shift_c_dismisses_new_always(self) -> None:
        screens, _ = import_screens()
        # C should work even when has_tasks=False
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("C")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("new")

    def test_shift_h_blocked_without_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("H")
        screen.on_key(event)
        screen.dismiss.assert_not_called()

    def test_shift_h_works_with_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("H")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("diff_head")

    def test_shift_p_works_with_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("P")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("diff_prev")

    def test_lowercase_d_blocked_without_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("d")
        screen.on_key(event)
        screen.dismiss.assert_not_called()

    def test_lowercase_d_works_with_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("d")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("delete")

    def test_lowercase_c_works_with_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("c")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("cli")

    def test_lowercase_w_works_with_tasks(self) -> None:
        from terok.lib.core.config import set_experimental

        set_experimental(True)
        try:
            screens, _ = import_screens()
            screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
            screen.dismiss = mock.Mock()
            event = make_key_event("w")
            screen.on_key(event)
            screen.dismiss.assert_called_once_with("web")
        finally:
            set_experimental(False)

    def test_lowercase_w_blocked_without_experimental(self) -> None:
        from terok.lib.core.config import is_experimental, set_experimental

        previous = is_experimental()
        set_experimental(False)
        try:
            screens, _ = import_screens()
            screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
            screen.dismiss = mock.Mock()
            event = make_key_event("w")
            screen.on_key(event)
            screen.dismiss.assert_not_called()
        finally:
            set_experimental(previous)

    def test_lowercase_r_works_with_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("r")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("restart")

    def test_escape_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("escape")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with(None)

    def test_q_dismisses_none(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("q")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with(None)

    def test_unmapped_key_does_nothing(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("x")
        screen.on_key(event)
        screen.dismiss.assert_not_called()
        event.stop.assert_not_called()

    def test_shift_a_dismisses_task_start_autopilot(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("A")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("task_start_autopilot")
        event.stop.assert_called_once()

    def test_lowercase_f_works_with_autopilot_task(self) -> None:
        screens, widgets = import_screens()
        task = widgets.TaskMeta(
            task_id="t1", mode="run", workspace="/w", web_port=None, container_state="running"
        )
        screen = screens.TaskDetailsScreen(task=task, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("f")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("follow_logs")

    def test_lowercase_f_works_for_non_autopilot_task(self) -> None:
        screens, widgets = import_screens()
        task = widgets.TaskMeta(
            task_id="t1", mode="cli", workspace="/w", web_port=None, container_state="running"
        )
        screen = screens.TaskDetailsScreen(task=task, has_tasks=True, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("f")
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("follow_logs")

    def test_lowercase_f_blocked_without_tasks(self) -> None:
        screens, _ = import_screens()
        screen = screens.TaskDetailsScreen(task=None, has_tasks=False, project_id="p")
        screen.dismiss = mock.Mock()
        event = make_key_event("f")
        screen.on_key(event)
        screen.dismiss.assert_not_called()


class AuthScreenOptionsTests(TestCase):
    """Tests that AuthActionsScreen includes the import option."""

    def test_auth_screen_has_import_opencode_option(self) -> None:
        """Verify AuthActionsScreen includes import_opencode_config option."""
        screens, _ = import_screens()

        screen = screens.AuthActionsScreen()
        screen.dismiss = mock.Mock()

        # Simulate selecting the import option via on_option_list_option_selected
        event = mock.Mock()
        event.option_id = "import_opencode_config"
        screen.on_option_list_option_selected(event)
        screen.dismiss.assert_called_once_with("import_opencode_config")

    def test_auth_screen_number_key_triggers_import(self) -> None:
        """Verify the number key after last provider selects import option."""
        from terok.lib.security.auth import AUTH_PROVIDERS

        screens, _ = import_screens()
        screen = screens.AuthActionsScreen()
        screen.dismiss = mock.Mock()

        # The import option is at index = len(AUTH_PROVIDERS)
        import_num = len(AUTH_PROVIDERS) + 1
        event = make_key_event(str(import_num))
        event.character = str(import_num)
        screen.on_key(event)
        screen.dismiss.assert_called_once_with("import_opencode_config")

    def test_opencode_config_screen_construction(self) -> None:
        """Verify OpenCodeConfigScreen can be instantiated."""
        screens, _ = import_screens()
        screen = screens.OpenCodeConfigScreen()
        self.assertIsNotNone(screen)

    def test_opencode_config_screen_cancel(self) -> None:
        """Verify cancel action dismisses with None."""
        screens, _ = import_screens()
        screen = screens.OpenCodeConfigScreen()
        screen.dismiss = mock.Mock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(None)


class ActionDispatchTests(TestCase):
    """Tests for action dispatch routing in the app."""

    def test_project_action_dispatch_project_init(self) -> None:
        app_mod, AppClass = import_app()
        instance = mock.Mock(spec=AppClass)

        coro = AppClass._handle_project_action(instance, "project_init")
        asyncio.run(coro)

        instance._action_project_init.assert_called_once()

    def test_project_action_dispatch_auth_providers(self) -> None:
        """Auth dispatch extracts the provider name from the action string."""
        from terok.lib.security.auth import AUTH_PROVIDERS

        app_mod, AppClass = import_app()

        for provider in AUTH_PROVIDERS:
            with self.subTest(provider=provider):
                instance = mock.Mock(spec=AppClass)
                coro = AppClass._handle_project_action(instance, f"auth_{provider}")
                asyncio.run(coro)
                instance._action_auth.assert_called_once_with(provider)

    def test_project_action_dispatch_import_opencode(self) -> None:
        """Import opencode config action routes to the handler."""
        app_mod, AppClass = import_app()
        instance = mock.Mock(spec=AppClass)
        coro = AppClass._handle_project_action(instance, "import_opencode_config")
        asyncio.run(coro)
        instance._action_import_opencode_config.assert_called_once()

    def test_task_action_dispatch_all(self) -> None:
        """Every entry in TASK_ACTION_HANDLERS routes to its handler."""
        app_mod, AppClass = import_app()

        for action, handler in app_mod.TASK_ACTION_HANDLERS.items():
            with self.subTest(action=action):
                instance = mock.Mock(spec=AppClass)
                coro = AppClass._handle_task_action(instance, action)
                asyncio.run(coro)
                getattr(instance, handler).assert_called_once()

    def test_project_action_dispatch_all(self) -> None:
        """Every entry in PROJECT_ACTION_HANDLERS routes to its handler."""
        app_mod, AppClass = import_app()

        for action, handler in app_mod.PROJECT_ACTION_HANDLERS.items():
            with self.subTest(action=action):
                instance = mock.Mock(spec=AppClass)
                coro = AppClass._handle_project_action(instance, action)
                asyncio.run(coro)
                getattr(instance, handler).assert_called_once()

    def test_action_run_cli_from_main(self) -> None:
        app_mod, AppClass = import_app()
        instance = mock.Mock(spec=AppClass)
        coro = AppClass.action_run_cli_from_main(instance)
        asyncio.run(coro)
        instance._action_task_start_cli.assert_called_once()

    def test_action_delete_task_from_main(self) -> None:
        app_mod, AppClass = import_app()
        instance = mock.Mock(spec=AppClass)
        coro = AppClass.action_delete_task_from_main(instance)
        asyncio.run(coro)
        instance.action_delete_task.assert_called_once()

    def test_action_run_autopilot_from_main(self) -> None:
        app_mod, AppClass = import_app()
        instance = mock.Mock(spec=AppClass)
        coro = AppClass.action_run_autopilot_from_main(instance)
        asyncio.run(coro)
        instance._action_task_start_autopilot.assert_called_once()

    def test_action_follow_logs_from_main(self) -> None:
        app_mod, AppClass = import_app()
        instance = mock.Mock(spec=AppClass)
        coro = AppClass.action_follow_logs_from_main(instance)
        asyncio.run(coro)
        instance._action_follow_logs.assert_called_once()


class ActionSelectionTests(TestCase):
    """Tests for task selection after task creation flows."""

    def test_action_new_task_selects_created_task(self) -> None:
        _, AppClass = import_app()

        instance = AppClass()
        instance.current_project_id = "proj1"
        instance._last_selected_tasks = {}
        instance.notify = mock.Mock()
        instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
        instance._save_selection_state = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()
        fake_task_new = mock.Mock(return_value="7")
        action_globals = AppClass.action_new_task.__globals__

        # push_screen now shows a name modal; simulate immediate callback
        async def fake_push_screen(screen, callback):
            await callback("test-name")

        instance.push_screen = fake_push_screen

        with (
            mock.patch.dict(
                action_globals,
                {"task_new": fake_task_new, "generate_task_name": lambda *a, **kw: "test-name"},
            ),
            mock.patch("builtins.input", return_value=""),
        ):
            asyncio.run(AppClass.action_new_task(instance))

        self.assertEqual(instance._last_selected_tasks.get("proj1"), "7")
        fake_task_new.assert_called_once_with("proj1", name="test-name")
        instance._save_selection_state.assert_called_once()
        instance.refresh_tasks.assert_awaited_once()

    def test_action_new_task_calls_focus_helper(self) -> None:
        _, AppClass = import_app()

        instance = AppClass()
        instance.current_project_id = "proj1"
        instance._last_selected_tasks = {}
        instance.notify = mock.Mock()
        instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
        instance._save_selection_state = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()
        fake_task_new = mock.Mock(return_value="8")
        action_globals = AppClass.action_new_task.__globals__
        original_focus = instance._focus_task_after_creation
        instance._focus_task_after_creation = mock.Mock(wraps=original_focus)

        async def fake_push_screen(screen, callback):
            await callback("test-name")

        instance.push_screen = fake_push_screen

        with (
            mock.patch.dict(
                action_globals,
                {"task_new": fake_task_new, "generate_task_name": lambda *a, **kw: "test-name"},
            ),
            mock.patch("builtins.input", return_value=""),
        ):
            asyncio.run(AppClass.action_new_task(instance))

        fake_task_new.assert_called_once_with("proj1", name="test-name")
        instance._focus_task_after_creation.assert_called_once_with("proj1", "8")
        instance._save_selection_state.assert_called_once()
        instance.refresh_tasks.assert_awaited_once()

    def test_task_start_cli_selects_created_task(self) -> None:
        _, AppClass = import_app()

        instance = AppClass()
        instance.current_project_id = "proj1"
        instance._last_selected_tasks = {}
        instance.notify = mock.Mock()
        instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
        instance._save_selection_state = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()
        fake_task_new = mock.Mock(return_value="42")
        fake_task_run_cli = mock.Mock()
        action_globals = AppClass._action_task_start_cli.__globals__

        async def fake_push_screen(screen, callback):
            await callback("test-name")

        instance.push_screen = fake_push_screen

        with (
            mock.patch.dict(
                action_globals,
                {
                    "task_new": fake_task_new,
                    "task_run_cli": fake_task_run_cli,
                    "generate_task_name": lambda *a, **kw: "test-name",
                },
            ),
            mock.patch("builtins.input", return_value=""),
        ):
            asyncio.run(AppClass._action_task_start_cli(instance))

        self.assertEqual(instance._last_selected_tasks.get("proj1"), "42")
        fake_task_new.assert_called_once_with("proj1", name="test-name")
        fake_task_run_cli.assert_called_once_with("proj1", "42")
        instance._save_selection_state.assert_called_once()
        instance.refresh_tasks.assert_awaited_once()

    def test_task_start_web_selects_created_task(self) -> None:
        from terok.lib.core.config import set_experimental

        set_experimental(True)
        try:
            _, AppClass = import_app()

            instance = AppClass()
            instance.current_project_id = "proj1"
            instance._last_selected_tasks = {}
            instance.notify = mock.Mock()
            instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
            instance._save_selection_state = mock.Mock()
            instance.refresh_tasks = mock.AsyncMock()
            instance._prompt_ui_backend = mock.Mock(return_value="codex")
            fake_task_new = mock.Mock(return_value="99")
            fake_task_run_web = mock.Mock()
            action_globals = AppClass._action_task_start_web.__globals__

            async def fake_push_screen(screen, callback):
                await callback("test-name")

            instance.push_screen = fake_push_screen

            with (
                mock.patch.dict(
                    action_globals,
                    {
                        "task_new": fake_task_new,
                        "task_run_web": fake_task_run_web,
                        "generate_task_name": lambda *a, **kw: "test-name",
                    },
                ),
                mock.patch("builtins.input", return_value=""),
            ):
                asyncio.run(AppClass._action_task_start_web(instance))

            self.assertEqual(instance._last_selected_tasks.get("proj1"), "99")
            fake_task_new.assert_called_once_with("proj1", name="test-name")
            fake_task_run_web.assert_called_once_with("proj1", "99", backend="codex")
            instance._save_selection_state.assert_called_once()
            instance.refresh_tasks.assert_awaited_once()
        finally:
            set_experimental(False)

    def test_autopilot_launch_selects_created_task(self) -> None:
        app_mod, AppClass = import_app()

        instance = AppClass()
        instance.current_project_id = "proj1"
        instance._last_selected_tasks = {}
        instance.notify = mock.Mock()
        instance._save_selection_state = mock.Mock()
        instance._start_autopilot_watcher = mock.Mock()
        instance.refresh_tasks = mock.AsyncMock()

        worker = mock.Mock()
        worker.group = "autopilot-launch"
        worker.result = ("proj1", "123", None)
        event = mock.Mock()
        event.worker = worker
        event.state = app_mod.WorkerState.SUCCESS

        asyncio.run(AppClass.handle_worker_state_changed(instance, event))

        self.assertEqual(instance._last_selected_tasks.get("proj1"), "123")
        instance._save_selection_state.assert_called_once()
        instance._start_autopilot_watcher.assert_called_once_with("proj1", "123")
        instance.refresh_tasks.assert_awaited_once()


class GateSyncActionTests(TestCase):
    """Tests for gate sync action behavior in suspended terminal mode."""

    def test_action_sync_gate_handles_system_exit_without_exiting_tui(self) -> None:
        _, AppClass = import_app()

        instance = AppClass()
        instance.current_project_id = "proj1"
        instance.notify = mock.Mock()
        instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
        instance._print_sync_gate_ssh_help = mock.Mock()
        instance._refresh_project_state = mock.Mock()
        fake_sync_project_gate = mock.Mock(side_effect=SystemExit("auth failed"))
        action_globals = AppClass._action_sync_gate.__globals__

        with (
            mock.patch.dict(action_globals, {"sync_project_gate": fake_sync_project_gate}),
            mock.patch("builtins.input", return_value=""),
        ):
            asyncio.run(AppClass._action_sync_gate(instance))

        fake_sync_project_gate.assert_called_once_with("proj1")
        instance._print_sync_gate_ssh_help.assert_called_once_with("proj1")
        instance.notify.assert_called_once_with("Gate sync failed. See terminal output.")
        instance._refresh_project_state.assert_called_once()

    def test_action_sync_gate_success_notifies_and_refreshes(self) -> None:
        _, AppClass = import_app()

        instance = AppClass()
        instance.current_project_id = "proj1"
        instance.notify = mock.Mock()
        instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
        instance._print_sync_gate_ssh_help = mock.Mock()
        instance._refresh_project_state = mock.Mock()
        fake_sync_project_gate = mock.Mock(
            return_value={"success": True, "created": False, "errors": []}
        )
        action_globals = AppClass._action_sync_gate.__globals__

        with (
            mock.patch.dict(action_globals, {"sync_project_gate": fake_sync_project_gate}),
            mock.patch("builtins.input", return_value=""),
        ):
            asyncio.run(AppClass._action_sync_gate(instance))

        fake_sync_project_gate.assert_called_once_with("proj1")
        instance._print_sync_gate_ssh_help.assert_not_called()
        instance.notify.assert_called_once_with("Gate synced from upstream")
        instance._refresh_project_state.assert_called_once()


class ProjectScreenNoneStateTests(TestCase):
    """Tests that ProjectDetailsScreen handles None state correctly."""

    def test_project_screen_stores_none_state(self) -> None:
        screens, _ = import_screens()
        project = mock.Mock()
        project.id = "proj1"
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=3)
        self.assertIsNone(screen._state)
        self.assertEqual(screen._task_count, 3)


if __name__ == "__main__":
    main()
