# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Task list widget and helpers."""

from typing import Any

from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from ...lib.containers.task_display import STATUS_DISPLAY, mode_emoji
from ...lib.containers.tasks import TaskMeta
from ...lib.util.emoji import draw_emoji


class TaskListItem(ListItem):
    """List item that carries task metadata."""

    def __init__(self, project_id: str, task: TaskMeta, label: str, generation: int) -> None:
        """Create a task list item with its metadata and display label."""
        super().__init__(Static(label, markup=False))
        self.project_id = project_id
        self.task_meta = task
        self.generation = generation


def get_backend_name(task: TaskMeta) -> str | None:
    """Get the backend name for a task.

    Returns the backend name from the task's backend field, or None if not set.
    """
    return task.backend


class TaskList(ListView):
    """Middle pane: per-project tasks."""

    BINDINGS = [
        ("enter", "app.show_task_actions", "Task\u2026"),
        ("H", "app.copy_diff_head", "Diff HEAD"),
        ("P", "app.copy_diff_prev", "Diff PREV"),
        ("A", "app.run_autopilot_from_main", "Autopilot"),
        ("c", "app.run_cli_from_main", "CLI"),
        ("l", "app.login_from_main", "Login"),
        ("f", "app.follow_logs_from_main", "Logs"),
        ("d", "app.delete_task_from_main", "Delete"),
    ]

    class TaskSelected(Message):
        """Posted when a task is highlighted in the list."""

        def __init__(self, project_id: str, task: TaskMeta) -> None:
            """Create the message with the owning project ID and task metadata."""
            super().__init__()
            self.project_id = project_id
            self.task = task

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the task list with empty state."""
        super().__init__(**kwargs)
        self.project_id: str | None = None
        self.tasks: list[TaskMeta] = []
        self._generation = 0

    def _format_task_label(self, task: TaskMeta) -> str:
        """Build a human-readable label string for a task list entry."""
        m_emoji = draw_emoji(mode_emoji(task))
        s_info = STATUS_DISPLAY.get(task.status, STATUS_DISPLAY["created"])
        s_emoji = draw_emoji(s_info.emoji)

        extra_parts: list[str] = []
        if task.web_port is not None:
            extra_parts.append(f"port={task.web_port}")

        label = f"{task.task_id:>3} {m_emoji} {s_emoji} {task.name}"
        if extra_parts:
            label += f" [{'; '.join(extra_parts)}]"
        return label

    def set_tasks(self, project_id: str, tasks_meta: list[TaskMeta]) -> None:
        """Populate the list from ``TaskMeta`` instances."""
        existing_states: dict[str, str | None] = {}
        if self.project_id == project_id:
            for task in self.tasks:
                existing_states[task.task_id] = task.container_state

        self.project_id = project_id
        self.tasks = []
        self._generation += 1
        self.clear()

        for tm in tasks_meta:
            if tm.task_id in existing_states:
                tm.container_state = existing_states[tm.task_id]
            self.tasks.append(tm)

            label = self._format_task_label(tm)
            self.append(TaskListItem(project_id, tm, label, self._generation))

    def mark_deleting(self, task_id: str) -> bool:
        """Mark a task as 'deleting' in the list and refresh its label."""
        found = False

        for tm in self.tasks:
            if tm.task_id == task_id:
                tm.deleting = True
                found = True
                break

        for item in self.query(TaskListItem):
            if item.task_meta.task_id != task_id:
                continue
            item.task_meta.deleting = True
            label = self._format_task_label(item.task_meta)
            item.query_one(Static).update(label)
            found = True

        return found

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:  # type: ignore[override]
        """Update selection immediately when highlight changes."""
        if event.item is None:
            return
        self._post_selected_task(event.item)

    def _post_selected_task(self, item: ListItem | None = None) -> None:
        """Emit a TaskSelected message for the given or currently highlighted item."""
        if self.project_id is None:
            return
        if item is None:
            item = self.highlighted_child
        if not isinstance(item, TaskListItem):
            return
        if item.parent is not self:
            return
        if item.generation != self._generation:
            return
        if item.project_id != self.project_id:
            return
        self.post_message(self.TaskSelected(self.project_id, item.task_meta))
