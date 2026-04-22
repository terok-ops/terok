# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project list and action bar widgets."""

import inspect
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.message import Message
from textual.widgets import Button, ListItem, ListView, Static

from ...lib.core.projects import BrokenProject, ProjectConfig
from ...lib.core.task_display import GPU_DISPLAY, SECURITY_CLASS_DISPLAY, StatusInfo, has_gpu
from ...lib.util.emoji import render_emoji

# Reuse the existing "failed" status badge for broken-project rows so the
# emoji pipeline (wide glyph, --no-emoji fallback label) stays consistent
# with how other errors render in the TUI.
_BROKEN_BADGE = StatusInfo(label="broken", emoji="❌", color="red")


class ProjectListItem(ListItem):
    """List item that carries project metadata."""

    def __init__(
        self, project_id: str, label: str, generation: int, *, is_broken: bool = False
    ) -> None:
        """Create a project list item with its ID, label, and broken flag."""
        super().__init__(Static(label, markup=False))
        self.project_id = project_id
        self.generation = generation
        self.is_broken = is_broken


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

        def __init__(self, project_id: str, *, is_broken: bool = False) -> None:
            """Create the message with the selected project's ID and broken flag."""
            super().__init__()
            self.project_id = project_id
            self.is_broken = is_broken

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the project list with empty state."""
        super().__init__(**kwargs)
        self.projects: list[ProjectConfig] = []
        self.broken: list[BrokenProject] = []
        self._generation = 0

    def set_projects(
        self,
        projects: list[ProjectConfig],
        broken: list[BrokenProject] | None = None,
    ) -> None:
        """Populate the list with healthy and (optionally) broken projects.

        Broken projects render with an error marker ahead of healthy ones so
        they are impossible to miss; selecting one lets the app show the
        validation error in place of the normal project state panel (#565).
        """
        self.projects = projects
        self.broken = list(broken or [])
        self._generation += 1
        self.clear()

        # Broken first — a damaged config is the thing most likely to need
        # attention, so putting it at the top makes the error indicator the
        # first thing the user sees.
        for bp in self.broken:
            label = f"{render_emoji(_BROKEN_BADGE)} {bp.id} (broken)"
            self.append(ProjectListItem(bp.id, label, self._generation, is_broken=True))

        for proj in projects:
            sec = SECURITY_CLASS_DISPLAY.get(proj.security_class, SECURITY_CLASS_DISPLAY["online"])
            gpu = GPU_DISPLAY[has_gpu(proj)]
            label = f"{render_emoji(sec)}{render_emoji(gpu)} {proj.id}"
            self.append(ProjectListItem(proj.id, label, self._generation))

    def select_project(self, project_id: str) -> None:
        """Select a project by id (healthy or broken)."""
        # Broken rows are inserted before healthy ones; walk the combined
        # sequence in the same order they were appended.
        for idx, bp in enumerate(self.broken):
            if bp.id == project_id:
                self.index = idx
                return
        offset = len(self.broken)
        for idx, proj in enumerate(self.projects):
            if proj.id == project_id:
                self.index = offset + idx
                return

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
        self.post_message(self.ProjectSelected(item.project_id, is_broken=item.is_broken))


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
            yield Button("[yellow]w[/yellow] toad", id="btn-task-run-toad", compact=True)
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
            "btn-task-run-toad": "action_run_toad",
            "btn-task-delete": "action_delete_task",
        }
        method_name = mapping.get(btn_id)
        if not method_name or not hasattr(app, method_name):
            return

        method = getattr(app, method_name)
        result = method()  # type: ignore[misc]
        if inspect.isawaitable(result):
            await result
