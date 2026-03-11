#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Full-page and modal Textual screens for the terok TUI."""

from typing import TypedDict

from textual import events, screen
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

try:  # pragma: no cover - optional import for test stubs
    from textual.widgets import OptionList
except Exception:  # pragma: no cover - textual may be a stub module
    OptionList = None  # type: ignore[assignment,misc]

try:  # pragma: no cover - optional import for test stubs
    from textual.widgets.option_list import Option
except Exception:  # pragma: no cover - textual may be a stub module
    Option = None  # type: ignore[assignment,misc]

try:  # pragma: no cover - optional import for test stubs
    from textual.binding import Binding
except Exception:  # pragma: no cover - textual may be a stub module
    Binding = None  # type: ignore[assignment]

try:  # pragma: no cover - optional import for test stubs
    from textual.widgets import TextArea
except Exception:  # pragma: no cover - textual may be a stub module
    TextArea = None  # type: ignore[assignment,misc]

try:  # pragma: no cover - optional import for test stubs
    from textual.widgets import SelectionList
except Exception:  # pragma: no cover - textual may be a stub module
    SelectionList = None  # type: ignore[assignment,misc]

try:  # pragma: no cover - optional import for test stubs
    from textual.widgets import Input
except Exception:  # pragma: no cover - textual may be a stub module
    Input = None  # type: ignore[assignment,misc]

from rich.style import Style
from rich.text import Text

from ..lib.containers.tasks import sanitize_task_name, validate_task_name
from ..lib.core.config import is_experimental
from ..lib.core.projects import ProjectConfig
from ..lib.facade import (
    GateServerStatus,
    GateStalenessInfo,
    check_units_outdated,
    get_gate_base_path,
)
from .widgets import TaskMeta, render_project_details, render_project_loading, render_task_details


def _modal_binding(key: str, action: str, description: str) -> tuple | object:
    """Create a Binding (or plain tuple fallback) for modal screen key shortcuts."""
    if Binding is None:
        return (key, action, description)
    return Binding(key, action, description, show=False)


# ---------------------------------------------------------------------------
# Shared CSS for full-page detail screens
# ---------------------------------------------------------------------------

_DETAIL_SCREEN_CSS = """
    #detail-content {
        height: auto;
        max-height: 50%;
        border: round $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
        margin: 1;
        overflow-y: auto;
    }

    #actions-list {
        height: 1fr;
        margin: 0 1;
    }
"""


# ---------------------------------------------------------------------------
# Gate Server helpers
# ---------------------------------------------------------------------------


def render_gate_server_status(status: GateServerStatus | None) -> Text:
    """Render gate server status details as a Rich Text object."""
    if status is None:
        return Text("Gate server status unknown.")

    ok_style = Style(color="green")
    err_style = Style(color="red")
    warn_style = Style(color="yellow")

    mode_s = Text(status.mode)
    running_s = (
        Text("running", style=ok_style) if status.running else Text("stopped", style=err_style)
    )

    lines = [
        Text.assemble("Mode:      ", mode_s),
        Text.assemble("Status:    ", running_s),
        Text(f"Port:      {status.port}"),
        Text(f"Base path: {get_gate_base_path()}"),
    ]

    outdated = check_units_outdated()
    if outdated:
        lines.append(Text(""))
        lines.append(Text(outdated, style=warn_style))

    if not status.running:
        lines.append(Text(""))
        lines.append(
            Text(
                "The gate server is not running. Use the actions below to install or start it.",
                style=Style(dim=True),
            )
        )

    return Text("\n").join(lines)


# ---------------------------------------------------------------------------
# Gate Server Screen
# ---------------------------------------------------------------------------


class GateServerScreen(screen.Screen[str | None]):
    """Full-page screen for managing the gate server."""

    BINDINGS = [
        _modal_binding("escape", "dismiss", "Back"),
        _modal_binding("q", "dismiss", "Back"),
        _modal_binding("i", "gate_install", "Install systemd socket"),
        _modal_binding("u", "gate_uninstall", "Uninstall systemd units"),
        _modal_binding("s", "gate_start", "Start daemon"),
        _modal_binding("p", "gate_stop", "Stop daemon"),
        _modal_binding("r", "gate_refresh", "Refresh status"),
    ]

    CSS = (
        """
    GateServerScreen {
        layout: vertical;
        background: $background;
    }
    """
        + _DETAIL_SCREEN_CSS
    )

    def __init__(self, status: GateServerStatus | None = None) -> None:
        """Store gate server status for rendering."""
        super().__init__()
        self._status = status

    def compose(self) -> ComposeResult:
        """Build the detail pane and action list for gate server management."""
        detail_pane = Static(id="detail-content")
        detail_pane.border_title = "Git Gate Server"
        detail_pane.border_subtitle = "Esc to close"
        yield detail_pane

        yield OptionList(
            Option("\\[i]nstall systemd socket", id="gate_install"),
            Option("\\[u]ninstall systemd units", id="gate_uninstall"),
            None,
            Option("\\[s]tart daemon", id="gate_start"),
            Option("sto\\[p] daemon", id="gate_stop"),
            None,
            Option("\\[r]efresh status", id="gate_refresh"),
            id="actions-list",
        )

    def on_mount(self) -> None:
        """Render gate server status and focus the action list."""
        self._render_status()
        actions = self.query_one("#actions-list", OptionList)
        actions.focus()

    def _render_status(self) -> None:
        """Update the detail pane with current status."""
        detail_widget = self.query_one("#detail-content", Static)
        detail_widget.update(render_gate_server_status(self._status))

    def _refresh_status(self) -> None:
        """Re-fetch status and update the display."""
        from ..lib.facade import get_server_status

        try:
            self._status = get_server_status()
        except Exception:
            self._status = None
        self._render_status()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Handle action selection from the option list."""
        option_id = event.option_id
        if option_id == "gate_refresh":
            self._refresh_status()
        elif option_id:
            self.dismiss(option_id)

    def action_dismiss(self) -> None:
        """Close the screen without selecting an action."""
        self.dismiss(None)

    def action_gate_install(self) -> None:
        """Trigger systemd socket installation."""
        self.dismiss("gate_install")

    def action_gate_uninstall(self) -> None:
        """Trigger systemd unit uninstallation."""
        self.dismiss("gate_uninstall")

    def action_gate_start(self) -> None:
        """Trigger daemon start."""
        self.dismiss("gate_start")

    def action_gate_stop(self) -> None:
        """Trigger daemon stop."""
        self.dismiss("gate_stop")

    def action_gate_refresh(self) -> None:
        """Refresh the status display."""
        self._refresh_status()


# ---------------------------------------------------------------------------
# Project Details Screen
# ---------------------------------------------------------------------------


class ProjectDetailsScreen(screen.Screen[str | None]):
    """Full-page detail screen for a project with categorized actions."""

    BINDINGS = [
        _modal_binding("escape", "dismiss", "Back"),
        _modal_binding("q", "dismiss", "Back"),
        _modal_binding("i", "project_init", "Full Setup"),
        _modal_binding("g", "sync_gate", "Sync git gate"),
        _modal_binding("d", "generate", "Generate dockerfiles"),
        _modal_binding("b", "build", "Build project image"),
        _modal_binding("r", "build_agents", "Rebuild with agents"),
        _modal_binding("f", "build_full", "Full rebuild no cache"),
        _modal_binding("s", "init_ssh", "Init SSH"),
        _modal_binding("a", "auth", "Authenticate"),
        _modal_binding("I", "edit_instructions", "Edit instructions"),
        _modal_binding("t", "toggle_inherit", "Toggle inherit"),
        _modal_binding("v", "show_resolved", "Show resolved instructions"),
        _modal_binding("D", "delete_project", "Delete project"),
    ]

    CSS = (
        """
    ProjectDetailsScreen {
        layout: vertical;
        background: $background;
    }
    """
        + _DETAIL_SCREEN_CSS
    )

    def __init__(
        self,
        project: ProjectConfig,
        state: dict | None,
        task_count: int | None,
        staleness: GateStalenessInfo | None = None,
    ) -> None:
        """Store the project data to render when the screen is mounted."""
        super().__init__()
        self._project = project
        self._state = state
        self._task_count = task_count
        self._staleness = staleness

    def compose(self) -> ComposeResult:
        """Build the detail pane and categorized action list for a project."""
        detail_pane = Static(id="detail-content")
        detail_pane.border_title = f"Project: {self._project.id}"
        detail_pane.border_subtitle = "Esc to close"
        yield detail_pane

        yield OptionList(
            Option(
                "Full Setup - project-\\[i]nit  (ssh + generate + build + gate-sync)",
                id="project_init",
            ),
            Option("sync \\[g]it gate", id="sync_gate"),
            None,
            Option("generate \\[d]ockerfiles", id="generate"),
            Option("\\[b]uild project image", id="build"),
            Option("\\[r]ebuild with agents", id="build_agents"),
            Option("\\[f]ull rebuild no cache", id="build_full"),
            Option("initialize \\[s]sh", id="init_ssh"),
            None,
            Option("\\[a]uthenticate...", id="auth"),
            None,
            Option("edit \\[I]nstructions", id="edit_instructions"),
            Option("\\[t]oggle instructions inherit", id="toggle_inherit"),
            Option("\\[v]iew resolved instructions", id="show_resolved"),
            None,
            Option("\\[D]elete project", id="delete_project"),
            id="actions-list",
        )

    def on_mount(self) -> None:
        """Render project details and focus the action list."""
        detail_widget = self.query_one("#detail-content", Static)
        if self._state is not None:
            rendered = render_project_details(
                self._project, self._state, self._task_count, self._staleness
            )
        else:
            rendered = render_project_loading(self._project, self._task_count)
        detail_widget.update(rendered)
        actions = self.query_one("#actions-list", OptionList)
        actions.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the chosen action ID, or open the auth sub-modal."""
        option_id = event.option_id
        if option_id == "auth":
            self._open_auth_modal()
        elif option_id:
            self.dismiss(option_id)

    def _open_auth_modal(self) -> None:
        """Push the authentication provider selection modal."""
        self.app.push_screen(AuthActionsScreen(), self._on_auth_result)

    def _on_auth_result(self, result: str | None) -> None:
        """Forward the selected auth action from the sub-modal as this screen's result."""
        if result:
            self.dismiss(result)

    # Action methods invoked by BINDINGS
    def action_dismiss(self) -> None:
        """Close the screen without selecting an action."""
        self.dismiss(None)

    def action_project_init(self) -> None:
        """Trigger the full project initialization pipeline."""
        self.dismiss("project_init")

    def action_sync_gate(self) -> None:
        """Trigger git gate synchronization."""
        self.dismiss("sync_gate")

    def action_generate(self) -> None:
        """Trigger Dockerfile generation."""
        self.dismiss("generate")

    def action_build(self) -> None:
        """Trigger project image build."""
        self.dismiss("build")

    def action_build_agents(self) -> None:
        """Trigger agent image rebuild."""
        self.dismiss("build_agents")

    def action_build_full(self) -> None:
        """Trigger a full no-cache rebuild."""
        self.dismiss("build_full")

    def action_init_ssh(self) -> None:
        """Trigger SSH directory initialization."""
        self.dismiss("init_ssh")

    def action_auth(self) -> None:
        """Open the authenticate agents and tools modal."""
        self._open_auth_modal()

    def action_edit_instructions(self) -> None:
        """Open instructions for editing."""
        self.dismiss("edit_instructions")

    def action_toggle_inherit(self) -> None:
        """Toggle instructions inheritance mode."""
        self.dismiss("toggle_inherit")

    def action_show_resolved(self) -> None:
        """Show fully resolved instructions."""
        self.dismiss("show_resolved")

    def action_delete_project(self) -> None:
        """Trigger project deletion."""
        self.dismiss("delete_project")


# ---------------------------------------------------------------------------
# Auth Actions Modal (sub-modal of ProjectDetailsScreen)
# ---------------------------------------------------------------------------


class AuthActionsScreen(screen.ModalScreen[str | None]):
    """Small modal for authenticating agents and tools.

    Options are built dynamically from ``AUTH_PROVIDERS``.
    Number keys (1-9) act as shortcuts for the corresponding list entry.
    """

    BINDINGS = [
        _modal_binding("escape", "dismiss", "Cancel"),
        _modal_binding("q", "dismiss", "Cancel"),
    ]

    CSS = """
    AuthActionsScreen {
        align: center middle;
    }

    #auth-dialog {
        width: 50;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #auth-actions-list {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the numbered list of authentication providers."""
        from ..lib.facade import AUTH_PROVIDERS

        providers = list(AUTH_PROVIDERS.values())
        options: list[Option | None] = [
            Option(f"\\[{i}] {p.label}", id=f"auth_{p.name}")
            for i, p in enumerate(providers, 1)
            if i <= 9
        ]
        next_num = len(providers) + 1
        options.append(None)
        import_label = (
            f"\\[{next_num}] Import OpenCode config" if next_num <= 9 else "Import OpenCode config"
        )
        options.append(Option(import_label, id="import_opencode_config"))
        with Vertical(id="auth-dialog") as dialog:
            yield OptionList(*options, id="auth-actions-list")
        dialog.border_title = "Authenticate agents and tools"
        dialog.border_subtitle = "Esc to close"

    def on_mount(self) -> None:
        """Focus the auth provider list on mount."""
        actions = self.query_one("#auth-actions-list", OptionList)
        actions.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the selected provider's action ID."""
        if event.option_id:
            self.dismiss(event.option_id)

    def on_key(self, event: events.Key) -> None:
        """Handle number-key shortcuts (1-9) to select a provider or import."""
        from ..lib.facade import AUTH_PROVIDERS

        if event.character and event.character.isdigit():
            idx = int(event.character) - 1
            providers = list(AUTH_PROVIDERS.values())
            if 0 <= idx < min(len(providers), 9):
                self.dismiss(f"auth_{providers[idx].name}")
                event.stop()
            elif idx == len(providers) and idx < 9:
                self.dismiss("import_opencode_config")
                event.stop()

    def action_dismiss(self) -> None:
        """Close the auth modal without selecting a provider."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# OpenCode Config Import Screen
# ---------------------------------------------------------------------------


class OpenCodeConfigScreen(screen.ModalScreen[str | None]):
    """Modal for entering a file path to import as OpenCode config.

    Validates that the file exists and contains valid JSON, then copies it
    to the shared ``_opencode-config`` mount.  Dismisses with the
    destination path on success, or ``None`` if cancelled.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    OpenCodeConfigScreen {
        align: center middle;
    }

    #opencode-config-dialog {
        width: 70;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #opencode-config-input {
        margin-bottom: 1;
    }

    #opencode-config-buttons {
        height: auto;
        align-horizontal: right;
    }

    #opencode-config-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the file path input and OK/Cancel buttons."""
        with Vertical(id="opencode-config-dialog") as dialog:
            yield Input(
                placeholder="/path/to/opencode.json",
                id="opencode-config-input",
            )
            with Horizontal(id="opencode-config-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Import", id="btn-import", variant="primary")
        dialog.border_title = "Import OpenCode Config"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the file path input for immediate typing."""
        inp = self.query_one("#opencode-config-input", Input)
        inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Import or Cancel button clicks."""
        if event.button.id == "btn-import":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:  # type: ignore[name-defined]
        """Accept on Enter key press."""
        self._submit()

    def _submit(self) -> None:
        """Validate the file path and copy the config to the shared mount."""
        import json
        import shutil
        from pathlib import Path

        from ..lib.core.config import get_envs_base_dir

        inp = self.query_one("#opencode-config-input", Input)
        raw = inp.value.strip()
        if not raw:
            self.notify("File path cannot be empty.")
            return

        src = Path(raw).expanduser()
        if not src.is_file():
            self.notify(f"File not found: {src}")
            return

        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
            self.notify(f"Cannot read config: {e}")
            return
        if not isinstance(data, dict):
            self.notify("Invalid config: expected a JSON object")
            return

        try:
            dest_dir = get_envs_base_dir() / "_opencode-config"
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / "opencode.json"
            shutil.copy2(str(src), str(dest))
        except OSError as e:
            self.notify(f"Copy failed: {e}")
            return

        self.dismiss(str(dest))

    def action_cancel(self) -> None:
        """Cancel the import and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Autopilot Prompt Screen
# ---------------------------------------------------------------------------


class SubagentInfo(TypedDict):
    """Metadata for a single sub-agent shown in the autopilot selection screen.

    Sub-agents are provider-specific assistants (currently Claude only) that can
    be included in an autopilot run via ``--agents`` JSON.

    Attributes:
        name: Unique sub-agent identifier used as the dict key in ``--agents`` JSON.
        description: Human-readable summary of the sub-agent's purpose.
        default: Whether the sub-agent is pre-selected when the selection screen opens.
    """

    name: str
    description: str
    default: bool


class AutopilotPromptScreen(screen.ModalScreen[str | None]):
    """Modal for entering an autopilot prompt.

    A modal dialog that prompts the user to enter a prompt for the autopilot
    (headless Claude) mode. The user can enter their prompt in a text area and
    submit it or cancel.

    The screen dismisses with the prompt string if submitted, or ``None``
    if cancelled (e.g. via Escape or the Cancel button).
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    AutopilotPromptScreen {
        align: center middle;
    }

    #autopilot-dialog {
        width: 80;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #prompt-area {
        height: 8;
        margin-bottom: 1;
    }

    #prompt-buttons {
        height: auto;
        align-horizontal: right;
    }

    #prompt-buttons Button {
        margin-left: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Build the prompt text area and submit/cancel buttons."""
        with Vertical(id="autopilot-dialog") as dialog:
            yield TextArea(id="prompt-area")
            with Horizontal(id="prompt-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Run ▶", id="btn-run", variant="primary")
        dialog.border_title = "Autopilot Prompt"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the text area for immediate typing."""
        area = self.query_one("#prompt-area", TextArea)
        area.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle Run or Cancel button clicks."""
        if event.button.id == "btn-run":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def _submit(self) -> None:
        """Dismiss with the entered prompt text if non-empty."""
        area = self.query_one("#prompt-area", TextArea)
        text = area.text.strip()
        if text:
            self.dismiss(text)

    def action_cancel(self) -> None:
        """Cancel the prompt and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Agent Selection Screen (agent + optional sub-agents)
# ---------------------------------------------------------------------------


class AgentSelectionScreen(screen.ModalScreen[tuple[str, list[str] | None] | None]):
    """Combined modal for selecting the autopilot agent and optional sub-agents.

    The top section lists all registered headless agents (Claude, Codex, etc.)
    with the project default marked ``*``.  The bottom section shows sub-agent
    checkboxes when the project defines them (currently Claude-only).

    Number keys (1-9) act as shortcuts for agent selection.

    Dismisses with ``(agent_name, selected_subagents_or_None)`` on OK,
    or ``None`` if cancelled.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    AgentSelectionScreen {
        align: center middle;
    }

    #agent-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #agent-list {
        height: auto;
        max-height: 10;
        margin-bottom: 1;
    }

    #subagent-label {
        height: 1;
        margin-top: 1;
        color: $text-muted;
    }

    #subagent-selection {
        height: auto;
        max-height: 8;
        margin-bottom: 1;
    }

    #agent-buttons {
        height: auto;
        align-horizontal: right;
    }

    #agent-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(
        self,
        subagents: list[SubagentInfo] | None = None,
        default_agent: str = "claude",
    ) -> None:
        """Create the combined agent + sub-agent selection screen.

        Args:
            subagents: Optional list of sub-agent dicts. When non-empty a
                checkbox section is shown below the agent list.
            default_agent: Name of the project's default agent (pre-highlighted
                and marked with ``*``).
        """
        super().__init__()
        self._subagents = subagents or []

        from ..lib.containers.headless_providers import HEADLESS_PROVIDERS

        if default_agent in HEADLESS_PROVIDERS:
            self._default_agent = default_agent
        else:
            self._default_agent = next(iter(HEADLESS_PROVIDERS))
        self._selected_agent: str = self._default_agent

    def compose(self) -> ComposeResult:
        """Build the agent list, optional sub-agent checkboxes, and buttons."""
        from ..lib.containers.headless_providers import HEADLESS_PROVIDERS

        with Vertical(id="agent-dialog") as dialog:
            options = []
            for i, provider in enumerate(HEADLESS_PROVIDERS.values(), 1):
                marker = " *" if provider.name == self._default_agent else ""
                options.append(Option(f"\\[{i}] {provider.label}{marker}", id=provider.name))
            yield OptionList(*options, id="agent-list")

            if self._subagents:
                yield Static("Sub-agents (Claude only):", id="subagent-label")
                items = []
                for sa in self._subagents:
                    name = sa.get("name", "unnamed")
                    desc = sa.get("description", "")
                    label = f"{name}: {desc}" if desc else name
                    initial = bool(sa.get("default", False))
                    items.append((label, name, initial))
                yield SelectionList(*items, id="subagent-selection")

            with Horizontal(id="agent-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("OK", id="btn-ok", variant="primary")
        dialog.border_title = "Select Agent"
        dialog.border_subtitle = "Esc to cancel  (* = default)"

    def on_mount(self) -> None:
        """Focus the agent list and highlight the default entry."""
        agent_list = self.query_one("#agent-list", OptionList)
        from ..lib.containers.headless_providers import HEADLESS_PROVIDERS

        for idx, name in enumerate(HEADLESS_PROVIDERS):
            if name == self._default_agent:
                agent_list.highlighted = idx
                break
        agent_list.focus()

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        """Track the currently highlighted agent as the selection."""
        if event.option_id:
            self._selected_agent = event.option_id

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Confirm agent choice on Enter and advance focus."""
        if event.option_id:
            self._selected_agent = event.option_id
        if self._subagents:
            self.query_one("#subagent-selection", SelectionList).focus()
        else:
            self.query_one("#btn-ok", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle OK or Cancel button clicks."""
        if event.button.id == "btn-ok":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def _submit(self) -> None:
        """Dismiss with the selected agent and sub-agent list."""
        agent = self._selected_agent
        subagents: list[str] | None = None
        if self._subagents:
            sel = self.query_one("#subagent-selection", SelectionList)
            subagents = list(sel.selected)
        self.dismiss((agent, subagents))

    def on_key(self, event: events.Key) -> None:
        """Handle number-key shortcuts (1-9) to select an agent."""
        from ..lib.containers.headless_providers import HEADLESS_PROVIDERS

        if event.character and event.character.isdigit():
            idx = int(event.character) - 1
            providers = list(HEADLESS_PROVIDERS.values())
            if 0 <= idx < len(providers):
                self._selected_agent = providers[idx].name
                agent_list = self.query_one("#agent-list", OptionList)
                agent_list.highlighted = idx
                event.stop()

    def action_cancel(self) -> None:
        """Cancel agent selection and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Task Name Screen (name input for new task or rename)
# ---------------------------------------------------------------------------


class TaskNameScreen(screen.ModalScreen[str | None]):
    """Modal for entering or editing a task name.

    Dismisses with the name string if submitted, or ``None`` if cancelled.
    Pre-fills the input with a default (generated or current) name.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    TaskNameScreen {
        align: center middle;
    }

    #name-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $primary;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #name-input {
        margin-bottom: 1;
    }

    #name-buttons {
        height: auto;
        align-horizontal: right;
    }

    #name-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, default_name: str = "") -> None:
        """Create the name screen with a pre-filled default name."""
        super().__init__()
        self._default_name = default_name

    def compose(self) -> ComposeResult:
        """Build the name input field and OK/Cancel buttons."""
        with Vertical(id="name-dialog") as dialog:
            yield Input(
                value=self._default_name,
                placeholder="task-name",
                id="name-input",
            )
            with Horizontal(id="name-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("OK", id="btn-ok", variant="primary")
        dialog.border_title = "Task Name"
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the name input for immediate editing."""
        inp = self.query_one("#name-input", Input)
        inp.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle OK or Cancel button clicks."""
        if event.button.id == "btn-ok":
            self._submit()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: "Input.Submitted") -> None:  # type: ignore[name-defined]
        """Accept the name on Enter key press."""
        self._submit()

    def _submit(self) -> None:
        """Validate and dismiss with the sanitized name, or show an error."""
        inp = self.query_one("#name-input", Input)
        raw = inp.value.strip()
        # Fall back to default if field is blank, then run full validation pipeline
        candidate = raw or self._default_name
        if not candidate:
            self.notify("Name cannot be empty.")
            return
        sanitized = sanitize_task_name(candidate)
        if sanitized is None:
            self.notify("Invalid name: must contain at least one alphanumeric character.")
            return
        err = validate_task_name(sanitized)
        if err:
            self.notify(f"Invalid name: {err}.")
            return
        self.dismiss(sanitized)

    def action_cancel(self) -> None:
        """Cancel the name input and dismiss without a result."""
        self.dismiss(None)


# ---------------------------------------------------------------------------
# Task Details Screen
# ---------------------------------------------------------------------------


class ConfirmDeleteScreen(screen.ModalScreen[bool]):
    """Modal confirmation dialog for destructive operations.

    Dismisses with ``True`` if the user confirms, ``False`` otherwise.
    """

    BINDINGS = [
        _modal_binding("escape", "cancel", "Cancel"),
    ]

    CSS = """
    ConfirmDeleteScreen {
        align: center middle;
    }

    #confirm-dialog {
        width: 60;
        height: auto;
        max-height: 80%;
        border: heavy $error;
        border-title-align: right;
        border-subtitle-align: left;
        background: $surface;
        padding: 1;
    }

    #confirm-message {
        margin-bottom: 1;
    }

    #confirm-buttons {
        height: auto;
        align-horizontal: right;
    }

    #confirm-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, message: str, title: str = "Confirm Delete") -> None:
        """Create a confirmation dialog with a warning message."""
        super().__init__()
        self._message = message
        self._title = title

    def compose(self) -> ComposeResult:
        """Build the confirmation message and Yes/Cancel buttons."""
        with Vertical(id="confirm-dialog") as dialog:
            yield Static(self._message, id="confirm-message", markup=False)
            with Horizontal(id="confirm-buttons"):
                yield Button("Cancel", id="btn-cancel", variant="default")
                yield Button("Delete", id="btn-confirm", variant="error")
        dialog.border_title = self._title
        dialog.border_subtitle = "Esc to cancel"

    def on_mount(self) -> None:
        """Focus the cancel button by default (safe choice)."""
        self.query_one("#btn-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks."""
        if event.button.id == "btn-confirm":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        """Cancel and dismiss without confirming."""
        self.dismiss(False)


class TaskDetailsScreen(screen.Screen[str | None]):
    """Full-page detail screen for a task with categorized actions."""

    # Only escape/q use BINDINGS. Other keys require case-sensitive
    # dispatch (e.g. shift-N vs n) which Textual BINDINGS cannot express,
    # so they are handled in on_key instead.
    BINDINGS = [
        _modal_binding("escape", "dismiss", "Back"),
        _modal_binding("q", "dismiss", "Back"),
    ]

    CSS = (
        """
    TaskDetailsScreen {
        layout: vertical;
        background: $background;
    }
    """
        + _DETAIL_SCREEN_CSS
    )

    def __init__(
        self,
        task: TaskMeta | None,
        has_tasks: bool,
        project_id: str,
        image_old: bool | None = None,
    ) -> None:
        """Store task data and context for rendering when the screen mounts."""
        super().__init__()
        self._task_meta = task
        self._has_tasks = has_tasks
        self._project_id = project_id
        self._image_old = image_old

    def compose(self) -> ComposeResult:
        """Build the detail pane and categorized action list for a task."""
        detail_pane = Static(id="detail-content")
        title = "Task Details"
        if self._task_meta:
            backend = self._task_meta.backend or self._task_meta.mode or "unknown"
            title = f"Task: {self._task_meta.task_id} ({backend})"
        detail_pane.border_title = title
        detail_pane.border_subtitle = "Esc to close"
        yield detail_pane

        options: list[Option | None] = [
            Option("Start CLI task  \\[N]  (new task + run CLI)", id="task_start_cli"),
        ]
        if is_experimental():
            options.append(Option("Start \\[W]eb task  (new task + run Web)", id="task_start_web"))
        options.append(
            Option("Start \\[A]utopilot task  (new task + run headless)", id="task_start_autopilot")
        )
        if self._has_tasks:
            options.append(Option("\\[l]ogin to container", id="login"))
            if self._task_meta and self._task_meta.mode:
                options.append(Option("view \\[f]ormatted logs", id="follow_logs"))
            options.append(None)
            options.append(Option("run \\[c]li agent", id="cli"))
            if is_experimental():
                options.append(Option("run \\[w]eb UI", id="web"))
            options.append(Option("\\[r]estart container", id="restart"))
            if (
                self._task_meta
                and self._task_meta.mode == "run"
                and self._task_meta.exit_code is not None
            ):
                options.append(Option("follow \\[u]p with new prompt", id="followup"))
            options.append(None)
            options.append(Option("Copy diff vs \\[H]EAD", id="diff_head"))
            options.append(Option("Copy diff vs \\[P]REV", id="diff_prev"))
            options.append(None)
            options.append(Option("re\\[n]ame task", id="rename"))
        options.append(None)
        options.append(Option("New task (no run)  \\[C]", id="new"))

        yield OptionList(*options, id="actions-list")

    def on_mount(self) -> None:
        """Render task details and focus the action list."""
        detail_widget = self.query_one("#detail-content", Static)
        rendered = render_task_details(
            self._task_meta,
            project_id=self._project_id,
            image_old=self._image_old,
            empty_message="No task selected.",
        )
        detail_widget.update(rendered)
        actions = self.query_one("#actions-list", OptionList)
        actions.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the chosen action ID."""
        option_id = event.option_id
        if option_id:
            self.dismiss(option_id)

    def on_key(self, event: events.Key) -> None:
        """Handle case-sensitive shortcut keys for task actions."""
        key = event.key  # case-sensitive

        if key.lower() in ("escape", "q"):
            self.dismiss(None)
            event.stop()
            return

        # Shift keys (uppercase) — N/A/C always available, H/P require tasks
        shift_map: dict[str, str] = {
            "N": "task_start_cli",
            "A": "task_start_autopilot",
            "C": "new",
            "H": "diff_head",
            "P": "diff_prev",
        }
        if is_experimental():
            shift_map["W"] = "task_start_web"
        if key in shift_map:
            if key in ("H", "P") and not self._has_tasks:
                return
            self.dismiss(shift_map[key])
            event.stop()
            return

        # Lowercase keys — all require tasks to exist
        lower_map: dict[str, str] = {
            "d": "delete",
            "c": "cli",
            "r": "restart",
            "l": "login",
            "u": "followup",
            "n": "rename",
        }
        if is_experimental():
            lower_map["w"] = "web"
        if key in lower_map:
            if not self._has_tasks:
                return
            self.dismiss(lower_map[key])
            event.stop()
            return

        # 'f' (view formatted logs) — available for all modes with containers
        if key == "f":
            if self._has_tasks and self._task_meta and self._task_meta.mode:
                self.dismiss("follow_logs")
                event.stop()

    def action_dismiss(self) -> None:
        """Close the task details screen without selecting an action."""
        self.dismiss(None)
