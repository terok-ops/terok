# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project list and action bar widgets."""

import inspect
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, ListItem, ListView, Static

from ...lib.containers.task_display import GPU_DISPLAY, SECURITY_CLASS_DISPLAY, has_gpu
from ...lib.core.projects import ProjectConfig
from ...lib.util.emoji import render_emoji


class ProjectListItem(ListItem):
    """List item that carries project metadata."""

    def __init__(self, project_id: str, label: str, generation: int) -> None:
        """Create a project list item with its ID and display label."""
        super().__init__(Static(label, markup=False))
        self.project_id = project_id
        self.generation = generation


class ProjectList(ListView):
    """Left-hand project list widget."""

    # Override ListView's Enter to open the project actions modal instead
    # of firing ListView.Selected.  Uses the ``app.`` prefix so the action
    # is dispatched to the App instance.
    BINDINGS = [
        ("enter", "app.show_project_actions", "Project\u2026"),
        ("n", "app.new_project_wizard", "New Project"),
    ]

    class ProjectSelected(Message):
        """Posted when a project is highlighted in the list."""

        def __init__(self, project_id: str) -> None:
            """Create the message with the selected project's ID."""
            super().__init__()
            self.project_id = project_id

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the project list with empty state."""
        super().__init__(**kwargs)
        self.projects: list[ProjectConfig] = []
        self._generation = 0

    def set_projects(self, projects: list[ProjectConfig]) -> None:
        """Populate the list with projects."""
        self.projects = projects
        self._generation += 1
        self.clear()
        for proj in projects:
            sec = SECURITY_CLASS_DISPLAY.get(proj.security_class, SECURITY_CLASS_DISPLAY["online"])
            gpu = GPU_DISPLAY[has_gpu(proj)]
            label = f"{render_emoji(sec)}{render_emoji(gpu)} {proj.id}"
            self.append(ProjectListItem(proj.id, label, self._generation))

    def select_project(self, project_id: str) -> None:
        """Select a project by id."""
        for idx, proj in enumerate(self.projects):
            if proj.id == project_id:
                self.index = idx
                break

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:  # type: ignore[override]
        """Update selection immediately when highlight changes."""
        if event.item is None:
            return
        self._post_selected_project(event.item)

    def _post_selected_project(self, item: ListItem | None = None) -> None:
        """Emit a ProjectSelected message for the given or currently highlighted item."""
        if item is None:
            item = self.highlighted_child
        if not isinstance(item, ProjectListItem):
            return
        if item.parent is not self:
            return
        if item.generation != self._generation:
            return
        self.post_message(self.ProjectSelected(item.project_id))


class ProjectActions(Static):
    """Single-row action bar for project + task actions."""

    def compose(self) -> ComposeResult:
        """Yield two rows of action buttons for project and task operations."""
        with Horizontal():
            yield Button("[yellow]g[/yellow]en", id="btn-generate", compact=True)
            yield Button("[yellow]b[/yellow]uild", id="btn-build", compact=True)
            yield Button("[yellow]A[/yellow]gents", id="btn-build-agents", compact=True)
            yield Button("[yellow]F[/yellow]ull", id="btn-build-full", compact=True)
            yield Button("[yellow]s[/yellow]sh", id="btn-ssh-init", compact=True)
            yield Button("[yellow]S[/yellow]ync", id="btn-sync-gate", compact=True)

        with Horizontal():
            yield Button("[yellow]t[/yellow] new", id="btn-new-task", compact=True)
            yield Button("[yellow]r[/yellow] cli", id="btn-task-run-cli", compact=True)
            yield Button("[yellow]w[/yellow] web", id="btn-task-run-web", compact=True)
            yield Button("[yellow]d[/yellow]el", id="btn-task-delete", compact=True)

    async def on_button_pressed(self, event: Button.Pressed) -> None:  # type: ignore[override]
        """Route button presses to the corresponding App action method."""
        btn_id = event.button.id
        app = self.app
        if not app or not btn_id:
            return

        mapping = {
            "btn-generate": "action_generate_dockerfiles",
            "btn-build": "action_build_images",
            "btn-build-agents": "_action_build_agents",
            "btn-build-full": "_action_build_full",
            "btn-ssh-init": "action_init_ssh",
            "btn-sync-gate": "action_sync_gate",
            "btn-new-task": "action_new_task",
            "btn-task-run-cli": "action_run_cli",
            "btn-task-run-web": "action_run_web",
            "btn-task-delete": "action_delete_task",
        }
        method_name = mapping.get(btn_id)
        if not method_name or not hasattr(app, method_name):
            return

        method = getattr(app, method_name)
        result = method()  # type: ignore[misc]
        if inspect.isawaitable(result):
            await result
