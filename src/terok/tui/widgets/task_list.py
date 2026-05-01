# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task list widget and helpers."""

from typing import Any

from textual import events
from textual.message import Message
from textual.widgets import ListItem, ListView, Static

from ...lib.core.task_display import STATUS_DISPLAY, mode_info
from ...lib.orchestration.tasks import TaskMeta
from ...lib.util.emoji import render_emoji
from ...lib.util.text_wrap import wrap_with_hanging_indent


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
        ("n", "app.create_task_from_main", "New"),
        ("H", "app.copy_diff_head", "Diff HEAD"),
        ("P", "app.copy_diff_prev", "Diff PREV"),
        ("A", "app.run_autopilot_from_main", "Autopilot"),
        ("c", "app.run_cli_from_main", "CLI"),
        ("w", "app.run_toad_from_main", "Toad"),
        ("l", "app.login_from_main", "Login"),
        ("f", "app.follow_logs_from_main", "Logs"),
        ("X", "app.delete_task_from_main", "Delete"),
        ("d", "app.shield_down_from_main", "Shield\u2193"),
        ("D", "app.shield_down_all_from_main", "Shield\u2193\u2193"),
        ("s", "app.shield_up_from_main", "Shield\u2191"),
        ("i", "app.shield_interactive_from_main", "Verdicts"),
        ("W", "app.shield_watch_from_main", "Watch"),
        ("C", "app.show_clearance_from_main", "Clearance"),
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
        self._last_label_width = -1

    def _format_task_label(self, task: TaskMeta, width: int = 0) -> str:
        """Build a human-readable label string for a task list entry.

        When *width* is positive, long names wrap at dashes (or anywhere
        if a dashless segment overflows), with continuations indented to
        align with the start of the name.
        """
        m_emoji = render_emoji(mode_info(task.mode))
        s_info = STATUS_DISPLAY.get(task.status, STATUS_DISPLAY["created"])
        s_emoji = render_emoji(s_info)

        extra_parts: list[str] = []
        if task.work_status:
            extra_parts.append(f"work={task.work_status}")
        if task.web_port is not None:
            extra_parts.append(f"port={task.web_port}")

        prefix = f"{task.task_id} {m_emoji} {s_emoji} "
        suffix = f" [{'; '.join(extra_parts)}]" if extra_parts else ""
        return wrap_with_hanging_indent(prefix, task.name, suffix, width)

    def _label_width(self) -> int:
        """Cell width available to a list item's label.

        ``scrollable_content_region`` (rather than ``content_size``)
        subtracts the vertical scrollbar's gutter when one appears —
        otherwise the wrap overshoots by 2 cells the moment enough
        tasks pile up to make the list scroll.
        """
        return self.scrollable_content_region.size.width

    def refresh_labels(self) -> None:
        """Re-render every visible task label at the current width."""
        width = self._label_width()
        for item in self.query(TaskListItem):
            label = self._format_task_label(item.task_meta, width)
            item.query_one(Static).update(label)

    def on_resize(self, event: events.Resize) -> None:
        """Re-wrap labels only when the panel's content width changes."""
        width = self._label_width()
        if width == self._last_label_width:
            return
        self._last_label_width = width
        self.refresh_labels()

    def set_tasks(self, project_id: str, tasks_meta: list[TaskMeta]) -> None:
        """Populate the list from ``TaskMeta`` instances, newest first."""
        existing_states: dict[str, str | None] = {}
        if self.project_id == project_id:
            for task in self.tasks:
                existing_states[task.task_id] = task.container_state

        self.project_id = project_id
        self.tasks = []
        self._generation += 1
        self.clear()

        width = self._label_width()
        for tm in sorted(tasks_meta, key=lambda t: t.created_at or "", reverse=True):
            if tm.task_id in existing_states:
                tm.container_state = existing_states[tm.task_id]
            self.tasks.append(tm)

            label = self._format_task_label(tm, width)
            self.append(TaskListItem(project_id, tm, label, self._generation))

    def mark_deleting(self, task_id: str) -> bool:
        """Mark a task as 'deleting' in the list and refresh its label."""
        found = False

        for tm in self.tasks:
            if tm.task_id == task_id:
                tm.deleting = True
                found = True
                break

        width = self._label_width()
        for item in self.query(TaskListItem):
            if item.task_meta.task_id != task_id:
                continue
            item.task_meta.deleting = True
            label = self._format_task_label(item.task_meta, width)
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
