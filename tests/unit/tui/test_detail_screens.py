# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for TUI detail screens (Phase 2) and rendering helpers."""

import asyncio
import contextlib
import inspect
import sys
from collections.abc import Callable
from unittest import mock

import pytest
from rich.text import Text

from tests.testfs import MOCK_BASE, MOCK_CONFIG_ROOT
from tests.testnet import GATE_PORT, TEST_EGRESS_URL, TEST_UPSTREAM_URL
from tests.unit.tui.tui_test_helpers import (
    import_app,
    import_screens,
    import_widgets,
    make_key_event,
)

MOCK_WORKSPACE = str(MOCK_BASE / "ws")
TEST_PROJECT_ID = "test-proj"
TEST_PROJECT_ROOT = MOCK_CONFIG_ROOT / "projects" / TEST_PROJECT_ID


def make_project(**overrides: object) -> mock.Mock:
    """Return a project mock with sensible defaults for TUI rendering tests."""
    project = mock.Mock()
    project.id = TEST_PROJECT_ID
    project.upstream_url = TEST_UPSTREAM_URL
    project.security_class = "online"
    project.agents = ["codex"]
    project.agent_config = {}
    project.root = TEST_PROJECT_ROOT
    for key, value in overrides.items():
        setattr(project, key, value)
    return project


def make_task(widgets: object, **overrides: object) -> object:
    """Build a TaskMeta with defaults tuned for these tests."""
    defaults: dict[str, object] = {
        "task_id": "1",
        "mode": "cli",
        "workspace": MOCK_WORKSPACE,
        "web_port": None,
        "container_state": "running",
    }
    merged = defaults | overrides
    merged.setdefault("initialized", merged["mode"] is not None)
    return widgets.TaskMeta(**merged)


def make_task_screen(*, has_tasks: bool, mode: str | None = None) -> object:
    """Build a TaskDetailsScreen with a mocked dismiss method."""
    screens, widgets = import_screens()
    task = None if mode is None else make_task(widgets, task_id="t1", mode=mode)
    screen = screens.TaskDetailsScreen(task=task, has_tasks=has_tasks, project_id="p")
    screen.dismiss = mock.Mock()
    return screen


def render_task_details_text(**overrides: object) -> str:
    """Render task details and return plain text for substring assertions."""
    widgets = import_widgets()
    task = make_task(widgets, **overrides)
    return str(widgets.render_task_details(task, project_id="proj1"))


def format_task_label(**overrides: object) -> str:
    """Format a task label using the shared TaskMeta defaults."""
    widgets = import_widgets()
    return widgets.TaskList()._format_task_label(make_task(widgets, **overrides))


def run(coro: object) -> object:
    """Run an async test coroutine."""
    return asyncio.run(coro)


def assert_rendered_needles(text: str, present: list[str], absent: list[str]) -> None:
    """Assert that required needles are present and forbidden ones absent."""
    for needle in present:
        assert needle in text
    for needle in absent:
        assert needle not in text


async def fake_push_screen(
    _screen: object,
    callback: Callable[[str], object],
) -> None:
    """Simulate a modal that immediately returns a generated task name."""
    result = callback("test-name")
    if inspect.isawaitable(result):
        await result


def make_creation_app(app_class: type) -> object:
    """Build a TUI app instance prepared for task-creation workflows."""
    instance = app_class()
    instance.current_project_id = "proj1"
    instance._last_selected_tasks = {}
    instance.notify = mock.Mock()
    instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
    instance._save_selection_state = mock.Mock()
    instance.refresh_tasks = mock.AsyncMock()
    instance.push_screen = fake_push_screen
    instance._mark_launching = mock.Mock()
    return instance


def make_sync_gate_app(app_class: type) -> object:
    """Build a TUI app instance prepared for gate-sync workflows."""
    instance = app_class()
    instance.current_project_id = "proj1"
    instance.notify = mock.Mock()
    instance.suspend = mock.Mock(return_value=contextlib.nullcontext())
    instance._print_sync_gate_ssh_help = mock.Mock()
    instance._refresh_project_state = mock.Mock()
    return instance


def make_gate_server_status(
    *, mode: str = "systemd", running: bool = True, port: int = GATE_PORT
) -> mock.Mock:
    """Build a gate-server status mock with common defaults."""
    status = mock.Mock()
    status.mode = mode
    status.running = running
    status.port = port
    return status


def _task_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.TASK_ACTION_HANDLERS.items())


def _auth_providers() -> list[str]:
    from terok_executor import AUTH_PROVIDERS

    return list(AUTH_PROVIDERS)


def _project_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.PROJECT_ACTION_HANDLERS.items())


def _gate_server_action_cases() -> list[tuple[str, str]]:
    app_mod, _ = import_app()
    return list(app_mod.GATE_SERVER_ACTION_HANDLERS.items())


class TestRenderHelpers:
    """Tests for the extracted render_* helper functions."""

    def test_render_project_details_returns_text(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {
            "ssh": True,
            "dockerfiles": True,
            "images": True,
            "gate": True,
        }

        result = widgets.render_project_details(project, state, task_count=5)

        assert isinstance(result, Text)
        text_str = str(result)
        assert TEST_PROJECT_ID in text_str

    def test_render_project_details_shows_config_path(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}

        result = widgets.render_project_details(project, state, task_count=5)
        text_str = str(result)
        assert f"Config: {TEST_PROJECT_ROOT}" in text_str

    def test_render_project_details_none_project(self) -> None:
        widgets = import_widgets()

        result = widgets.render_project_details(None, None)

        assert isinstance(result, Text)
        assert "No project" in str(result)

    def test_render_task_details_returns_text(self) -> None:
        widgets = import_widgets()

        task = make_task(widgets, task_id="42", backend="codex")

        result = widgets.render_task_details(task, project_id="proj1")

        assert isinstance(result, Text)
        text_str = str(result)
        assert "42" in text_str

    def test_render_task_details_none_shows_empty_message(self) -> None:
        widgets = import_widgets()

        result = widgets.render_task_details(None, empty_message="Nothing here")

        assert isinstance(result, Text)
        assert "Nothing here" in str(result)

    def test_render_project_loading(self) -> None:
        widgets = import_widgets()
        project = make_project(id="myproj", upstream_url=TEST_EGRESS_URL)

        result = widgets.render_project_loading(project, task_count=3)

        assert isinstance(result, Text)
        text_str = str(result)
        assert "myproj" in text_str

    def test_render_project_loading_none_project(self) -> None:
        widgets = import_widgets()

        result = widgets.render_project_loading(None)

        assert isinstance(result, Text)
        assert "No project" in str(result)

    def test_render_task_details_autopilot_mode(self) -> None:
        widgets = import_widgets()
        task = make_task(widgets, task_id="5", mode="run")
        result = widgets.render_task_details(task, project_id="proj1")
        assert isinstance(result, Text)
        text_str = str(result)
        assert "Autopilot" in text_str
        assert "terok task logs" in text_str

    def test_render_task_details_autopilot_with_exit_code(self) -> None:
        widgets = import_widgets()
        task = make_task(widgets, task_id="5", mode="run", exit_code=0)
        result = widgets.render_task_details(task, project_id="proj1")
        text_str = str(result)
        assert "Exit code: 0" in text_str

    def test_web_url_emits_osc8_link_outside_web_mode(self) -> None:
        """In a real terminal the URL style carries an OSC 8 link for cross-line wrap."""
        import io

        from rich.console import Console

        widgets = import_widgets()
        task = make_task(widgets, task_id="42", mode="toad", web_port=8123, web_token="t0k")
        result = widgets.render_task_details(task, project_id="proj1", is_web=False)
        # Render to a buffer with ``force_terminal`` so styles serialise
        # to ANSI; the plain ``str(result)`` projection drops styling.
        buf = io.StringIO()
        Console(file=buf, width=80, force_terminal=True, color_system="standard").print(result)
        ansi = buf.getvalue()
        assert "\x1b]8;" in ansi  # OSC 8 sequence present
        assert "id=" in ansi  # shared id for cross-line stitching

    def test_web_url_skips_osc8_when_web_mode(self) -> None:
        """In web mode (xterm.js) we omit OSC 8 to avoid the dangerous-link dialog."""
        import io

        from rich.console import Console

        widgets = import_widgets()
        task = make_task(widgets, task_id="42", mode="toad", web_port=8123, web_token="t0k")
        result = widgets.render_task_details(task, project_id="proj1", is_web=True)
        buf = io.StringIO()
        Console(file=buf, width=80, force_terminal=True, color_system="standard").print(result)
        ansi = buf.getvalue()
        assert "\x1b]8;" not in ansi  # no OSC 8 — Textual @click handles the click
        # The URL itself is still in the rendered text so users can copy it.
        assert "8123" in ansi

    @pytest.mark.parametrize(
        ("overrides", "present", "absent"),
        [
            pytest.param(
                {
                    "task_id": "10",
                    "mode": "run",
                    "work_status": "coding",
                    "work_message": "Implementing JWT validation",
                },
                ["Work:", "coding", "Implementing JWT validation"],
                [],
                id="work-status-with-message",
            ),
            pytest.param(
                {"task_id": "11", "mode": "run", "work_status": "testing"},
                ["Work:", "testing"],
                [],
                id="work-status-no-message",
            ),
            pytest.param(
                {"task_id": "12", "mode": "cli"},
                [],
                ["Work:"],
                id="no-work-status",
            ),
        ],
    )
    def test_render_task_details_work_status_variants(
        self, overrides: dict[str, object], present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(render_task_details_text(**overrides), present, absent)

    @pytest.mark.parametrize(
        ("overrides", "present", "absent"),
        [
            pytest.param(
                {"task_id": "20", "mode": "run", "unrestricted": True},
                ["Perms:     unrestricted"],
                [],
                id="unrestricted",
            ),
            pytest.param(
                {"task_id": "21", "mode": "run", "unrestricted": False},
                ["Perms:     restricted"],
                ["Perms:     unrestricted"],
                id="restricted",
            ),
        ],
    )
    def test_render_task_details_permission_variants(
        self, overrides: dict[str, object], present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(render_task_details_text(**overrides), present, absent)

    @pytest.mark.parametrize(
        ("shield_state", "present", "absent"),
        [
            pytest.param(
                "DISABLED",
                ["Shield:", "disabled", "shield-security"],
                [],
                id="disabled",
            ),
            pytest.param(
                "INACTIVE",
                ["inactive", "shield-security"],
                [],
                id="inactive-running",
            ),
            pytest.param(
                "UP",
                ["up"],
                ["shield-security"],
                id="up",
            ),
        ],
    )
    def test_render_task_details_shield_variants(
        self, shield_state: str, present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(
            render_task_details_text(task_id="99", shield_state=shield_state),
            present,
            absent,
        )

    def test_render_shield_inactive_stopped_hooks_ok_shows_ready(self) -> None:
        """Stopped containers with healthy hooks show 'ready', no warning."""
        widgets = import_widgets()
        task = make_task(widgets, task_id="99", shield_state="INACTIVE", container_state="exited")
        text = str(widgets.render_task_details(task, project_id="proj1", shield_hooks_ok=True))
        assert "ready" in text
        assert "inactive" not in text
        assert "shield-security" not in text

    def test_render_shield_inactive_stopped_hooks_broken_shows_warning(self) -> None:
        """Stopped containers with broken hooks still show inactive warning."""
        widgets = import_widgets()
        task = make_task(widgets, task_id="99", shield_state="INACTIVE", container_state="exited")
        text = str(widgets.render_task_details(task, project_id="proj1", shield_hooks_ok=False))
        assert "inactive" in text
        assert "shield-security" in text
        assert "ready" not in text

    @pytest.mark.parametrize(
        ("overrides", "present", "absent"),
        [
            pytest.param(
                {"task_id": "13", "mode": "run", "work_status": "debugging"},
                ["work=debugging"],
                [],
                id="with-work-status",
            ),
            pytest.param(
                {"task_id": "14", "mode": "cli"},
                [],
                ["work="],
                id="without-work-status",
            ),
            pytest.param(
                {"task_id": "3", "mode": "run"},
                ["🚀"],
                [],
                id="autopilot",
            ),
        ],
    )
    def test_format_task_label_variants(
        self, overrides: dict[str, object], present: list[str], absent: list[str]
    ) -> None:
        assert_rendered_needles(format_task_label(**overrides), present, absent)

    @pytest.mark.parametrize(
        ("overrides", "expected"),
        [
            pytest.param({"task_id": "1", "mode": "run", "exit_code": 1}, 1, id="explicit-exit"),
            pytest.param({"task_id": "1", "mode": "cli"}, None, id="default-none"),
        ],
    )
    def test_task_meta_exit_code_variants(
        self, overrides: dict[str, object], expected: int | None
    ) -> None:
        widgets = import_widgets()
        task = make_task(widgets, **overrides)
        assert task.exit_code == expected


class TestScreenConstruction:
    """Tests that screen classes can be instantiated with correct arguments."""

    def test_project_details_screen_construction(self) -> None:
        screens, _ = import_screens()
        project = make_project(id="proj1")
        staleness = mock.Mock()

        screen = screens.ProjectDetailsScreen(
            project=project,
            state={"ssh": True},
            task_count=5,
            staleness=staleness,
        )
        assert screen._project == project
        assert screen._state == {"ssh": True}
        assert screen._task_count == 5
        assert screen._staleness == staleness

    def test_task_details_screen_construction(self) -> None:
        screens, widgets = import_screens()
        task = make_task(widgets, task_id="7", backend="codex")

        screen = screens.TaskDetailsScreen(
            task=task,
            has_tasks=True,
            project_id="proj1",
            image_old=False,
        )
        assert screen._task_meta == task
        assert screen._has_tasks
        assert screen._project_id == "proj1"
        assert not screen._image_old

    @pytest.mark.parametrize("screen_name", ["AuthActionsScreen", "AutopilotPromptScreen"])
    def test_simple_screen_construction(self, screen_name: str) -> None:
        screens, _ = import_screens()
        assert getattr(screens, screen_name)() is not None

    def test_agent_selection_screen_construction(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen()
        assert screen is not None
        assert screen._default_agent == "claude"
        assert screen._subagents == []

    def test_agent_selection_screen_custom_default(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="codex")
        assert screen._default_agent == "codex"

    def test_agent_selection_screen_with_subagents(self) -> None:
        screens, _ = import_screens()
        subagents = [
            {"name": "reviewer", "description": "Code reviewer", "default": True},
            {"name": "debugger", "description": "Debugger", "default": False},
        ]
        screen = screens.AgentSelectionScreen(subagents=subagents)
        assert screen is not None
        assert len(screen._subagents) == 2

    def test_agent_selection_screen_no_subagents(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(subagents=None)
        assert screen is not None
        assert screen._subagents == []

    def test_agent_selection_screen_invalid_default_falls_back(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="nonexistent")
        # Should fall back to first registered provider, not keep invalid name
        assert screen._default_agent != "nonexistent"
        assert screen._selected_agent == screen._default_agent

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
        assert isinstance(result, tuple)
        assert result[0] == "codex"
        assert result[1] is None

    def test_agent_selection_screen_number_key_updates_selection(self) -> None:
        screens, _ = import_screens()
        screen = screens.AgentSelectionScreen(default_agent="claude")
        # Stub query_one to return a mock OptionList
        mock_option_list = mock.Mock()
        screen.query_one = mock.Mock(return_value=mock_option_list)
        # Press "1" — selects the first agent (alphabetically), which is not "claude"
        event = make_key_event("1")
        event.character = "1"
        screen.on_key(event)
        assert screen._selected_agent != "claude"
        event.stop.assert_called_once()


class TestTaskScreenKeyBinding:
    """Tests for TaskDetailsScreen.on_key case-sensitive dispatch."""

    @pytest.mark.parametrize(
        ("key", "has_tasks", "expected", "mode", "should_stop"),
        [
            pytest.param("c", False, "task_start_cli", None, True, id="lower-c"),
            pytest.param("w", False, "task_start_toad", None, True, id="lower-w"),
            pytest.param("A", False, "task_start_autopilot", None, True, id="shift-a"),
            pytest.param("H", True, "diff_head", None, None, id="shift-h"),
            pytest.param("P", True, "diff_prev", None, None, id="shift-p"),
            pytest.param("X", True, "delete", None, None, id="shift-x"),
            pytest.param("r", True, "restart", None, None, id="lower-r"),
            pytest.param("t", True, "stop", None, None, id="lower-t"),
            pytest.param("d", True, "shield_down", None, None, id="lower-d"),
            pytest.param("D", True, "shield_down_all", None, None, id="shift-d"),
            pytest.param("s", True, "shield_up", None, None, id="lower-s"),
            pytest.param("escape", False, None, None, None, id="escape"),
            pytest.param("q", False, None, None, None, id="q"),
            pytest.param("f", True, "follow_logs", "run", None, id="follow-autopilot"),
            pytest.param("f", True, "follow_logs", "cli", None, id="follow-cli"),
        ],
    )
    def test_key_dispatch(
        self,
        key: str,
        has_tasks: bool,
        expected: str | None,
        mode: str | None,
        should_stop: bool | None,
    ) -> None:
        screen = make_task_screen(has_tasks=has_tasks, mode=mode)
        event = make_key_event(key)
        screen.on_key(event)
        screen.dismiss.assert_called_once_with(expected)
        if should_stop is True:
            event.stop.assert_called_once()
        elif should_stop is False:
            event.stop.assert_not_called()

    @pytest.mark.parametrize("key", ["H", "d", "D", "s", "f"])
    def test_task_only_keys_are_blocked_without_tasks(self, key: str) -> None:
        screen = make_task_screen(has_tasks=False)
        event = make_key_event(key)
        screen.on_key(event)
        screen.dismiss.assert_not_called()

    def test_unmapped_key_does_nothing(self) -> None:
        screen = make_task_screen(has_tasks=True)
        event = make_key_event("x")
        screen.on_key(event)
        screen.dismiss.assert_not_called()
        event.stop.assert_not_called()


class TestAuthScreenOptions:
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
        """Verify the number key after last provider selects import option.

        The shortcut only exists when the provider count is below 9
        (single-digit keys 1-9).  When all 9 slots are occupied, the
        import option has no number shortcut and pressing the next
        number is a no-op.
        """
        from terok_executor import AUTH_PROVIDERS

        screens, _ = import_screens()
        screen = screens.AuthActionsScreen()
        screen.dismiss = mock.Mock()

        import_num = len(AUTH_PROVIDERS) + 1
        event = make_key_event(str(import_num))
        event.character = str(import_num)
        screen.on_key(event)
        if import_num <= 9:
            screen.dismiss.assert_called_once_with("import_opencode_config")
        else:
            screen.dismiss.assert_not_called()

    def test_opencode_config_screen_construction(self) -> None:
        """Verify OpenCodeConfigScreen can be instantiated."""
        screens, _ = import_screens()
        screen = screens.OpenCodeConfigScreen()
        assert screen is not None

    def test_opencode_config_screen_cancel(self) -> None:
        """Verify cancel action dismisses with None."""
        screens, _ = import_screens()
        screen = screens.OpenCodeConfigScreen()
        screen.dismiss = mock.Mock()
        screen.action_cancel()
        screen.dismiss.assert_called_once_with(None)


class TestActionDispatch:
    """Tests for action dispatch routing in the app."""

    def test_project_action_dispatch_project_init(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)

        run(app_class._handle_project_action(instance, "project_init"))

        instance._action_project_init.assert_called_once()

    @pytest.mark.parametrize("provider", _auth_providers())
    def test_project_action_dispatch_auth_providers(self, provider: str) -> None:
        """Auth dispatch extracts the provider name from the action string."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_project_action(instance, f"auth_{provider}"))
        instance._action_auth.assert_called_once_with(provider)

    def test_project_action_dispatch_import_opencode(self) -> None:
        """Import opencode config action routes to the handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_project_action(instance, "import_opencode_config"))
        instance._action_import_opencode_config.assert_called_once()

    @pytest.mark.parametrize(("action", "handler"), _task_action_cases())
    def test_task_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every entry in TASK_ACTION_HANDLERS routes to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_task_action(instance, action))
        getattr(instance, handler).assert_called_once()

    @pytest.mark.parametrize(("action", "handler"), _project_action_cases())
    def test_project_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every entry in PROJECT_ACTION_HANDLERS routes to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._handle_project_action(instance, action))
        getattr(instance, handler).assert_called_once()

    def test_action_run_cli_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_run_cli_from_main(instance))
        instance._action_task_start_cli.assert_called_once()

    def test_action_delete_task_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_delete_task_from_main(instance))
        instance.action_delete_task.assert_called_once()

    def test_action_run_autopilot_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_run_autopilot_from_main(instance))
        instance._action_task_start_autopilot.assert_called_once()

    def test_action_follow_logs_from_main(self) -> None:
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class.action_follow_logs_from_main(instance))
        instance._action_follow_logs.assert_called_once()


_FAKE_SSH_INIT_RESULT = {
    "key_id": 42,
    "key_type": "ed25519",
    "fingerprint": "fp",
    "comment": "c",
    "public_line": "ssh-ed25519 AAAA c",
}


class TestSSHKeyRegistration:
    """TUI SSH init and project-init go through the facade's ``provision_ssh_key``."""

    def _get_mixin(self):
        """Import ProjectActionsMixin directly — avoids import_app() Textual stubs."""
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_action_init_ssh_provisions_key(self) -> None:
        """action_init_ssh calls ``provision_ssh_key`` and renders the summary."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "proj"

        with (
            mock.patch("terok.tui.project_actions.provision_ssh_key") as m_provision,
            mock.patch("terok.tui.project_actions.summarize_ssh_init") as m_summarize,
        ):
            m_provision.return_value = _FAKE_SSH_INIT_RESULT
            instance._run_suspended = mock.AsyncMock(side_effect=lambda fn, **kw: fn())
            run(mixin.action_init_ssh(instance))

            m_provision.assert_called_once_with("proj")
            m_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)

    def test_action_project_init_provisions_key(self) -> None:
        """_action_project_init runs provision_ssh_key, not raw manager + register."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "proj"

        with (
            mock.patch("terok.tui.project_actions.provision_ssh_key") as m_provision,
            mock.patch("terok.tui.project_actions.summarize_ssh_init") as m_summarize,
            mock.patch("terok.tui.project_actions.load_project"),
            mock.patch("terok.tui.project_actions.maybe_pause_for_ssh_key_registration"),
            mock.patch("terok.tui.project_actions.generate_dockerfiles"),
            mock.patch("terok.tui.project_actions.build_images"),
            mock.patch("terok.tui.project_actions.make_git_gate") as m_gate,
        ):
            m_provision.return_value = _FAKE_SSH_INIT_RESULT
            m_gate.return_value.sync.return_value = {
                "success": True,
                "path": "/tmp/terok-testing/g",
            }

            async def run_work(fn, **kw):
                fn()
                return True

            instance._run_suspended = run_work
            instance.notify = mock.Mock()
            run(mixin._action_project_init(instance))

            m_provision.assert_called_once_with("proj")
            m_summarize.assert_called_once_with(_FAKE_SSH_INIT_RESULT)


class TestActionAuth:
    """Per-project ``_action_auth`` and host-wide ``_action_auth_host_wide``."""

    def _get_mixin(self):
        from terok.tui.project_actions import ProjectActionsMixin

        return ProjectActionsMixin

    def test_per_project_calls_authenticate_with_project_id(self) -> None:
        """``_action_auth`` is the project-details path — passes the selection."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "myproj"
        instance._run_suspended = mock.AsyncMock(side_effect=lambda fn, **kw: fn())

        with mock.patch("terok.tui.project_actions.authenticate") as m_auth:
            run(mixin._action_auth(instance, "claude"))
        m_auth.assert_called_once_with("claude", "myproj")

    def test_per_project_without_selection_notifies_and_skips(self) -> None:
        """Without a selection ``_action_auth`` is a no-op — host-wide path is separate."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = None
        # ``notify`` lives on the App parent, not the mixin spec — wire it
        # explicitly so the early-return path can call it without erroring.
        instance.notify = mock.Mock()
        instance._run_suspended = mock.AsyncMock()

        with mock.patch("terok.tui.project_actions.authenticate") as m_auth:
            run(mixin._action_auth(instance, "claude"))
        m_auth.assert_not_called()
        instance._run_suspended.assert_not_called()
        instance.notify.assert_called_once()

    def test_host_wide_passes_none_regardless_of_selection(self) -> None:
        """``_action_auth_host_wide`` ignores ``current_project_id`` by design."""
        mixin = self._get_mixin()
        instance = mock.Mock(spec=mixin)
        instance.current_project_id = "selected-but-irrelevant"
        instance._run_suspended = mock.AsyncMock(side_effect=lambda fn, **kw: fn())

        with mock.patch("terok.tui.project_actions.authenticate") as m_auth:
            run(mixin._action_auth_host_wide(instance, "claude"))
        m_auth.assert_called_once_with("claude", None)


class TestActionSelection:
    """Tests for task selection after task creation flows."""

    def test_task_start_cli_selects_created_task(self) -> None:
        _, app_class = import_app()
        instance = make_creation_app(app_class)
        instance.run_worker = mock.Mock()
        instance.push_screen = mock.AsyncMock()
        fake_task_new = mock.Mock(return_value="42")
        action_globals = app_class._start_cli_task_background.__globals__

        fake_project = mock.Mock()
        fake_project.default_login = None
        fake_load_project = mock.Mock(return_value=fake_project)

        with mock.patch.dict(
            action_globals,
            {
                "task_new": fake_task_new,
                "load_project": fake_load_project,
                "container_name": lambda *a: "terok-proj1-cli-42",
            },
        ):
            run(app_class._start_cli_task_background(instance, "test-name"))

        assert instance._last_selected_tasks.get("proj1") == "42"
        fake_task_new.assert_called_once_with("proj1", name="test-name")
        instance._save_selection_state.assert_called_once()
        instance.run_worker.assert_called_once()
        instance.refresh_tasks.assert_awaited()

    def test_autopilot_launch_selects_created_task(self) -> None:
        app_mod, app_class = import_app()

        instance = app_class()
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

        run(app_class.handle_worker_state_changed(instance, event))

        assert instance._last_selected_tasks.get("proj1") == "123"
        instance._save_selection_state.assert_called_once()
        instance._start_autopilot_watcher.assert_called_once_with("proj1", "123")
        instance.refresh_tasks.assert_awaited_once()


class TestGateSyncAction:
    """Tests for gate sync action behavior in suspended terminal mode."""

    def test_action_sync_gate_handles_system_exit_without_exiting_tui(self) -> None:
        _, app_class = import_app()
        instance = make_sync_gate_app(app_class)
        fake_gate = mock.Mock()
        fake_gate.sync = mock.Mock(side_effect=SystemExit("auth failed"))
        action_globals = app_class._action_sync_gate.__globals__

        with (
            mock.patch.dict(
                action_globals,
                {
                    "make_git_gate": mock.Mock(return_value=fake_gate),
                    "load_project": mock.Mock(),
                },
            ),
            mock.patch("builtins.input", return_value=""),
        ):
            run(app_class._action_sync_gate(instance))

        fake_gate.sync.assert_called_once()
        instance._print_sync_gate_ssh_help.assert_called_once_with("proj1")
        instance.notify.assert_called_once_with("Gate sync failed. See terminal output.")
        instance._refresh_project_state.assert_called_once()

    def test_action_sync_gate_success_notifies_and_refreshes(self) -> None:
        _, app_class = import_app()
        instance = make_sync_gate_app(app_class)
        fake_gate = mock.Mock()
        fake_gate.sync = mock.Mock(return_value={"success": True, "created": False, "errors": []})
        action_globals = app_class._action_sync_gate.__globals__

        with (
            mock.patch.dict(
                action_globals,
                {
                    "make_git_gate": mock.Mock(return_value=fake_gate),
                    "load_project": mock.Mock(),
                },
            ),
            mock.patch("builtins.input", return_value=""),
        ):
            run(app_class._action_sync_gate(instance))

        fake_gate.sync.assert_called_once()
        instance._print_sync_gate_ssh_help.assert_not_called()
        instance.notify.assert_called_once_with("Gate synced from upstream")
        instance._refresh_project_state.assert_called_once()


class TestProjectScreenNoneState:
    """Tests that ProjectDetailsScreen handles None state correctly."""

    def test_project_screen_stores_none_state(self) -> None:
        screens, _ = import_screens()
        project = make_project(id="proj1")
        screen = screens.ProjectDetailsScreen(project=project, state=None, task_count=3)
        assert screen._state is None
        assert screen._task_count == 3


class TestGateServerScreen:
    """Tests for the GateServerScreen."""

    def test_gate_server_screen_construction(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status()
        screen = screens.GateServerScreen(status)
        assert screen._status == status

    def test_gate_server_screen_construction_default(self) -> None:
        screens, _ = import_screens()
        screen = screens.GateServerScreen()
        assert screen._status is None

    def test_gate_server_screen_dismiss(self) -> None:
        screens, _ = import_screens()
        screen = screens.GateServerScreen()
        screen.dismiss = mock.Mock()
        screen.action_dismiss()
        screen.dismiss.assert_called_once_with(None)

    @pytest.mark.parametrize(
        ("method_name", "expected"),
        [
            pytest.param("action_gate_install", "gate_install", id="install"),
            pytest.param("action_gate_uninstall", "gate_uninstall", id="uninstall"),
            pytest.param("action_gate_start", "gate_start", id="start"),
            pytest.param("action_gate_stop", "gate_stop", id="stop"),
        ],
    )
    def test_gate_server_screen_actions(self, method_name: str, expected: str) -> None:
        screens, _ = import_screens()
        screen = screens.GateServerScreen()
        screen.dismiss = mock.Mock()
        getattr(screen, method_name)()
        screen.dismiss.assert_called_once_with(expected)


class TestDisableOptions:
    """Tests for the _disable_options helper used by systemd-dependent screens."""

    def test_disable_options_disables_matching_ids(self) -> None:
        """Options whose id is in the frozenset are disabled."""
        screens, _ = import_screens()
        option_list = mock.Mock()
        opts = [mock.Mock(id="a"), mock.Mock(id="b"), mock.Mock(id="c")]
        option_list.option_count = len(opts)
        option_list.get_option_at_index = lambda idx: opts[idx]

        screens._disable_options(option_list, frozenset({"a", "c"}))

        option_list.disable_option_at_index.assert_any_call(0)
        option_list.disable_option_at_index.assert_any_call(2)
        assert option_list.disable_option_at_index.call_count == 2

    def test_gate_server_systemd_option_ids(self) -> None:
        """GateServerScreen declares the correct systemd option ids."""
        screens, _ = import_screens()
        assert {"gate_install", "gate_uninstall"} == screens.GateServerScreen._SYSTEMD_OPTIONS

    def test_vault_systemd_option_ids(self) -> None:
        """VaultScreen declares the correct systemd option ids."""
        screens, _ = import_screens()
        assert {
            "vault_install",
            "vault_uninstall",
        } == screens.VaultScreen._SYSTEMD_OPTIONS


class TestCommandPalette:
    """Tests for command palette customization."""

    def test_get_system_commands_includes_gate_server(self) -> None:
        from tests.unit.tui.tui_test_helpers import build_textual_stubs

        stubs = build_textual_stubs()
        _, app_class = import_app(stubs)
        instance = app_class()
        # get_system_commands imports SystemCommand at call time, so we need
        # textual.app in sys.modules during the call.
        with mock.patch.dict(sys.modules, stubs):
            commands = list(app_class.get_system_commands(instance, screen=mock.Mock()))
        titles = [cmd.title for cmd in commands]
        assert "Git Gate Server" in titles

    def test_get_system_commands_includes_authenticate(self) -> None:
        """The host-wide auth flow is reachable from the command palette."""
        from tests.unit.tui.tui_test_helpers import build_textual_stubs

        stubs = build_textual_stubs()
        _, app_class = import_app(stubs)
        instance = app_class()
        with mock.patch.dict(sys.modules, stubs):
            commands = list(app_class.get_system_commands(instance, screen=mock.Mock()))
        titles = [cmd.title for cmd in commands]
        assert "Authenticate agents and tools" in titles


class TestGlobalAuthBinding:
    """The top-level ``a`` shortcut + ``action_authenticate`` route."""

    def test_app_binds_a_to_authenticate(self) -> None:
        """``a`` on the main screen opens the auth modal — no project required."""
        _, app_class = import_app()
        bindings = {(b[0], b[1]) for b in app_class.BINDINGS}
        assert ("a", "authenticate") in bindings

    def test_action_authenticate_pushes_auth_actions_screen(self) -> None:
        """``action_authenticate`` opens [`AuthActionsScreen`][terok.tui.screens.AuthActionsScreen]."""
        _, app_class = import_app()
        # No spec — ``push_screen`` is inherited from the Textual ``App``
        # stub and isn't present on the inner ``TerokTUI`` class itself.
        instance = mock.MagicMock()
        instance.push_screen = mock.AsyncMock()
        run(app_class.action_authenticate(instance))
        instance.push_screen.assert_awaited_once()
        pushed_screen, callback = instance.push_screen.await_args.args
        assert type(pushed_screen).__name__ == "AuthActionsScreen"
        assert callback == instance._on_authenticate_result

    def test_on_authenticate_result_routes_to_host_wide(self) -> None:
        """``auth_<provider>`` from the global modal lands in the host-wide handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_authenticate_result(instance, "auth_claude"))
        instance._action_auth_host_wide.assert_awaited_once_with("claude")
        instance._action_auth.assert_not_called()

    def test_on_authenticate_result_routes_opencode_import(self) -> None:
        """OpenCode import from the global modal reuses the project-screen handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_authenticate_result(instance, "import_opencode_config"))
        instance._action_import_opencode_config.assert_awaited_once()

    def test_on_authenticate_result_ignores_cancel(self) -> None:
        """Esc on the modal returns ``None`` — handler is a no-op."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_authenticate_result(instance, None))
        instance._action_auth_host_wide.assert_not_called()
        instance._action_import_opencode_config.assert_not_called()


class TestRenderGateServerStatus:
    """Tests for the render_gate_server_status helper."""

    def test_render_gate_server_status_none(self) -> None:
        screens, _ = import_screens()
        result = screens.render_gate_server_status(None)
        assert isinstance(result, Text)
        assert "unknown" in str(result)

    def test_render_gate_server_status_running(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status()
        with mock.patch.object(screens, "check_units_outdated", return_value=None):
            result = screens.render_gate_server_status(status)
        text_str = str(result)
        assert "running" in text_str
        assert "systemd" in text_str
        assert str(GATE_PORT) in text_str

    def test_render_gate_server_status_stopped(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status(mode="none", running=False)
        with mock.patch.object(screens, "check_units_outdated", return_value=None):
            result = screens.render_gate_server_status(status)
        text_str = str(result)
        assert "stopped" in text_str
        assert "not running" in text_str

    def test_render_gate_server_status_outdated(self) -> None:
        screens, _ = import_screens()
        status = make_gate_server_status()
        with mock.patch.object(
            screens, "check_units_outdated", return_value="Units outdated (v1 vs v3)"
        ):
            result = screens.render_gate_server_status(status)
        text_str = str(result)
        assert "outdated" in text_str


class TestCombinedGateStatus:
    """Tests for combined gate status in render_project_details."""

    def test_render_project_details_gate_server_down(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}
        gate_status = mock.Mock()
        gate_status.running = False

        result = widgets.render_project_details(
            project, state, task_count=5, gate_server_status=gate_status
        )
        text_str = str(result)
        assert "gate down" in text_str

    def test_render_project_details_gate_server_ok(self) -> None:
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": True}
        gate_status = mock.Mock()
        gate_status.running = True

        result = widgets.render_project_details(
            project, state, task_count=5, gate_server_status=gate_status
        )
        text_str = str(result)
        assert "gate down" not in text_str
        assert "yes" in text_str

    def test_render_project_details_gate_server_none_fallback(self) -> None:
        """When gate_server_status is None, show normal repo-based status."""
        widgets = import_widgets()
        project = make_project()
        state = {"ssh": True, "dockerfiles": True, "images": True, "gate": False}

        result = widgets.render_project_details(project, state, task_count=5)
        text_str = str(result)
        assert "gate down" not in text_str


class TestGateServerActionDispatch:
    """Tests for gate server action dispatch routing."""

    @pytest.mark.parametrize(("action", "handler"), _gate_server_action_cases())
    def test_gate_server_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every entry in GATE_SERVER_ACTION_HANDLERS routes to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_gate_server_action_result(instance, action))
        getattr(instance, handler).assert_called_once()

    def test_gate_server_action_dispatch_none(self) -> None:
        """None result does not dispatch any handler."""
        app_mod, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_gate_server_action_result(instance, None))
        # No action handler should have been called
        for handler in app_mod.GATE_SERVER_ACTION_HANDLERS.values():
            getattr(instance, handler).assert_not_called()


class TestDeleteTaskResult:
    """Tests for _delete_task tuple shape and delete notification messages."""

    def _call_delete(
        self,
        side_effect: BaseException | None = None,
        warnings: list[str] | None = None,
        **kwargs: str,
    ) -> tuple[str, str, str, str | None, list[str]]:
        """Import app, mock task_delete, and call _delete_task."""
        from terok.lib.orchestration.tasks import TaskDeleteResult

        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        # Patch task_delete in the method's own globals (the reimported module dict).
        fn_globals = app_class._delete_task.__globals__
        orig = fn_globals["task_delete"]
        if side_effect:
            fake = mock.Mock(side_effect=side_effect)
        else:
            task_id = kwargs.get("task_id", "3")
            fake = mock.Mock(
                return_value=TaskDeleteResult(task_id=task_id, warnings=warnings or [])
            )
        fn_globals["task_delete"] = fake
        try:
            return app_class._delete_task(
                instance,
                kwargs.get("project_id", "proj1"),
                kwargs.get("task_id", "3"),
                kwargs.get("task_name", "fix-login"),
            )
        finally:
            fn_globals["task_delete"] = orig

    def test_delete_task_success_returns_five_tuple(self) -> None:
        """Successful deletion returns (project_id, task_id, task_name, None, [])."""
        assert self._call_delete() == ("proj1", "3", "fix-login", None, [])

    def test_delete_task_error_returns_five_tuple(self) -> None:
        """Failed deletion returns error string and empty warnings."""
        result = self._call_delete(side_effect=RuntimeError("boom"))
        assert result == ("proj1", "3", "fix-login", "boom", [])

    def test_delete_task_systemexit_returns_five_tuple(self) -> None:
        """SystemExit during deletion is captured in the error slot."""
        result = self._call_delete(side_effect=SystemExit("not found"), task_name="")
        assert result == ("proj1", "3", "", "not found", [])

    def test_delete_task_empty_name(self) -> None:
        """Empty task name is preserved through the round-trip."""
        result = self._call_delete(task_name="")
        assert result == ("proj1", "3", "", None, [])

    def test_delete_task_warnings_propagated(self) -> None:
        """Warnings from TaskDeleteResult are passed through the tuple."""
        result = self._call_delete(warnings=["Container c1: locked"])
        assert result == ("proj1", "3", "fix-login", None, ["Container c1: locked"])


# ---------------------------------------------------------------------------
# Vault Screen
# ---------------------------------------------------------------------------

MOCK_VAULT_SOCKET = MOCK_BASE / "run" / "vault.sock"
MOCK_VAULT_DB = MOCK_BASE / "vault" / "credentials.db"
MOCK_VAULT_ROUTES = MOCK_BASE / "vault" / "routes.json"


def make_vault_status(
    *,
    mode: str = "daemon",
    running: bool = True,
    routes_configured: int = 3,
    credentials_stored: tuple[str, ...] = ("claude", "gh"),
) -> mock.Mock:
    """Build a vault status mock with common defaults."""
    status = mock.Mock()
    status.mode = mode
    status.running = running
    status.socket_path = MOCK_VAULT_SOCKET
    status.db_path = MOCK_VAULT_DB
    status.routes_path = MOCK_VAULT_ROUTES
    status.routes_configured = routes_configured
    status.credentials_stored = credentials_stored
    return status


class TestVaultScreen:
    """Tests for the VaultScreen."""

    def test_vault_screen_construction(self) -> None:
        """Screen stores the provided status."""
        screens, _ = import_screens()
        status = make_vault_status()
        screen = screens.VaultScreen(status)
        assert screen._status == status

    def test_vault_screen_construction_default(self) -> None:
        """Screen defaults to None status."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        assert screen._status is None

    def test_vault_screen_dismiss(self) -> None:
        """action_dismiss sends None result."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        screen.dismiss = mock.Mock()
        screen.action_dismiss()
        screen.dismiss.assert_called_once_with(None)

    @pytest.mark.parametrize(
        ("method_name", "expected"),
        [
            pytest.param("action_vault_install", "vault_install", id="install"),
            pytest.param("action_vault_uninstall", "vault_uninstall", id="uninstall"),
            pytest.param("action_vault_start", "vault_start", id="start"),
            pytest.param("action_vault_stop", "vault_stop", id="stop"),
        ],
    )
    def test_vault_screen_actions(self, method_name: str, expected: str) -> None:
        """Action methods dismiss with the expected result string."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        screen.dismiss = mock.Mock()
        getattr(screen, method_name)()
        screen.dismiss.assert_called_once_with(expected)


class TestRenderVaultStatus:
    """Tests for the render_vault_status helper."""

    def test_render_vault_status_none(self) -> None:
        """None status renders an 'unknown' message."""
        screens, _ = import_screens()
        result = screens.render_vault_status(None)
        assert isinstance(result, Text)
        assert "unknown" in str(result)

    def test_render_vault_status_running(self) -> None:
        """Running vault shows status and credential details."""
        screens, _ = import_screens()
        status = make_vault_status()
        result = screens.render_vault_status(status)
        text_str = str(result)
        assert "running" in text_str
        assert "claude" in text_str
        assert "3 configured" in text_str

    def test_render_vault_status_stopped(self) -> None:
        """Stopped vault shows hint text."""
        screens, _ = import_screens()
        status = make_vault_status(running=False)
        result = screens.render_vault_status(status)
        text_str = str(result)
        assert "stopped" in text_str
        assert "actions below" in text_str

    def test_render_vault_status_standby(self) -> None:
        """Systemd socket active but service idle shows standby."""
        screens, _ = import_screens()
        status = make_vault_status(mode="systemd", running=False)
        with mock.patch("terok_sandbox.is_vault_socket_active", return_value=True):
            result = screens.render_vault_status(status)
        text_str = str(result)
        assert "standby" in text_str
        assert "first connection" in text_str
        # Standby should not show the "actions below" help text
        assert "actions below" not in text_str

    def test_render_vault_status_systemd_stopped(self) -> None:
        """Systemd socket inactive shows stopped with help text."""
        screens, _ = import_screens()
        status = make_vault_status(mode="systemd", running=False)
        with mock.patch("terok_sandbox.is_vault_socket_active", return_value=False):
            result = screens.render_vault_status(status)
        text_str = str(result)
        assert "stopped" in text_str
        assert "actions below" in text_str

    def test_render_vault_status_no_credentials(self) -> None:
        """Empty credentials tuple renders 'none stored'."""
        screens, _ = import_screens()
        status = make_vault_status(credentials_stored=())
        result = screens.render_vault_status(status)
        assert "none stored" in str(result)


class TestVaultScreenRefresh:
    """Tests for vault screen refresh logic."""

    def test_refresh_status_updates_status(self) -> None:
        """_refresh_status fetches new status from terok_sandbox."""
        screens, _ = import_screens()
        screen = screens.VaultScreen(make_vault_status(running=False))
        detail = mock.Mock()
        screen.query_one = mock.Mock(return_value=detail)
        new_status = make_vault_status(running=True)
        with mock.patch("terok_sandbox.get_vault_status", return_value=new_status):
            screen._refresh_status()
        assert screen._status is new_status
        detail.update.assert_called_once()

    def test_refresh_status_handles_exception(self) -> None:
        """_refresh_status sets status to None on failure."""
        screens, _ = import_screens()
        screen = screens.VaultScreen(make_vault_status())
        detail = mock.Mock()
        screen.query_one = mock.Mock(return_value=detail)
        with mock.patch("terok_sandbox.get_vault_status", side_effect=RuntimeError):
            screen._refresh_status()
        assert screen._status is None

    def test_vault_screen_refresh_action(self) -> None:
        """action_vault_refresh calls _refresh_status."""
        screens, _ = import_screens()
        screen = screens.VaultScreen()
        screen._refresh_status = mock.Mock()
        screen.action_vault_refresh()
        screen._refresh_status.assert_called_once()


class TestVaultCommandPalette:
    """Tests for vault in the command palette."""

    def test_get_system_commands_includes_vault(self) -> None:
        """Command palette includes 'Vault' entry."""
        from tests.unit.tui.tui_test_helpers import build_textual_stubs

        stubs = build_textual_stubs()
        _, app_class = import_app(stubs)
        instance = app_class()
        with mock.patch.dict(sys.modules, stubs):
            commands = list(app_class.get_system_commands(instance, screen=mock.Mock()))
        titles = [cmd.title for cmd in commands]
        assert "Vault" in titles


class TestVaultActionDispatch:
    """Tests for vault action handler dispatch."""

    @pytest.mark.parametrize(
        ("action", "handler"),
        [
            ("vault_install", "_action_vault_install"),
            ("vault_uninstall", "_action_vault_uninstall"),
            ("vault_start", "_action_vault_start"),
            ("vault_stop", "_action_vault_stop"),
        ],
    )
    def test_vault_action_dispatch_all(self, action: str, handler: str) -> None:
        """Every vault action routes through the callback to its handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_vault_action_result(instance, action))
        getattr(instance, handler).assert_called_once()

    def test_vault_action_dispatch_none(self) -> None:
        """None result does not dispatch any handler."""
        _, app_class = import_app()
        instance = mock.Mock(spec=app_class)
        run(app_class._on_vault_action_result(instance, None))
        instance._action_vault_install.assert_not_called()
        instance._action_vault_uninstall.assert_not_called()
        instance._action_vault_start.assert_not_called()
        instance._action_vault_stop.assert_not_called()
