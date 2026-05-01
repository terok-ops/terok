# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task detail rendering widget and helper."""

from typing import Any

from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.widgets import Static

from ...lib.core.config import SHIELD_SECURITY_HINT, get_public_host
from ...lib.core.task_display import STATUS_DISPLAY, mode_info
from ...lib.orchestration.tasks import TaskMeta
from ...lib.util.emoji import render_emoji
from ...lib.util.net import url_host
from ...lib.util.text_wrap import wrap_with_hanging_indent


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
    is_web: bool = False,
    width: int = 0,
) -> Text:
    """Render task details as a Rich Text object.

    *is_web* is the caller's ``app.is_web`` — when False (the TUI is
    running in a real terminal) we emit OSC 8 hyperlinks so wrapped
    URL lines stay one clickable link.  In web mode we skip OSC 8
    because xterm.js's link handler shows a "could be dangerous"
    confirmation dialog for every click; the ``@click`` meta below
    keeps that path working without the dialog.
    """
    if task is None:
        return Text(empty_message or "")

    variables = css_variables or {}
    accent_style = Style(color=variables.get("primary", "cyan"))
    warning_style = Style(color=variables.get("warning", "yellow"))

    m_info = mode_info(task.mode)
    m_emoji = render_emoji(m_info)
    # Empty label for an unset mode lets the cricket emoji speak for
    # itself — modes are picked via the Start CLI/Toad/Autopilot menu,
    # not from this panel.
    mode_display = m_info.label

    s_info = STATUS_DISPLAY.get(task.status, STATUS_DISPLAY["created"])

    lines = [
        Text(f"Task ID:   {task.task_id}"),
    ]
    lines.append(Text(wrap_with_hanging_indent("Name:      ", task.name, "", width)))
    type_line = f"Type:      {m_emoji} {mode_display}".rstrip()
    lines += [
        Text(f"Status:    {render_emoji(s_info)} {s_info.label}"),
        Text(type_line),
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
        base_url = f"http://{url_host(get_public_host())}:{task.web_port}/"
        # Token in the query is what Caddy trades for its auth cookie.
        # Render the full URL verbatim so users without OSC-8 support
        # (and copy-paste) still get the tokenised URL.
        link_url = f"{base_url}?token={task.web_token}" if task.web_token else base_url
        # ``@click`` meta is the in-Textual click dispatcher — it routes
        # through ``open_url`` and avoids xterm.js's OSC 8 confirm
        # dialog when the TUI is served via textual-serve.  Outside web
        # mode (real terminal underneath) we *also* attach Rich's
        # ``link=`` so the host terminal sees the OSC 8 hyperlink: with
        # the shared ``id=`` Rich emits, wrapped URL segments stitch
        # back into one clickable link instead of breaking at the panel
        # edge.
        click_style = accent_style + Style.from_meta({"@click": f"open_link({link_url!r})"})
        if not is_web:
            click_style = click_style + Style(link=link_url)
        lines.append(Text.assemble("Web URL:   ", Text(link_url, style=click_style)))
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
        self._current_task: TaskMeta | None = None
        self._current_empty_message: str | None = None
        self._current_image_old: bool | None = None
        self._last_render_width = -1

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
        if task is None:
            self.current_project_id = None
        else:
            self.current_project_id = self.app.current_project_id if self.app else None

        self._current_task = task
        self._current_empty_message = empty_message
        self._current_image_old = image_old
        self._last_render_width = -1  # task changed — force re-render
        self._redraw_content()

    def on_resize(self, event: events.Resize) -> None:
        """Re-render only when the panel's content width changes."""
        width = self.query_one("#task-details-content", Static).content_size.width
        if width == self._last_render_width:
            return
        self._redraw_content()

    def _redraw_content(self) -> None:
        """Render the cached task into the inner Static at the current width.

        Named ``_redraw_content`` (not ``_render``) because Textual's
        :class:`~textual.widget.Widget` already defines ``_render(self) ->
        Visual`` as part of its rendering pipeline; shadowing that method
        crashes the renderer with ``'NoneType' has no attribute
        'render_strips'`` when this Widget gets repainted.
        """
        content = self.query_one("#task-details-content", Static)
        self._last_render_width = content.content_size.width

        # Determine shield hook health from the cached project-level env check.
        hooks_ok: bool | None = None
        try:
            shield_env = getattr(self.app, "_last_shield_env", None)
            if shield_env is not None:
                hooks_ok = shield_env.health == "ok"
        except Exception:
            pass

        rendered = render_task_details(
            self._current_task,
            self.current_project_id,
            self._current_image_old,
            self._current_empty_message,
            _get_css_variables(self),
            show_workspace=False,
            shield_hooks_ok=hooks_ok,
            is_web=bool(self.app and self.app.is_web),
            width=self._last_render_width,
        )
        content.update(rendered)

    def action_open_link(self, href: str) -> None:
        """Open *href* via the app's URL driver — no xterm.js confirm dialog."""
        self.app.open_url(href)
