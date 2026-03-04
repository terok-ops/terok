# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""TaskActionsMixin — task lifecycle operations for TerokTUI.

Handles task creation, deletion, renaming, running (CLI/web/autopilot),
login, restart, follow-up, log viewing, and diff copying.
"""

from ..lib.containers.agents import parse_md_agent
from ..lib.containers.autopilot import wait_for_container_exit
from ..lib.containers.runtime import container_name, get_container_state
from ..lib.containers.task_display import effective_status
from ..lib.containers.tasks import (
    generate_task_name,
    get_login_command,
    get_workspace_git_diff,
    mark_task_deleting,
)
from ..lib.core.config import is_experimental
from ..lib.core.projects import load_project
from ..lib.facade import (
    task_delete,
    task_followup_headless,
    task_new,
    task_rename,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_web,
)
from .clipboard import copy_to_clipboard_detailed
from .screens import AgentSelectionScreen, AutopilotPromptScreen, SubagentInfo, TaskNameScreen
from .widgets import TaskList


class TaskActionsMixin:
    """Task-related action handlers for the TerokTUI application.

    Provides all ``action_*`` and ``_action_*`` methods for task lifecycle
    operations.  The host class must provide the standard Textual ``App``
    interface plus the instance attributes initialised by ``TerokTUI.__init__``.
    """

    _autopilot_pending_name: str | None = None

    # ---------- Helpers ----------

    @staticmethod
    def _normalize_subagents(subagents: list[dict]) -> list[SubagentInfo]:
        """Resolve ``file:`` shorthand entries into full sub-agent dicts.

        Each entry in *subagents* may be either an inline dict (already has
        ``name``, ``description``, etc.) or a ``file:`` reference whose
        ``name`` and ``description`` live inside the ``.md`` YAML frontmatter.
        This normalises both forms into :class:`SubagentInfo` dicts so the UI
        screens always have ``name`` and ``description`` to display.
        """
        result: list[SubagentInfo] = []
        for sa in subagents:
            if "file" in sa:
                parsed = parse_md_agent(sa["file"])
                if not parsed:
                    continue
                if "default" in sa:
                    parsed["default"] = sa["default"]
                agent = parsed
            else:
                agent = dict(sa)
            name = agent.get("name")
            if not name:
                continue
            result.append(
                SubagentInfo(
                    name=name,
                    description=agent.get("description", ""),
                    default=bool(agent.get("default", False)),
                )
            )
        return result

    def _focus_task_after_creation(self, project_id: str, task_id: str) -> None:
        """Persist selection so the newly created task is focused after refresh."""
        self._last_selected_tasks[project_id] = task_id
        self._save_selection_state()

    # ---------- Worker helpers ----------

    def _queue_task_delete(self, project_id: str, task_id: str) -> None:
        """Schedule a background worker to delete a task."""
        self.run_worker(
            lambda: self._delete_task(project_id, task_id),
            name=f"task-delete:{project_id}:{task_id}",
            group="task-delete",
            thread=True,
            exit_on_error=False,
        )

    def _delete_task(self, project_id: str, task_id: str) -> tuple[str, str, str | None]:
        """Delete a task and return ``(project_id, task_id, error_or_None)``."""
        try:
            task_delete(project_id, task_id)
            return project_id, task_id, None
        except SystemExit as e:
            return project_id, task_id, str(e)
        except Exception as e:
            return project_id, task_id, str(e)

    # ---------- Task lifecycle actions ----------

    async def action_new_task(self) -> None:
        """Create a new task for the current project."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        default_name = generate_task_name(self.current_project_id)
        await self.push_screen(
            TaskNameScreen(default_name=default_name),
            self._on_new_task_name,
        )

    async def _on_new_task_name(self, name: str | None) -> None:
        """Handle name result from TaskNameScreen for new task creation."""
        if name is None or not self.current_project_id:
            return
        pid = self.current_project_id

        def work() -> None:
            """Create the task and update focus state."""
            task_id = task_new(pid, name=name)
            self._focus_task_after_creation(pid, task_id)

        await self._run_suspended(work, success_msg="Task created.", refresh="tasks")

    async def action_run_cli(self) -> None:
        """Run the CLI agent for the currently selected task."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        pid = self.current_project_id
        tid = self.current_task.task_id

        def work() -> None:
            """Launch the CLI container for this task."""
            print(f"Running CLI for {pid}/{tid}...\n")
            task_run_cli(pid, tid)

        await self._run_suspended(work, refresh="tasks")

    async def action_run_web(self) -> None:
        """Public action for running web UI (delegates to _action_run_web)."""
        await self._action_run_web()

    async def _action_run_web(self) -> None:
        """Run web UI for current task."""
        if not is_experimental():
            self.notify("Web tasks require --experimental flag.")
            return
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        pid = self.current_project_id
        tid = self.current_task.task_id

        def work() -> None:
            """Prompt for backend and launch the web container."""
            backend = self._prompt_ui_backend()
            print(f"Starting Web UI for {pid}/{tid} (backend: {backend})...\n")
            task_run_web(pid, tid, backend=backend)

        await self._run_suspended(work, refresh="tasks")

    async def _action_task_start_cli(self) -> None:
        """Create a new task and immediately run CLI agent."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        default_name = generate_task_name(self.current_project_id)
        await self.push_screen(
            TaskNameScreen(default_name=default_name),
            self._on_task_start_cli_name,
        )

    async def _on_task_start_cli_name(self, name: str | None) -> None:
        """Handle name result from TaskNameScreen for CLI task start."""
        if name is None or not self.current_project_id:
            return
        pid = self.current_project_id

        def work() -> None:
            """Create a new task and immediately launch CLI mode."""
            task_id = task_new(pid, name=name)
            self._focus_task_after_creation(pid, task_id)
            print(f"\nRunning CLI for {pid}/{task_id}...\n")
            task_run_cli(pid, task_id)

        await self._run_suspended(work, refresh="tasks")

    async def _action_task_start_web(self) -> None:
        """Create a new task and immediately run Web UI."""
        if not is_experimental():
            self.notify("Web tasks require --experimental flag.")
            return
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        default_name = generate_task_name(self.current_project_id)
        await self.push_screen(
            TaskNameScreen(default_name=default_name),
            self._on_task_start_web_name,
        )

    async def _on_task_start_web_name(self, name: str | None) -> None:
        """Handle name result from TaskNameScreen for web task start."""
        if name is None or not self.current_project_id:
            return
        pid = self.current_project_id

        def work() -> None:
            """Create a new task and immediately launch web mode."""
            task_id = task_new(pid, name=name)
            self._focus_task_after_creation(pid, task_id)
            backend = self._prompt_ui_backend()
            print(f"\nStarting Web UI for {pid}/{task_id} (backend: {backend})...\n")
            task_run_web(pid, task_id, backend=backend)

        await self._run_suspended(work, refresh="tasks")

    async def _action_task_start_autopilot(self) -> None:
        """Create a new task and run Claude headlessly (autopilot)."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        # Show name input screen first, then prompt
        default_name = generate_task_name(self.current_project_id)
        await self.push_screen(
            TaskNameScreen(default_name=default_name),
            self._on_autopilot_name_result,
        )

    _autopilot_pending_agent: tuple[str, list[str] | None] | None = None

    async def _on_autopilot_name_result(self, name: str | None) -> None:
        """Handle the name returned from TaskNameScreen for autopilot."""
        if name is None or not self.current_project_id:
            return

        pid = self.current_project_id

        # Store the name and show agent selection screen
        self._autopilot_pending_name = name

        try:
            project = load_project(pid)
        except (SystemExit, Exception) as e:
            self._autopilot_pending_name = None
            self.notify(f"Error loading project: {e}")
            return

        default_agent = project.default_agent or "claude"
        raw_subagents = project.agent_config.get("subagents", [])
        subagents = self._normalize_subagents(raw_subagents) if raw_subagents else []

        await self.push_screen(
            AgentSelectionScreen(subagents=subagents or None, default_agent=default_agent),
            self._on_agent_selection_result,
        )

    async def _on_agent_selection_result(self, result: tuple[str, list[str] | None] | None) -> None:
        """Handle the result from AgentSelectionScreen, then show the prompt screen."""
        if result is None:
            self._autopilot_pending_name = None
            return

        self._autopilot_pending_agent = result
        await self.push_screen(
            AutopilotPromptScreen(),
            self._on_autopilot_prompt_result,
        )

    async def _on_autopilot_prompt_result(self, prompt: str | None) -> None:
        """Handle the prompt returned from AutopilotPromptScreen and launch."""
        if not prompt:
            self._autopilot_pending_name = None
            self._autopilot_pending_agent = None
            return

        result = self._autopilot_pending_agent
        self._autopilot_pending_agent = None
        if not result:
            return

        agent_name, selected_subagents = result

        # Only pass sub-agents if the agent supports them
        from ..lib.containers.headless_providers import HEADLESS_PROVIDERS

        provider = HEADLESS_PROVIDERS.get(agent_name)
        agents = selected_subagents if provider and provider.supports_agents_json else None

        await self._launch_autopilot(prompt, agents=agents, provider=agent_name)

    async def _launch_autopilot(
        self, prompt: str, agents: list[str] | None = None, provider: str | None = None
    ) -> None:
        """Launch a headless autopilot task in a background worker."""
        if not self.current_project_id:
            return
        pid = self.current_project_id
        name = getattr(self, "_autopilot_pending_name", None)
        self._autopilot_pending_name = None
        self.notify(f"Starting autopilot task for {pid}...")
        self.run_worker(
            lambda: self._run_headless_worker(pid, prompt, agents, name, provider=provider),
            name=f"autopilot-launch:{pid}",
            group="autopilot-launch",
            thread=True,
            exit_on_error=False,
        )

    def _run_headless_worker(
        self,
        project_id: str,
        prompt: str,
        agents: list[str] | None,
        name: str | None = None,
        provider: str | None = None,
    ) -> tuple[str, str, str | None]:
        """Background worker: launch task_run_headless and return result."""
        try:
            task_id = task_run_headless(
                project_id, prompt, follow=False, agents=agents, name=name, provider=provider
            )
            return project_id, task_id, None
        except SystemExit as e:
            return project_id, "", str(e)
        except Exception as e:
            return project_id, "", str(e)

    def _start_autopilot_watcher(self, project_id: str, task_id: str) -> None:
        """Spawn a background worker that waits for the container to finish
        and updates task metadata with the exit code."""
        cname = container_name(project_id, "run", task_id)
        self.run_worker(
            lambda: self._autopilot_wait_worker(project_id, task_id, cname),
            name=f"autopilot-wait:{project_id}:{task_id}",
            group="autopilot-wait",
            thread=True,
            exit_on_error=False,
        )

    def _autopilot_wait_worker(
        self, project_id: str, task_id: str, cname: str
    ) -> tuple[str, str, int | None, str | None]:
        """Background worker: wait for the container to exit and update metadata."""
        exit_code, error = wait_for_container_exit(cname, project_id, task_id)
        return project_id, task_id, exit_code, error

    # ── Follow-up on completed/failed autopilot tasks ──

    async def _action_task_followup(self) -> None:
        """Follow up on a completed/failed autopilot task with a new prompt."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        task = self.current_task
        if task.mode != "run" or effective_status(task) not in {"completed", "failed"}:
            self.notify("Follow-up is only available for completed/failed autopilot tasks.")
            return

        await self.push_screen(
            AutopilotPromptScreen(),
            self._on_followup_prompt_result,
        )

    async def _on_followup_prompt_result(self, prompt: str | None) -> None:
        """Handle the prompt returned from follow-up prompt screen."""
        if not prompt or not self.current_project_id or not self.current_task:
            return
        pid = self.current_project_id
        tid = self.current_task.task_id
        self.notify(f"Sending follow-up to task {tid}...")
        self.run_worker(
            lambda: self._run_followup_worker(pid, tid, prompt),
            name=f"followup-launch:{pid}:{tid}",
            group="followup-launch",
            thread=True,
            exit_on_error=False,
        )

    def _run_followup_worker(
        self, project_id: str, task_id: str, prompt: str
    ) -> tuple[str, str, str | None]:
        """Background worker: call task_followup_headless and return result."""
        try:
            task_followup_headless(project_id, task_id, prompt, follow=False)
            return project_id, task_id, None
        except SystemExit as e:
            return project_id, task_id, str(e)
        except Exception as e:
            return project_id, task_id, str(e)

    async def _action_follow_logs(self) -> None:
        """View logs for a task in the integrated log viewer."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        task = self.current_task
        if not task.mode:
            self.notify("Task has no mode set (never started).")
            return

        pid = self.current_project_id
        tid = task.task_id
        cname = container_name(pid, task.mode, tid)

        state = get_container_state(cname)
        if state is None:
            self.notify(f"No container found for task {tid}.")
            return
        follow = state == "running"

        from .log_viewer import LogViewerScreen

        provider = getattr(task, "provider", None)
        await self.push_screen(
            LogViewerScreen(
                project_id=pid,
                task_id=tid,
                mode=task.mode,
                container_name=cname,
                follow=follow,
                provider=provider,
            )
        )

    async def _action_restart_task(self) -> None:
        """Restart a task container (stops it first if running)."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        pid = self.current_project_id
        tid = self.current_task.task_id
        await self._run_suspended(lambda: task_restart(pid, tid), refresh="tasks")

    async def _action_login(self) -> None:
        """Log into the selected task's running container."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        pid = self.current_project_id
        tid = self.current_task.task_id
        try:
            cmd = get_login_command(pid, tid)
        except SystemExit as e:
            self.notify(str(e))
            return

        mode = self.current_task.mode or "cli"
        cname = container_name(pid, mode, tid)
        await self._launch_terminal_session(cmd, title=f"login:{cname}", cname=cname)

    # ---------- Task management actions ----------

    async def action_delete_task(self) -> None:
        """Delete the currently selected task and its containers."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return

        tid = self.current_task.task_id
        if self.current_task.deleting:
            self.notify(f"Task {tid} is already deleting.")
            return

        self._log_debug(f"delete: start project_id={self.current_project_id} task_id={tid}")
        self.notify(f"Deleting task {tid}...")

        self.current_task.deleting = True
        task_list = self.query_one("#task-list", TaskList)
        task_list.mark_deleting(tid)
        self._update_task_details()

        mark_task_deleting(self.current_project_id, tid)
        self._queue_task_delete(self.current_project_id, tid)

    async def _action_rename_task(self) -> None:
        """Rename the currently selected task."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        current_name = self.current_task.name or ""
        await self.push_screen(
            TaskNameScreen(default_name=current_name),
            self._on_rename_task_result,
        )

    async def _on_rename_task_result(self, name: str | None) -> None:
        """Handle name result from TaskNameScreen for rename."""
        if name is None or not self.current_project_id or not self.current_task:
            return
        pid = self.current_project_id
        tid = self.current_task.task_id
        try:
            task_rename(pid, tid, name)
        except SystemExit as e:
            self.notify(str(e))
            return
        self.notify(f"Task {tid} renamed.")
        await self.refresh_tasks()

    async def _copy_diff_to_clipboard(self, git_ref: str, label: str) -> None:
        """Common helper to copy a git diff to the clipboard."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return

        task_id = self.current_task.task_id
        diff = get_workspace_git_diff(self.current_project_id, task_id, git_ref)

        if diff is None:
            self.notify("Failed to get git diff. Is this a git repository?")
            return

        if diff == "":
            self.notify("No changes to copy (working tree clean).")
            return

        result = copy_to_clipboard_detailed(diff)
        if result.ok:
            self.notify(f"Git diff vs {label} copied to clipboard ({len(diff)} characters)")
        else:
            msg = result.error or "Failed to copy to clipboard."
            if result.hint:
                msg = f"{msg}\n{result.hint}"
            self.notify(msg)

    async def action_copy_diff_head(self) -> None:
        """Copy git diff vs HEAD to clipboard."""
        await self._copy_diff_to_clipboard("HEAD", "HEAD")

    async def action_copy_diff_prev(self) -> None:
        """Copy git diff vs previous commit to clipboard."""
        await self._copy_diff_to_clipboard("PREV", "PREV")

    # --- Main-screen task pane shortcuts (c/w/d) ---

    async def action_run_cli_from_main(self) -> None:
        """Start a new CLI task from the main screen."""
        await self._action_task_start_cli()

    async def action_delete_task_from_main(self) -> None:
        """Delete the selected task from the main screen."""
        await self.action_delete_task()

    async def action_login_from_main(self) -> None:
        """Login to the selected task from the main screen."""
        await self._action_login()

    async def action_run_autopilot_from_main(self) -> None:
        """Start a new autopilot task from the main screen."""
        await self._action_task_start_autopilot()

    async def action_follow_logs_from_main(self) -> None:
        """Follow logs for the selected autopilot task from the main screen."""
        await self._action_follow_logs()
