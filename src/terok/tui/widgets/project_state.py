# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project state rendering widget and helpers."""

from __future__ import annotations

from typing import Any

from rich.style import Style
from rich.text import Text
from textual.widgets import Static

from ...lib.containers.task_display import GPU_DISPLAY, SECURITY_CLASS_DISPLAY, has_gpu
from ...lib.core.projects import ProjectConfig
from ...lib.facade import GateServerStatus, GateStalenessInfo
from ...lib.util.emoji import render_emoji
from .task_detail import _get_css_variables


def render_project_loading(
    project: ProjectConfig | None,
    task_count: int | None = None,
) -> Text:
    """Render project loading state as a Rich Text object."""
    if project is None:
        return Text("No project selected.")

    upstream = project.upstream_url or "-"
    sec = SECURITY_CLASS_DISPLAY.get(project.security_class, SECURITY_CLASS_DISPLAY["online"])
    gpu = GPU_DISPLAY[has_gpu(project)]
    badges = f"{render_emoji(sec)}{render_emoji(gpu)}"
    tasks_line = (
        Text("Tasks:     loading") if task_count is None else Text(f"Tasks:     {task_count}")
    )

    lines = [
        Text(f"Project:   {project.id} {badges}"),
        Text(upstream),
        Text(""),
        Text("Loading details..."),
        tasks_line,
    ]
    return Text("\n").join(lines)


def render_project_details(
    project: ProjectConfig | None,
    state: dict | None,
    task_count: int | None = None,
    staleness: GateStalenessInfo | None = None,
    css_variables: dict[str, str] | None = None,
    gate_server_status: GateServerStatus | None = None,
) -> Text:
    """Render project details as a Rich Text object."""
    if project is None or state is None:
        return Text("No project selected.")

    variables = css_variables or {}
    success_color = variables.get("success", "green")
    error_color = variables.get("error", "red")
    warning_color = variables.get("warning", "yellow")

    status_styles = {
        "yes": Style(color=success_color),
        "no": Style(color=error_color),
        "old": Style(color=warning_color),
        "new": Style(color="blue"),
    }

    def _status_text(value: str) -> Text:
        """Return a styled Rich Text for a status value like 'yes', 'no', or 'old'."""
        style = status_styles.get(value, Style(color=error_color))
        return Text(value, style=style)

    docker_value = "yes" if state.get("dockerfiles") else "no"
    if docker_value == "yes" and state.get("dockerfiles_old"):
        docker_value = "old"
    docker_s = _status_text(docker_value)

    images_value = "yes" if state.get("images") else "no"
    if images_value == "yes" and state.get("images_old"):
        images_value = "old"
    images_s = _status_text(images_value)
    ssh_s = _status_text("yes" if state.get("ssh") else "no")

    # Gate line: server status overrides repo status when server is down
    if gate_server_status is not None and not gate_server_status.running:
        gate_s = Text("gate down", style=Style(color=error_color))
    else:
        gate_value = "yes" if state.get("gate") else "no"
        if (
            gate_value == "yes"
            and staleness is not None
            and not staleness.error
            and staleness.is_stale
        ):
            behind = staleness.commits_behind or 0
            ahead = staleness.commits_ahead or 0
            if ahead > 0 and behind == 0:
                gate_value = "new"
            else:
                gate_value = "old"
        gate_s = _status_text(gate_value)

    tasks_line = (
        Text("Tasks:     unknown") if task_count is None else Text(f"Tasks:     {task_count}")
    )
    upstream = project.upstream_url or "-"
    sec = SECURITY_CLASS_DISPLAY.get(project.security_class, SECURITY_CLASS_DISPLAY["online"])
    gpu = GPU_DISPLAY[has_gpu(project)]
    badges = f"{render_emoji(sec)}{render_emoji(gpu)}"

    dim_style = Style(dim=True)
    # Three-state badge based on YAML config + file existence
    yaml_instructions = project.agent_config.get("instructions")
    try:
        has_file = (project.root / "instructions.md").is_file()
    except (TypeError, AttributeError):
        has_file = False
    has_yaml = yaml_instructions is not None

    if has_yaml or has_file:
        # Check if _inherit is present (or YAML is absent = implicit inherit)
        inherits = not has_yaml
        if isinstance(yaml_instructions, list):
            inherits = "_inherit" in yaml_instructions
        elif isinstance(yaml_instructions, dict):
            inherits = any(
                isinstance(v, list) and "_inherit" in v for v in yaml_instructions.values()
            )
        if inherits:
            instr_s = Text("custom + inherited", style=Style(color=success_color))
        else:
            instr_s = Text("custom only", style=Style(color="cyan"))
    else:
        instr_s = Text("default", style=dim_style)

    lines = [
        Text(f"Project:   {project.id} {badges}"),
        Text(upstream),
        Text(""),
        Text.assemble("Dockerfiles: ", docker_s),
        Text.assemble("Images:      ", images_s),
        Text.assemble("SSH dir:     ", ssh_s),
        Text.assemble("Git gate:    ", gate_s),
        Text.assemble("Instruct:    ", instr_s),
        tasks_line,
    ]

    gate_commit = state.get("gate_last_commit")
    if gate_commit:
        commit_hash = gate_commit.get("commit_hash") or "unknown"
        commit_hash_short = commit_hash[:8] if isinstance(commit_hash, str) else "unknown"
        commit_date = gate_commit.get("commit_date") or "unknown"
        commit_author = gate_commit.get("commit_author") or "unknown"
        commit_message = gate_commit.get("commit_message") or "unknown"
        commit_message_short = (
            commit_message[:50] + ("..." if len(commit_message) > 50 else "")
            if isinstance(commit_message, str)
            else "unknown"
        )

        lines.append(Text(""))
        lines.append(Text("Gate info:"))
        lines.append(Text(f"  Commit:   {commit_hash_short}"))
        lines.append(Text(f"  Date:     {commit_date}"))
        lines.append(Text(f"  Author:   {commit_author}"))
        lines.append(Text(f"  Message:  {commit_message_short}"))

    if staleness is not None:
        lines.append(Text(""))
        lines.append(Text("Upstream status:"))
        if staleness.error:
            lines.append(Text(f"  Error:    {staleness.error}"))
        elif staleness.is_stale:
            behind = staleness.commits_behind or 0
            ahead = staleness.commits_ahead or 0

            if ahead > 0 and behind > 0:
                status_str = f"DIVERGED ({ahead} ahead, {behind} behind) on {staleness.branch}"
            elif ahead > 0:
                status_str = f"AHEAD ({ahead} commits) on {staleness.branch}"
            else:
                behind_str = "unknown" if staleness.commits_behind is None else str(behind)
                status_str = f"BEHIND ({behind_str} commits) on {staleness.branch}"

            lines.append(Text(f"  Status:   {status_str}"))
            upstream_head = staleness.upstream_head[:8] if staleness.upstream_head else "unknown"
            gate_head = staleness.gate_head[:8] if staleness.gate_head else "unknown"
            lines.append(Text(f"  Upstream: {upstream_head}"))
            lines.append(Text(f"  Gate:     {gate_head}"))
        else:
            lines.append(Text(f"  Status:   Up to date on {staleness.branch}"))
            gate_head = staleness.gate_head[:8] if staleness.gate_head else "unknown"
            lines.append(Text(f"  Commit:   {gate_head}"))
        lines.append(Text(f"  Checked:  {staleness.last_checked}"))

    lines.append(Text(""))
    lines.append(Text(f"Config: {project.root}", style=dim_style))

    return Text("\n").join(lines)


class ProjectState(Static):
    """Panel showing detailed information about the active project."""

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the project state panel."""
        super().__init__(**kwargs)

    def set_loading(self, project: ProjectConfig | None, task_count: int | None = None) -> None:
        """Show a loading placeholder while project state is being fetched."""
        self.update(render_project_loading(project, task_count))

    def set_state(
        self,
        project: ProjectConfig | None,
        state: dict | None,
        task_count: int | None = None,
        staleness: GateStalenessInfo | None = None,
        gate_server_status: GateServerStatus | None = None,
    ) -> None:
        """Display fully loaded project details including infrastructure status."""
        self.update(
            render_project_details(
                project,
                state,
                task_count,
                staleness,
                _get_css_variables(self),
                gate_server_status=gate_server_status,
            )
        )
