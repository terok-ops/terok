# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task detail rendering widget and helper."""

from typing import Any

from rich.style import Style
from rich.text import Text
from textual.app import ComposeResult
from textual.widgets import Static

from ...lib.core.config import SHIELD_SECURITY_HINT, get_public_host
from ...lib.core.task_display import STATUS_DISPLAY, mode_info
from ...lib.orchestration.tasks import TaskMeta
from ...lib.util.emoji import render_emoji


def _get_css_variables(widget: Static) -> dict[str, str]:
    """Extract CSS theme variables from a widget's parent app."""
    if widget.app is None:
        return {}
    try:
        return widget.app.get_css_variables()
    except Exception:
        return {}


def render_task_details(
    task: TaskMeta | None,
    project_id: str | None = None,
    image_old: bool | None = None,
    empty_message: str | None = None,
    css_variables: dict[str, str] | None = None,
    show_workspace: bool = True,
    shield_hooks_ok: bool | None = None,
) -> Text:
    """Render task details as a Rich Text object."""
    if task is None:
        return Text(empty_message or "")

    variables = css_variables or {}
    accent_style = Style(color=variables.get("primary", "cyan"))
    warning_style = Style(color=variables.get("warning", "yellow"))

    m_info = mode_info(task.mode)
    m_emoji = render_emoji(m_info)
    mode_display = m_info.label or "Not assigned (choose CLI or Web mode)"

    s_info = STATUS_DISPLAY.get(task.status, STATUS_DISPLAY["created"])

    lines = [
        Text(f"Task ID:   {task.task_id}"),
    ]
    lines.append(Text(f"Name:      {task.name}"))
    lines += [
        Text(f"Status:    {render_emoji(s_info)} {s_info.label}"),
        Text(f"Type:      {m_emoji} {mode_display}"),
    ]
    if task.work_status:
        work_text = task.work_status
        if task.work_message:
            work_text += f' \u2014 "{task.work_message}"'
        lines.append(Text(f"Work:      {work_text}"))
    if show_workspace:
        lines.append(Text(f"Workspace: {task.workspace}"))
    if task.status == "running" and image_old:
        lines.append(Text.assemble("Image:     ", Text("old", style=warning_style)))
    if task.web_port:
        lines.append(
            Text.assemble(
                "Web URL:   ",
                Text(f"http://{get_public_host()}:{task.web_port}/", style=accent_style),
            )
        )
    if task.mode == "cli" and project_id:
        lines.append(
            Text.assemble(
                "Log in:    ",
                Text(f"terok login {project_id} {task.task_id}", style=accent_style),
            )
        )
    if task.unrestricted is not None:
        perm_label = "unrestricted" if task.unrestricted else "restricted"
        lines.append(Text(f"Perms:     {perm_label}"))
    if task.shield_state:
        success_color = variables.get("success", "green")
        error_color = variables.get("error", "red")
        warning_color = variables.get("warning", "yellow")

        container_live = task.container_state == "running"
        # INACTIVE + container not running + hooks healthy → "ready" (no alarm)
        hooks_ok = shield_hooks_ok is not False  # True or unknown (None)
        if task.shield_state == "INACTIVE" and not container_live and hooks_ok:
            lines.append(
                Text.assemble(
                    "Shield:    ",
                    Text("ready", style=Style(dim=True)),
                )
            )
        else:
            shield_colors = {
                "UP": success_color,
                "DOWN": warning_color,
                "INACTIVE": error_color,
                "DISABLED": error_color,
            }
            shield_color = shield_colors.get(task.shield_state, warning_color)
            lines.append(
                Text.assemble(
                    "Shield:    ",
                    Text(task.shield_state.lower(), style=Style(color=shield_color)),
                )
            )
            if task.shield_state in {"DISABLED", "INACTIVE"}:
                lines.append(
                    Text(f"           {SHIELD_SECURITY_HINT}", style=Style(color=error_color))
                )
    if task.mode == "run":
        if task.exit_code is not None:
            lines.append(Text(f"Exit code: {task.exit_code}"))
        if project_id:
            lines.append(
                Text.assemble(
                    "Logs:      ",
                    Text(
                        f"terok task logs {project_id} {task.task_id} -f",
                        style=accent_style,
                    ),
                )
            )

    return Text("\n").join(lines)


class TaskDetails(Static):
    """Panel showing details for the currently selected task."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the task details panel."""
        super().__init__(**kwargs)
        self.current_project_id: str | None = None

    def compose(self) -> ComposeResult:
        """Yield the inner Static widget used for rendered task content."""
        yield Static(id="task-details-content")

    def set_task(
        self,
        task: TaskMeta | None,
        empty_message: str | None = None,
        image_old: bool | None = None,
    ) -> None:
        """Render and display details for the given task (or clear if None)."""
        content = self.query_one("#task-details-content", Static)
        if task is None:
            self.current_project_id = None
        else:
            self.current_project_id = self.app.current_project_id if self.app else None

        # Determine shield hook health from the cached project-level env check.
        hooks_ok: bool | None = None
        try:
            shield_env = getattr(self.app, "_last_shield_env", None)
            if shield_env is not None:
                hooks_ok = shield_env.health == "ok"
        except Exception:
            pass

        rendered = render_task_details(
            task,
            self.current_project_id,
            image_old,
            empty_message,
            _get_css_variables(self),
            show_workspace=False,
            shield_hooks_ok=hooks_ok,
        )
        content.update(rendered)
