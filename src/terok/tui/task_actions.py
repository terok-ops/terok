# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""TaskActionsMixin — task lifecycle operations for TerokTUI.

Handles task creation, deletion, renaming, running (CLI/toad/autopilot),
login, restart, follow-up, log viewing, and diff copying.
"""

import io
import shlex
from collections.abc import Callable
from contextlib import redirect_stdout
from pathlib import Path

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
from ..lib.core.config import get_shield_bypass_firewall_no_protection
from ..lib.core.projects import load_project
from ..lib.facade import (
    HeadlessRunRequest,
    shield_down,
    shield_up,
    task_delete,
    task_followup_headless,
    task_new,
    task_rename,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
)
from .clipboard import copy_to_clipboard_detailed
from .screens import (
    AgentSelectionScreen,
    AutopilotPromptScreen,
    SubagentInfo,
    TaskCreateScreen,
    TaskLaunchScreen,
    TaskNameScreen,
)
from .widgets import TaskList


def _build_interactive_agent_command(provider: object, prompt: str | None) -> str:
    """Build shell command to launch an agent interactively with optional prompt.

    Uses the binary as a positional command — the prompt is a first-turn
    argument, NOT the headless ``-p`` flag.  The agent runs interactively
    inside tmux so the user can re-attach later.
    """
    if not prompt:
        return provider.binary
    return f"{provider.binary} {shlex.quote(prompt)}"


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

    def _queue_task_delete(self, project_id: str, task_id: str, task_name: str) -> None:
        """Schedule a background worker to delete a task."""
        self.run_worker(
            lambda: self._delete_task(project_id, task_id, task_name),
            name=f"task-delete:{project_id}:{task_id}",
            group="task-delete",
            thread=True,
            exit_on_error=False,
        )

    def _delete_task(
        self, project_id: str, task_id: str, task_name: str
    ) -> tuple[str, str, str, str | None]:
        """Delete a task and return ``(project_id, task_id, task_name, error_or_None)``."""
        try:
            task_delete(project_id, task_id)
            return project_id, task_id, task_name, None
        except SystemExit as e:
            return project_id, task_id, task_name, str(e)
        except Exception as e:
            return project_id, task_id, task_name, str(e)

    # ---------- Background container start helpers ----------

    def _start_cli_container_quiet(
        self, pid: str, task_id: str
    ) -> tuple[str, str, str, str | None]:
        """Background worker: start CLI container, suppress stdout."""
        cname = container_name(pid, "cli", task_id)
        try:
            with redirect_stdout(io.StringIO()):
                task_run_cli(pid, task_id)
            return pid, task_id, cname, None
        except (SystemExit, Exception) as e:
            return pid, task_id, cname, str(e)

    def _start_toad_container_quiet(
        self, pid: str, task_id: str
    ) -> tuple[str, str, str, str | None]:
        """Background worker: start Toad container, suppress stdout."""
        cname = container_name(pid, "toad", task_id)
        try:
            with redirect_stdout(io.StringIO()):
                task_run_toad(pid, task_id)
            return pid, task_id, cname, None
        except (SystemExit, Exception) as e:
            return pid, task_id, cname, str(e)

    # ---------- Task lifecycle actions ----------

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
        await self._start_cli_task_background(name)

    async def _start_cli_task_background(self, name: str) -> None:
        """Create a CLI task and start its container in the background."""
        pid = self.current_project_id
        if not pid:
            return
        try:
            task_id = task_new(pid, name=name)
        except (SystemExit, Exception) as e:
            self.notify(f"Failed to create task: {e}")
            return
        self._focus_task_after_creation(pid, task_id)
        cname = container_name(pid, "cli", task_id)

        self.run_worker(
            lambda: self._start_cli_container_quiet(pid, task_id),
            name=f"cli-launch:{pid}:{task_id}",
            group="cli-launch",
            thread=True,
            exit_on_error=False,
        )

        # Resolve default login agent: project → global → "bash"
        default_login = "bash"
        try:
            project = load_project(pid)
            default_login = project.default_login or "bash"
        except (SystemExit, Exception):
            pass

        await self.push_screen(
            TaskLaunchScreen(
                container_name=cname,
                project_id=pid,
                task_id=task_id,
                default_login=default_login,
            ),
            self._on_launch_screen_result,
        )
        await self.refresh_tasks()

    async def _on_launch_screen_result(
        self, result: "tuple[str, str, str, str, str | None] | None"
    ) -> None:
        """Handle the result from TaskLaunchScreen.

        The result carries the full launch context captured at creation time
        so it is immune to ``self.current_task`` changes while the modal is open.
        """
        if result is None:
            await self.refresh_tasks()
            return

        pid, tid, cname, agent, prompt = result

        # All agents (including bash) launch interactively inside tmux so the
        # user can re-attach later with 'login'.  The base command is always
        # podman exec -it <cname> tmux new-session -A -s main [bash -lc <cmd>].
        try:
            base_cmd = get_login_command(pid, tid)
        except SystemExit as e:
            self.notify(str(e))
            return

        if agent == "bash":
            cmd = base_cmd
        else:
            from ..lib.containers.headless_providers import HEADLESS_PROVIDERS

            provider = HEADLESS_PROVIDERS.get(agent)
            if not provider:
                self.notify(f"Unknown agent: {agent}")
                return
            agent_cmd = _build_interactive_agent_command(provider, prompt)
            cmd = [*base_cmd, "bash", "-lc", agent_cmd]

        await self._launch_terminal_session(cmd, title=f"{pid}:{tid}", cname=cname)

    async def _action_task_start_toad(self) -> None:
        """Create a new task and immediately run Toad serve."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return

        default_name = generate_task_name(self.current_project_id)
        await self.push_screen(
            TaskNameScreen(default_name=default_name),
            self._on_task_start_toad_name,
        )

    async def _on_task_start_toad_name(self, name: str | None) -> None:
        """Handle name result from TaskNameScreen for Toad task start."""
        if name is None or not self.current_project_id:
            return
        await self._start_toad_task_background(name)

    async def _start_toad_task_background(self, name: str) -> None:
        """Create a Toad task and start its container in the background."""
        pid = self.current_project_id
        if not pid:
            return
        try:
            task_id = task_new(pid, name=name)
        except (SystemExit, Exception) as e:
            self.notify(f"Failed to create task: {e}")
            return
        self._focus_task_after_creation(pid, task_id)

        self.run_worker(
            lambda: self._start_toad_container_quiet(pid, task_id),
            name=f"toad-launch:{pid}:{task_id}",
            group="toad-launch",
            thread=True,
            exit_on_error=False,
        )
        self.notify("Starting Toad task\u2026")
        await self.refresh_tasks()

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
                HeadlessRunRequest(
                    project_id=project_id,
                    prompt=prompt,
                    follow=False,
                    agents=agents,
                    name=name,
                    provider=provider,
                )
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

        from .log_viewer import LogViewerScreen, TaskContainerRef

        provider = getattr(task, "provider", None)
        await self.push_screen(
            LogViewerScreen(
                TaskContainerRef(
                    project_id=pid,
                    task_id=tid,
                    mode=task.mode,
                    container_name=cname,
                    provider=provider,
                ),
                follow=follow,
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
        task_name = self.current_task.name or tid
        await self._launch_terminal_session(cmd, title=f"{pid}:{tid}:{task_name}", cname=cname)

    # ---------- Task management actions ----------

    async def action_delete_task(self) -> None:
        """Delete the currently selected task and its containers."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return

        tid = self.current_task.task_id
        tname = self.current_task.name or ""
        pid = self.current_project_id
        task_label = f"{pid} {tid}" + (f" {tname}" if tname else "")
        if self.current_task.deleting:
            self.notify(f"Task {task_label} is already deleting.")
            return

        self._log_debug(f"delete: start project_id={pid} task_id={tid}")
        self.notify(f"Deleting task {task_label}...")

        self.current_task.deleting = True
        task_list = self.query_one("#task-list", TaskList)
        task_list.mark_deleting(tid)
        self._update_task_details()

        mark_task_deleting(pid, tid)
        self._queue_task_delete(pid, tid, tname)

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

    # --- Shield actions ---

    def _action_shield_toggle(
        self,
        action: str,
        shield_fn: Callable[[str, Path], None],
    ) -> None:
        """Run a shield action (down/up) for the current task in a background worker."""
        if not self.current_project_id or not self.current_task:
            self.notify("No task selected.")
            return
        pid = self.current_project_id
        task = self.current_task
        tid = task.task_id
        cname = container_name(pid, task.mode or "cli", tid)

        def work() -> tuple[str, str, str | None]:
            """Execute shield action in background thread."""
            try:
                task_dir = load_project(pid).tasks_root / str(tid)
                shield_fn(cname, task_dir)
                return pid, tid, None
            except SystemExit as e:
                return pid, tid, str(e)
            except Exception as e:
                return pid, tid, str(e)

        self.run_worker(
            work,
            name=f"shield-action:{action}:{pid}:{tid}",
            group="shield-action",
            thread=True,
            exit_on_error=False,
        )

    def _action_shield_down(self) -> None:
        """Drop the shield (bypass mode) for the current task."""
        if self._notify_shield_bypassed():
            return
        self._action_shield_toggle("down", shield_down)

    def _notify_shield_bypassed(self) -> bool:
        """Warn the user and return ``True`` if the shield bypass is active."""
        if not get_shield_bypass_firewall_no_protection():
            return False
        from ..lib.security.shield import SHIELD_SECURITY_HINT

        self.notify(f"Shield unavailable (bypass_firewall_no_protection). {SHIELD_SECURITY_HINT}")
        return True

    def _action_shield_up(self) -> None:
        """Raise the shield (deny-all) for the current task."""
        if self._notify_shield_bypassed():
            return
        self._action_shield_toggle("up", shield_up)

    # --- Main-screen task pane shortcuts (c/w/X/D/s) ---

    async def action_run_cli_from_main(self) -> None:
        """Start a new CLI task from the main screen."""
        await self._action_task_start_cli()

    async def action_run_toad_from_main(self) -> None:
        """Start a new Toad task from the main screen."""
        await self._action_task_start_toad()

    async def action_delete_task_from_main(self) -> None:
        """Delete the selected task from the main screen."""
        await self.action_delete_task()

    def action_shield_down_from_main(self) -> None:
        """Drop the shield from the main screen."""
        if self._notify_shield_bypassed():
            return
        self._action_shield_toggle("down", shield_down)

    def action_shield_up_from_main(self) -> None:
        """Raise the shield from the main screen."""
        if self._notify_shield_bypassed():
            return
        self._action_shield_toggle("up", shield_up)

    async def action_login_from_main(self) -> None:
        """Login to the selected task from the main screen."""
        await self._action_login()

    async def action_run_autopilot_from_main(self) -> None:
        """Start a new autopilot task from the main screen."""
        await self._action_task_start_autopilot()

    async def action_follow_logs_from_main(self) -> None:
        """Follow logs for the selected autopilot task from the main screen."""
        await self._action_follow_logs()

    async def action_create_task_from_main(self) -> None:
        """Show the task creation modal from the main screen."""
        if not self.current_project_id:
            self.notify("No project selected.")
            return
        default_name = generate_task_name(self.current_project_id)
        await self.push_screen(
            TaskCreateScreen(default_name=default_name),
            self._on_create_task_result,
        )

    async def _on_create_task_result(self, result: "tuple[str, str] | None") -> None:
        """Dispatch create-task result to the appropriate background launcher."""
        if result is None:
            return
        name, mode = result
        if mode == "cli":
            await self._start_cli_task_background(name)
        elif mode == "toad":
            await self._start_toad_task_background(name)
        elif mode == "autopilot":
            await self._on_autopilot_name_result(name)
