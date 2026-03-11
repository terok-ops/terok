#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Terok TUI application built on Textual."""

import os
import sys


def enable_pycharm_debugger():
    """Attach the PyCharm remote debugger when PYCHARM_DEBUG is set."""
    import os

    if os.getenv("PYCHARM_DEBUG"):
        import pydevd_pycharm

        pydevd_pycharm.settrace(
            host="localhost",
            port=5678,
            suspend=False,  # or True if you want it to break immediately
        )


# Try to detect whether 'textual' is available. We avoid importing it or the
# widgets module at import time so the package can be installed without the
# optional TUI dependencies.
try:  # pragma: no cover - simple availability probe
    import importlib.util

    _HAS_TEXTUAL = importlib.util.find_spec("textual") is not None
except Exception:  # pragma: no cover - textual not installed
    _HAS_TEXTUAL = False


if _HAS_TEXTUAL:
    # Import textual and our widgets only when available
    from dataclasses import dataclass

    from textual import on
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Static
    from textual.worker import Worker, WorkerState

    from ..lib.containers.tasks import get_tasks
    from ..lib.core.config import get_tui_default_tmux, set_experimental, state_root
    from ..lib.core.projects import ProjectConfig, list_projects, load_project

    # Import version info function (shared with CLI --version)
    from ..lib.core.version import (
        get_version_info as _get_version_info,
        short_version as _short_version,
    )
    from ..lib.facade import (
        GateServerStatus,
        GateStalenessInfo,
        GitGate,
        get_project_state,
        get_server_status,
        is_task_image_old,
    )

    @dataclass(frozen=True)
    class ProjectStateResult:
        """Result of loading project infrastructure state in a background thread."""

        project_id: str
        project: ProjectConfig | None = None
        state: dict | None = None
        staleness: GateStalenessInfo | None = None
        gate_server_status: GateServerStatus | None = None
        error: str | None = None

    from .clipboard import get_clipboard_helper_status
    from .polling import PollingMixin
    from .project_actions import ProjectActionsMixin
    from .screens import GateServerScreen, ProjectDetailsScreen, TaskDetailsScreen
    from .task_actions import TaskActionsMixin
    from .widgets import (
        ProjectList,
        ProjectState,
        TaskDetails,
        TaskList,
        TaskListItem,
        TaskMeta,
    )

    # -- Dispatch tables mapping action IDs to handler method names ----------
    # These are the single source of truth for action routing.  Both
    # _handle_project_action and _handle_task_action do a dict lookup here
    # instead of maintaining long if/elif chains.

    PROJECT_ACTION_HANDLERS: dict[str, str] = {
        "project_init": "_action_project_init",
        "generate": "action_generate_dockerfiles",
        "build": "action_build_images",
        "build_agents": "_action_build_agents",
        "build_full": "_action_build_full",
        "init_ssh": "action_init_ssh",
        "sync_gate": "_action_sync_gate",
        "edit_instructions": "_action_edit_instructions",
        "toggle_inherit": "_action_toggle_instructions_inherit",
        "show_resolved": "_action_show_resolved_instructions",
        "import_opencode_config": "_action_import_opencode_config",
        "delete_project": "_action_delete_project",
    }

    GATE_SERVER_ACTION_HANDLERS: dict[str, str] = {
        "gate_install": "_action_gate_install",
        "gate_uninstall": "_action_gate_uninstall",
        "gate_start": "_action_gate_start",
        "gate_stop": "_action_gate_stop",
    }

    TASK_ACTION_HANDLERS: dict[str, str] = {
        "task_start_cli": "_action_task_start_cli",
        "task_start_web": "_action_task_start_web",
        "task_start_autopilot": "_action_task_start_autopilot",
        "new": "action_new_task",
        "cli": "action_run_cli",
        "web": "_action_run_web",
        "delete": "action_delete_task",
        "restart": "_action_restart_task",
        "followup": "_action_task_followup",
        "diff_head": "action_copy_diff_head",
        "diff_prev": "action_copy_diff_prev",
        "login": "_action_login",
        "follow_logs": "_action_follow_logs",
        "rename": "_action_rename_task",
    }

    class TerokTUI(PollingMixin, ProjectActionsMixin, TaskActionsMixin, App):
        """Redesigned TUI frontend for terok core modules."""

        CSS_PATH = None

        # Layout rules for the new streamlined design with borders
        CSS = """
        Screen {
            layout: vertical;
            background: $background;
        }

        #main {
            height: 1fr;
            background: $background;
        }

        /* Main container borders */
        #left-pane {
            width: 1fr;
            padding: 1;
            background: $background;
        }

        #right-pane {
            width: 1fr;
            padding: 1;
            background: $background;
        }

        /* Projects section with embedded title */
        #project-list {
            border: round $primary;
            border-title-align: right;
            background: $surface;
            height: auto;
            max-height: 8;
        }

        /* Project details section */
        #project-state {
            border: round $primary;
            border-title-align: right;
            background: $background;
            height: 1fr;
            min-height: 5;
            margin-top: 1;
        }

        /* Tasks section with embedded title */
        #task-list {
            border: round $primary;
            border-title-align: right;
            background: $surface;
            height: auto;
            max-height: 8;
        }

        /* Task details section */
        #task-details {
            border: round $primary;
            border-title-align: right;
            background: $background;
            height: 1fr;
            min-height: 5;
            margin-top: 1;
        }

        /* Task details internal layout */
        #task-details-content {
            height: 1fr;
        }
        """

        BINDINGS = [
            ("q", "quit", "Quit"),
        ]

        def __init__(self) -> None:
            """Initialize the TUI, setting up internal state and dynamic title."""
            super().__init__()
            # Set dynamic title with version and branch info
            self._update_title()

            self.current_project_id: str | None = None
            self.current_task: TaskMeta | None = None
            self._projects_by_id: dict[str, ProjectConfig] = {}
            self._last_task_count: int | None = None
            # Upstream polling state
            self._staleness_info: GateStalenessInfo | None = None
            self._polling_timer = None
            self._polling_project_id: str | None = None  # Project ID the timer was started for
            self._last_notified_stale: bool = False  # Track if we already notified about staleness
            self._auto_sync_cooldown: dict[str, float] = {}  # Per-project cooldown timestamps
            # Container status polling state
            self._container_status_timer = None
            # Gate server polling state
            self._gate_server_timer = None
            self._last_gate_server_running: bool | None = None
            self._last_gate_server_status: GateServerStatus | None = None
            # Cached state for detail screens
            self._last_project_state: dict | None = None
            self._last_image_old: bool | None = None
            # Selection persistence
            self._last_selected_project: str | None = None
            self._last_selected_tasks: dict[str, str] = {}  # project_id -> task_id

        def _update_title(self):
            """Update the TUI title with version and branch information."""
            version, branch_name = _get_version_info()
            display_ver = _short_version(version)

            if branch_name:
                title = f"Terok TUI v{display_ver} [{branch_name}]"
            else:
                title = f"Terok TUI v{display_ver}"

            self.title = title

        # ---------- Layout ----------

        def compose(self) -> ComposeResult:
            """Build the two-pane layout: projects/state on the left, tasks/details on the right."""
            # Use Textual's default Header which will show our title
            yield Header()

            # Main layout using grid
            with Horizontal(id="main"):
                # Left pane: project list (top) + selected project info (bottom)
                with Vertical(id="left-pane"):
                    project_list = ProjectList(id="project-list")
                    project_list.border_title = "Projects"
                    yield project_list
                    project_state = ProjectState(id="project-state")
                    project_state.border_title = "Project Details"
                    yield project_state
                # Right pane: tasks + task details
                with Vertical(id="right-pane"):
                    task_list = TaskList(id="task-list")
                    task_list.border_title = "Tasks"
                    yield task_list
                    task_details = TaskDetails(id="task-details")
                    task_details.border_title = "Task Details"
                    yield task_details

            # Use Textual's default Footer which will show key bindings
            yield Footer()

        async def on_mount(self) -> None:
            """Load projects, restore selection state, and start polling on first mount."""
            try:
                clipboard_status = get_clipboard_helper_status()
                if not clipboard_status.available:
                    msg = "Clipboard copy unavailable: no clipboard helper found."
                    if clipboard_status.hint:
                        msg = f"{msg}\n{clipboard_status.hint}"
                    self.notify(msg, severity="warning", timeout=10)
            except Exception:
                # Clipboard helpers are best-effort; never block startup.
                pass

            # Load selection state before refreshing projects
            self._load_selection_state()

            await self.refresh_projects()
            # Defer layout logging until after the first refresh cycle so
            # widgets have real sizes. This will help compare left vs right
            # panes and confirm whether the task list/details get space.
            try:
                self.call_after_refresh(self._log_layout_debug)
            except Exception:
                # call_after_refresh may not exist on very old Textual; in
                # that case we simply skip this extra logging.
                pass

            # Startup gate server health check
            self.run_worker(
                get_server_status,
                name="gate-health-check",
                group="gate-health",
                thread=True,
                exit_on_error=False,
            )
            # Start periodic gate server polling
            self._start_gate_server_polling()

        def _log_layout_debug(self) -> None:
            """Write a one-shot snapshot of key widget sizes to the state dir.

            This is for debugging why the right-hand task list/details may
            not be visible even though the widgets exist.
            """
            try:
                log_path = state_root() / "terok.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)

                left_pane = self.query_one("#left-pane")
                right_pane = self.query_one("#right-pane")
                project_list = self.query_one("#project-list", ProjectList)
                project_state = self.query_one("#project-state", ProjectState)
                task_list = self.query_one("#task-list", TaskList)
                task_details = self.query_one("#task-details", TaskDetails)

                with log_path.open("a", encoding="utf-8") as _f:
                    _f.write("[terok DEBUG] layout snapshot after refresh:\n")
                    _f.write(f"  left-pane   size={left_pane.size} region={left_pane.region}\n")
                    _f.write(f"  right-pane  size={right_pane.size} region={right_pane.region}\n")
                    _f.write(
                        f"  proj-list   size={project_list.size} region={project_list.region}\n"
                    )
                    _f.write(
                        f"  proj-state  size={project_state.size} region={project_state.region}\n"
                    )
                    _f.write(f"  task-list   size={task_list.size} region={task_list.region}\n")
                    _f.write(
                        f"  task-det    size={task_details.size} region={task_details.region}\n"
                    )
            except Exception:
                pass

        def _log_debug(self, message: str) -> None:
            """Append a simple debug line to the TUI log file.

            This is intentionally very small and best-effort so it never
            interferes with normal TUI behavior. It shares the same log
            path as `_log_layout_debug` for easier inspection.
            """

            try:
                from datetime import datetime as _dt

                log_path = state_root() / "terok.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                ts = _dt.now().isoformat(timespec="seconds")
                with log_path.open("a", encoding="utf-8") as _f:
                    _f.write(f"[terok DEBUG] {ts} {message}\n")
            except Exception:
                # Logging must never break the TUI.
                pass

        def _load_selection_state(self) -> None:
            """Load last selected project and tasks from persistent storage."""
            try:
                import json

                state_path = state_root() / "terok-state.json"
                if state_path.exists():
                    with state_path.open("r", encoding="utf-8") as f:
                        state = json.load(f)
                        self._last_selected_project = state.get("last_project")
                        self._last_selected_tasks = state.get("last_tasks", {})
            except Exception:
                # If loading fails, just start with empty state
                self._last_selected_project = None
                self._last_selected_tasks = {}

        def _save_selection_state(self) -> None:
            """Save current selection state to persistent storage."""
            try:
                import json

                state_path = state_root() / "terok-state.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state = {
                    "last_project": self.current_project_id,
                    "last_tasks": self._last_selected_tasks,
                }
                with state_path.open("w", encoding="utf-8") as f:
                    json.dump(state, f)
            except Exception:
                # If saving fails, just ignore - it's not critical
                pass

        # ---------- Helpers ----------

        async def refresh_projects(self) -> None:
            """Reload all projects and update the project list widget."""
            proj_widget = self.query_one("#project-list", ProjectList)
            projects = list_projects()
            self._projects_by_id = {proj.id: proj for proj in projects}
            proj_widget.set_projects(projects)

            if projects:
                # Try to restore last selected project, fall back to first project
                last_project = self._last_selected_project
                if last_project and any(p.id == last_project for p in projects):
                    self.current_project_id = last_project
                    proj_widget.select_project(self.current_project_id)
                elif self.current_project_id is None:
                    self.current_project_id = projects[0].id
                    proj_widget.select_project(self.current_project_id)

                # Reset cached detail screen state; workers will repopulate.
                self._last_project_state = None
                self._last_image_old = None
                await self.refresh_tasks()
                # Start upstream polling for the selected project
                self._start_upstream_polling()
            else:
                self.current_project_id = None
                self._last_project_state = None
                self._last_image_old = None
                task_list = self.query_one("#task-list", TaskList)
                task_list.set_tasks("", [])
                task_details = self.query_one("#task-details", TaskDetails)
                task_details.set_task(None)
                # No projects means no meaningful project state.
                state_widget = self.query_one("#project-state", ProjectState)
                state_widget.set_state(None, None, None)

        async def refresh_tasks(self) -> None:
            """Reload tasks for the current project and update the task list."""
            if not self.current_project_id:
                return
            tasks_meta = get_tasks(self.current_project_id, reverse=True)
            task_list = self.query_one("#task-list", TaskList)
            task_list.set_tasks(self.current_project_id, tasks_meta)

            if task_list.tasks:
                # Try to restore last selected task for this project
                last_task_id = self._last_selected_tasks.get(self.current_project_id)
                desired_idx = 0
                if last_task_id:
                    for idx, task in enumerate(task_list.tasks):
                        if task.task_id == last_task_id:
                            desired_idx = idx
                            break

                self.current_task = task_list.tasks[desired_idx]

                # Defer index setting to after layout pass so appended items
                # are fully mounted.  An immediate ``index = 0`` after clear()
                # is a no-op because clear() already reset the index to 0.
                def _apply_selection(idx: int = desired_idx) -> None:
                    """Set the task list index after layout is complete."""
                    try:
                        task_list.index = idx
                        task_list._post_selected_task()
                    except Exception:
                        pass

                self.call_after_refresh(_apply_selection)
            else:
                self.current_task = None

            self._update_task_details()

            task_count = len(task_list.tasks)
            self._last_task_count = task_count
            # Update project state panel (Dockerfiles/images/SSH/cache + task count)
            self._refresh_project_state(task_count=task_count)

        def _update_task_details(self) -> None:
            """Refresh the task details panel for the currently selected task."""
            details = self.query_one("#task-details", TaskDetails)
            if self.current_task is None:
                details.set_task(None)
                return
            details.set_task(self.current_task)
            if not self.current_task.deleting:
                self._queue_task_image_status(self.current_project_id, self.current_task)

        # ---------- Status / notifications ----------

        def _refresh_project_state(self, task_count: int | None = None) -> None:
            """Update the small project state summary panel.

            This is called whenever the current project changes or when actions
            that affect infrastructure state (generate/build/ssh/cache) finish.
            """
            state_widget = self.query_one("#project-state", ProjectState)

            if not self.current_project_id:
                state_widget.set_state(None, None, None)
                return
            if task_count is not None:
                self._last_task_count = task_count

            project_id = self.current_project_id
            project = self._projects_by_id.get(project_id)
            if project is not None:
                state_widget.set_loading(project, self._last_task_count)
            else:
                state_widget.update("Loading project details...")

            self.run_worker(
                lambda: self._load_project_state(project_id),
                name=f"project-state:{project_id}",
                group="project-state",
                exclusive=True,
                thread=True,
                exit_on_error=False,
            )

        def _load_project_state(self, project_id: str) -> ProjectStateResult:
            """Load project infrastructure state in a background thread."""
            try:
                project = load_project(project_id)
                gate = GitGate(project)
                state = get_project_state(
                    project_id,
                    gate_commit_provider=lambda _pid, _g=gate: _g.last_commit(),
                )
                staleness = None
                if state.get("gate") and project.upstream_url:
                    try:
                        staleness = gate.compare_vs_upstream()
                    except Exception:
                        staleness = None
                try:
                    gate_status = get_server_status()
                except Exception:
                    gate_status = None
                return ProjectStateResult(
                    project_id,
                    project,
                    state,
                    staleness,
                    gate_server_status=gate_status,
                )
            except SystemExit as e:
                return ProjectStateResult(project_id, error=str(e))
            except Exception as e:
                return ProjectStateResult(project_id, error=str(e))

        def _queue_task_image_status(self, project_id: str | None, task: TaskMeta | None) -> None:
            """Schedule a background check for whether the task's image is outdated."""
            if not project_id or task is None:
                return
            if task.deleting:
                return

            task_id = task.task_id
            self.run_worker(
                lambda: self._load_task_image_status(project_id, task),
                name=f"task-image:{project_id}:{task_id}",
                group="task-image",
                exclusive=True,
                thread=True,
                exit_on_error=False,
            )

        def _load_task_image_status(
            self, project_id: str, task: TaskMeta
        ) -> tuple[str, str, bool | None]:
            """Check whether a task's container image is outdated (runs in thread)."""
            image_old = is_task_image_old(project_id, task)
            return project_id, task.task_id, image_old

        # ---------- Selection handlers (from widgets) ----------

        @on(ProjectList.ProjectSelected)
        async def handle_project_selected(self, message: ProjectList.ProjectSelected) -> None:
            """Called when user selects a project in the list."""
            self.current_project_id = message.project_id
            self._last_project_state = None
            # Save the project selection
            self._last_selected_project = self.current_project_id
            self._save_selection_state()

            await self.refresh_tasks()
            # Start polling for the newly selected project
            self._start_upstream_polling()
            self._start_container_status_polling()

        @on(TaskList.TaskSelected)
        async def handle_task_selected(self, message: TaskList.TaskSelected) -> None:
            """Called when user selects a task in the list."""
            self.current_project_id = message.project_id
            self.current_task = message.task
            self._last_image_old = None

            # Save the task selection for this project
            if self.current_project_id and self.current_task:
                self._last_selected_tasks[self.current_project_id] = self.current_task.task_id
                self._save_selection_state()

            self._update_task_details()

            # Immediately check container state when task is selected
            if self.current_task and self.current_task.mode:
                self._queue_container_state_check(message.project_id)

        @on(Worker.StateChanged)
        async def handle_worker_state_changed(self, event: Worker.StateChanged) -> None:
            """Dispatch completed worker results to the appropriate UI panel."""
            worker = event.worker
            if event.state != WorkerState.SUCCESS:
                if worker.group == "project-state" and event.state == WorkerState.ERROR:
                    state_widget = self.query_one("#project-state", ProjectState)
                    state_widget.update(f"Project state error: {worker.error}")
                return

            if worker.group == "project-state":
                result = worker.result
                if not result:
                    return
                psr: ProjectStateResult = result
                if psr.project_id != self.current_project_id:
                    return
                state_widget = self.query_one("#project-state", ProjectState)
                if psr.error:
                    state_widget.update(f"Project state error: {psr.error}")
                    return
                if psr.project is None or psr.state is None:
                    state_widget.set_state(None, None, None)
                    return
                self._projects_by_id[psr.project_id] = psr.project
                self._staleness_info = psr.staleness
                self._last_project_state = psr.state
                self._last_gate_server_status = psr.gate_server_status
                state_widget.set_state(
                    psr.project,
                    psr.state,
                    self._last_task_count,
                    self._staleness_info,
                    gate_server_status=psr.gate_server_status,
                )
                return

            if worker.group == "task-image":
                result = worker.result
                if not result:
                    return
                project_id, task_id, image_old = result
                if project_id != self.current_project_id:
                    return
                if not self.current_task or self.current_task.task_id != task_id:
                    return
                self._last_image_old = image_old
                details = self.query_one("#task-details", TaskDetails)
                details.set_task(self.current_task, image_old=image_old)
                return

            if worker.group == "container-state":
                result = worker.result
                if not result:
                    return
                project_id, states = result
                if project_id != self.current_project_id:
                    return
                # Update container_state on all TaskMeta instances
                task_list = self.query_one("#task-list", TaskList)
                changed = False
                for tm in task_list.tasks:
                    new_state = states.get(tm.task_id)
                    if tm.container_state != new_state:
                        tm.container_state = new_state
                        changed = True
                if changed:
                    # Regenerate labels on visible list items so status badges update
                    for item in task_list.query(TaskListItem):
                        label = task_list._format_task_label(item.task_meta)
                        item.query_one(Static).update(label)
                    if self.current_task:
                        details = self.query_one("#task-details", TaskDetails)
                        details.set_task(self.current_task)
                return

            if worker.group == "task-delete":
                result = worker.result
                if not result:
                    return
                project_id, task_id, task_name, error = result
                task_label = f"{project_id} {task_id}" + (f" {task_name}" if task_name else "")
                if error:
                    self.notify(f"Delete error for task {task_label}: {error}")
                else:
                    self.notify(
                        f"Deleted task {task_label}.\n"
                        f"Archive: terokctl task archive list {project_id}",
                    )

                if project_id != self.current_project_id:
                    return
                await self.refresh_tasks()

            if worker.group == "autopilot-launch":
                result = worker.result
                if not result:
                    return
                project_id, task_id, error = result
                if error:
                    self.notify(f"Autopilot error: {error}")
                elif task_id:
                    self._focus_task_after_creation(project_id, task_id)
                    self.notify(f"Autopilot task {task_id} started for {project_id}")
                    self._start_autopilot_watcher(project_id, task_id)
                if project_id == self.current_project_id:
                    await self.refresh_tasks()
                return

            if worker.group == "autopilot-wait":
                result = worker.result
                if not result:
                    return
                project_id, task_id, exit_code, error = result
                if error:
                    self.notify(f"Autopilot watcher error for task {task_id}: {error}")
                elif exit_code == 0:
                    self.notify(f"Autopilot task {task_id} completed successfully")
                else:
                    self.notify(f"Autopilot task {task_id} failed (exit {exit_code})")
                if project_id == self.current_project_id:
                    await self.refresh_tasks()
                return

            if worker.group == "followup-launch":
                result = worker.result
                if not result:
                    return
                project_id, task_id, error = result
                if error:
                    self.notify(f"Follow-up error: {error}")
                else:
                    self.notify(f"Follow-up started for task {task_id}")
                    self._start_autopilot_watcher(project_id, task_id)
                if project_id == self.current_project_id:
                    await self.refresh_tasks()
                return

            if worker.group == "gate-health":
                result = worker.result
                if result and not result.running:
                    self.notify(
                        "Gate server is not running. "
                        "Press Ctrl+P \u2192 Git Gate Server to manage.",
                        severity="warning",
                        timeout=10,
                    )
                return

            if worker.group == "gate-server-poll":
                result = worker.result
                if not result:
                    return
                was_running = self._last_gate_server_running
                now_running = result.running
                if was_running is True and not now_running:
                    self.notify("Gate server stopped", severity="warning")
                elif was_running is False and now_running:
                    self.notify("Gate server is now running")
                self._last_gate_server_running = now_running
                self._last_gate_server_status = result
                # Refresh project state to update the combined gate line
                self._refresh_project_state()
                return

        # ---------- Actions (keys + called from buttons) ----------

        async def action_edit_global_instructions(self) -> None:
            """Edit Global Instructions."""
            await self._action_edit_global_instructions()

        async def action_show_default_instructions(self) -> None:
            """Show Default Instructions."""
            await self._action_show_default_instructions()

        async def action_quit(self) -> None:
            """Exit the TUI cleanly."""
            self._stop_upstream_polling()
            self._stop_container_status_polling()
            self._stop_gate_server_polling()
            self.exit()

        async def action_show_project_actions(self) -> None:
            """Show detail screen with project info and actions."""
            if not self.current_project_id:
                self.notify("No project selected.")
                return
            project = self._projects_by_id.get(self.current_project_id)
            if not project:
                self.notify("Project data not loaded yet.")
                return
            await self.push_screen(
                ProjectDetailsScreen(
                    project,
                    self._last_project_state,
                    self._last_task_count,
                    self._staleness_info,
                ),
                self._on_project_action_screen_result,
            )

        async def action_show_task_actions(self) -> None:
            """Show detail screen with task info and actions."""
            if not self.current_project_id:
                self.notify("No project selected.")
                return
            try:
                task_list = self.query_one("#task-list", TaskList)
                has_tasks = bool(task_list.tasks)
            except Exception:
                has_tasks = False
            await self.push_screen(
                TaskDetailsScreen(
                    self.current_task,
                    has_tasks,
                    self.current_project_id,
                    self._last_image_old,
                ),
                self._on_task_action_screen_result,
            )

        async def _on_project_action_screen_result(self, result: str | None) -> None:
            """Handle result from project actions screen."""
            if result:
                await self._handle_project_action(result)

        async def _on_task_action_screen_result(self, result: str | None) -> None:
            """Handle result from task actions screen."""
            if result:
                await self._handle_task_action(result)

        async def _handle_project_action(self, action: str) -> None:
            """Handle project actions."""
            if action.startswith("auth_"):
                await self._action_auth(action[5:])
                return
            handler = PROJECT_ACTION_HANDLERS.get(action)
            if handler:
                await getattr(self, handler)()

        async def _handle_task_action(self, action: str) -> None:
            """Handle task actions."""
            handler = TASK_ACTION_HANDLERS.get(action)
            if handler:
                await getattr(self, handler)()

        # ---------- Command palette ----------

        def get_system_commands(self, screen):
            """Add gate server management to the command palette."""
            from textual.app import SystemCommand

            yield from super().get_system_commands(screen)
            yield SystemCommand(
                "Git Gate Server",
                "Manage gate server status and operations",
                self.action_show_gate_server,
            )

        async def action_show_gate_server(self) -> None:
            """Open the gate server management screen."""
            await self.push_screen(
                GateServerScreen(self._last_gate_server_status),
                self._on_gate_server_action_result,
            )

        async def _on_gate_server_action_result(self, result: str | None) -> None:
            """Handle result from gate server screen."""
            if not result:
                return
            handler = GATE_SERVER_ACTION_HANDLERS.get(result)
            if handler:
                await getattr(self, handler)()

    def _launch_in_tmux() -> None:
        """Launch the TUI inside a managed tmux session.

        If already inside tmux, just run the TUI directly.
        Otherwise, verify that tmux is installed and exec into it with the
        terok host config (blue status bar, usage hints).  Exits with an
        actionable error message if tmux is not found on ``$PATH``.
        """
        if os.environ.get("TMUX"):
            # Already inside tmux — no double-wrap
            TerokTUI().run()
            return

        import shutil

        if not shutil.which("tmux"):
            print(
                "Error: tmux is not installed.\n"
                "Install it (e.g. 'apt install tmux' or 'brew install tmux') "
                "and try again,\nor run 'terok' without --tmux.",
                file=sys.stderr,
            )
            sys.exit(1)

        from importlib import resources as _res

        tmux_conf = _res.files("terok") / "resources" / "tmux" / "host-tmux.conf"
        # Materialise the resource to a real file path for tmux -f.
        # Note: os.execvp replaces this process so the context manager's
        # __exit__ never runs.  This is fine — tmux reads the config file
        # at startup, and OS process cleanup handles any temp resources.
        with _res.as_file(tmux_conf) as conf_path:
            os.execvp(
                "tmux",
                [
                    "tmux",
                    "-f",
                    str(conf_path),
                    "new-session",
                    "-s",
                    "terok",
                    "terok",
                ],
            )

    def main() -> None:
        """CLI entry-point for launching the terok TUI.

        Supports ``--tmux`` to wrap the TUI in a managed host tmux session
        (blue status bar, login windows as extra tmux windows).  Without the
        flag the TUI runs directly in the current terminal.

        If neither --tmux nor --no-tmux is specified, the behavior is controlled
        by the global config setting `tui.default_tmux` (defaults to False).
        """
        import argparse

        parser = argparse.ArgumentParser(prog="terok")
        parser.add_argument(
            "--tmux",
            action="store_true",
            help="Launch TUI inside a managed tmux session",
        )
        parser.add_argument(
            "--no-tmux",
            dest="tmux",
            action="store_false",
            help="Launch TUI directly in current terminal (default if not configured)",
        )
        parser.add_argument(
            "--experimental",
            action="store_true",
            default=False,
            help="Enable experimental features (e.g. web tasks)",
        )
        parser.add_argument(
            "--no-emoji",
            action="store_true",
            default=False,
            help="Replace emojis with text labels (e.g. [gate] instead of \U0001f6aa)",
        )
        args = parser.parse_args()
        set_experimental(args.experimental)

        if args.no_emoji:
            from ..lib.util.emoji import set_emoji_enabled

            set_emoji_enabled(False)

        # Determine tmux mode: explicit flag > config default > False
        use_tmux = (
            args.tmux if hasattr(args, "tmux") and args.tmux is not None else get_tui_default_tmux()
        )

        if use_tmux:
            _launch_in_tmux()
            return
        TerokTUI().run()

else:

    def main() -> None:
        """Print an error message when Textual is not installed and exit."""
        print(
            "terok TUI requires the 'textual' package.\nInstall it with: pip install 'terok[tui]'",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    enable_pycharm_debugger()
    main()
