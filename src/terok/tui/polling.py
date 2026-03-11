#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Polling mixin for the TerokTUI app.

Extracts upstream polling, container status polling, and auto-sync logic
from the main app module into a reusable mixin class.
"""


class PollingMixin:
    """Mixin providing upstream, container status, and gate server polling for the TUI app.

    Expects the host class to provide:
    - self.current_project_id: str | None
    - self.current_task: TaskMeta | None
    - self._staleness_info: GateStalenessInfo | None
    - self._polling_timer
    - self._polling_project_id: str | None
    - self._last_notified_stale: bool
    - self._auto_sync_cooldown: dict[str, float]
    - self._container_status_timer
    - self._gate_server_timer
    - self._last_gate_server_running: bool | None
    - self.run_worker(...)
    - self.set_interval(...)
    - self.notify(...)
    - self._log_debug(...)
    - self._refresh_project_state(...)
    """

    # ---------- Upstream polling ----------

    def _start_upstream_polling(self) -> None:
        """Start background polling for upstream changes.

        Only polls for gatekeeping projects with polling enabled and a gate initialized.
        """
        from ..lib.core.projects import load_project

        self._stop_upstream_polling()  # Stop any existing timer
        self._staleness_info = None
        self._last_notified_stale = False

        if not self.current_project_id:
            return

        try:
            project = load_project(self.current_project_id)
        except SystemExit:
            return

        # Only poll for gatekeeping projects with polling enabled
        if project.security_class != "gatekeeping":
            return
        if not project.upstream_polling_enabled:
            return
        if not project.gate_path.exists():
            return

        interval_seconds = project.upstream_polling_interval_minutes * 60
        self._polling_project_id = self.current_project_id

        # Perform initial poll immediately (in background worker)
        self._poll_upstream()

        # Schedule recurring polls
        self._polling_timer = self.set_interval(
            interval_seconds, self._poll_upstream, name="upstream_polling"
        )

    def _stop_upstream_polling(self) -> None:
        """Stop the upstream polling timer."""
        if self._polling_timer is not None:
            self._polling_timer.stop()
            self._polling_timer = None
        self._polling_project_id = None

    def _start_container_status_polling(self) -> None:
        """Start background polling for container status every 2 seconds."""
        self._stop_container_status_polling()
        if not self.current_project_id:
            return
        # Poll every 2 seconds - podman inspect is fast (~10-50ms)
        interval_seconds = 2
        # Initial poll
        self._poll_container_status()
        # Schedule recurring polls
        self._container_status_timer = self.set_interval(
            interval_seconds, self._poll_container_status, name="container_status_polling"
        )

    def _stop_container_status_polling(self) -> None:
        """Stop the container status polling timer."""
        if self._container_status_timer is not None:
            self._container_status_timer.stop()
            self._container_status_timer = None

    # ---------- Gate server polling ----------

    def _start_gate_server_polling(self) -> None:
        """Start background polling for gate server status every 60 seconds."""
        self._stop_gate_server_polling()
        self._poll_gate_server()
        self._gate_server_timer = self.set_interval(
            60, self._poll_gate_server, name="gate_server_polling"
        )

    def _stop_gate_server_polling(self) -> None:
        """Stop the gate server status polling timer."""
        if self._gate_server_timer is not None:
            self._gate_server_timer.stop()
            self._gate_server_timer = None

    def _poll_gate_server(self) -> None:
        """Check gate server status in a background worker."""
        from ..lib.facade import get_server_status

        self.run_worker(
            get_server_status,
            name="gate-server-poll",
            group="gate-server-poll",
            exclusive=True,
            thread=True,
            exit_on_error=False,
        )

    def _poll_container_status(self) -> None:
        """Check container status for all visible tasks via a single batch query."""
        if not self.current_project_id:
            return
        self._queue_container_state_check(self.current_project_id)

    def _queue_container_state_check(self, project_id: str) -> None:
        """Queue a background batch check for all task container states."""
        self.run_worker(
            self._load_container_state_worker(project_id),
            name=f"container-state:{project_id}",
            group="container-state",
            exclusive=True,
        )

    async def _load_container_state_worker(
        self, project_id: str
    ) -> tuple[str, dict[str, str | None]]:
        """Background worker to batch-query all container states for a project."""
        import asyncio

        from ..lib.containers.tasks import get_all_task_states, get_tasks

        try:
            tasks = await asyncio.get_event_loop().run_in_executor(None, get_tasks, project_id)
            states = await asyncio.get_event_loop().run_in_executor(
                None, get_all_task_states, project_id, tasks
            )
            return (project_id, states)
        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            self._log_debug(f"container state batch check error: {e}")
            return (project_id, {})

    def _poll_upstream(self) -> None:
        """Check upstream for changes and update staleness info.

        Runs the actual comparison in a background worker to avoid blocking the UI.
        """
        project_id = self._polling_project_id
        if not project_id or project_id != self.current_project_id:
            # Project changed since timer was started, skip this poll
            return

        self._log_debug(f"polling upstream for {project_id}")
        # Run blocking git operation in background worker
        self.run_worker(
            self._poll_upstream_worker(project_id),
            name="poll_upstream",
            exclusive=True,  # Cancel any previous poll still running
        )

    async def _poll_upstream_worker(self, project_id: str) -> None:
        """Background worker to check upstream (runs in thread pool)."""
        import asyncio

        from ..lib.core.projects import load_project
        from ..lib.security.git_gate import GitGate

        try:
            # Run blocking call in thread pool
            staleness = await asyncio.get_event_loop().run_in_executor(
                None, lambda: GitGate(load_project(project_id)).compare_vs_upstream()
            )

            # Validate project hasn't changed while we were polling
            if project_id != self.current_project_id:
                return

            self._on_staleness_updated(project_id, staleness)

        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            self._log_debug(f"upstream poll error: {e}")

    def _on_staleness_updated(self, project_id: str, staleness) -> None:
        """Handle updated staleness info."""
        # Double-check project hasn't changed
        if project_id != self.current_project_id:
            return

        self._staleness_info = staleness

        # Only update notification state for valid (non-error) comparisons
        if staleness.error:
            # Don't change notification state on errors - preserve previous state
            pass
        elif staleness.is_stale and not self._last_notified_stale:
            behind_str = ""
            if staleness.commits_behind is not None:
                behind_str = f" ({staleness.commits_behind} commits behind)"
            self.notify(f"Gate is behind upstream on {staleness.branch}{behind_str}")
            self._last_notified_stale = True

            # Trigger auto-sync if enabled (with cooldown check)
            self._maybe_auto_sync(project_id)
        elif not staleness.is_stale:
            # Only reset when we have confirmed up-to-date status
            self._last_notified_stale = False

        # Refresh the project state display
        self._refresh_project_state()

    def _maybe_auto_sync(self, project_id: str) -> None:
        """Trigger auto-sync if enabled for this project.

        Runs sync in background worker to avoid blocking UI.
        Implements cooldown to prevent sync loops.
        """
        import time

        from ..lib.core.projects import load_project

        if not project_id or project_id != self.current_project_id:
            return

        # Check cooldown (5 minute minimum between auto-syncs per project)
        now = time.time()
        cooldown_until = self._auto_sync_cooldown.get(project_id, 0)
        if now < cooldown_until:
            self._log_debug("auto-sync skipped: cooldown active")
            return

        try:
            project = load_project(project_id)
            if not project.auto_sync_enabled:
                return

            # Set cooldown before starting sync (5 minutes)
            self._auto_sync_cooldown[project_id] = now + 300

            self._log_debug(f"auto-syncing gate for {project_id}")
            self.notify("Auto-syncing gate from upstream...")

            # Run sync in background worker
            branches = project.auto_sync_branches or None
            self.run_worker(
                self._sync_worker(project_id, branches, is_auto=True),
                name="auto_sync",
                exclusive=True,
            )

        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            self._log_debug(f"auto-sync error: {e}")

    async def _sync_worker(
        self, project_id: str, branches: list = None, is_auto: bool = False
    ) -> None:
        """Background worker to sync gate from upstream."""
        import asyncio

        from ..lib.core.projects import load_project
        from ..lib.security.git_gate import GitGate

        try:
            # Run blocking sync in thread pool
            result = await asyncio.get_event_loop().run_in_executor(
                None, lambda: GitGate(load_project(project_id)).sync_branches(branches)
            )

            # Validate project hasn't changed
            if project_id != self.current_project_id:
                return

            if result["success"]:
                label = "Auto-synced" if is_auto else "Synced"
                self.notify(f"{label} gate from upstream")

                # Re-check staleness after sync
                staleness = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: GitGate(load_project(project_id)).compare_vs_upstream()
                )

                if project_id == self.current_project_id:
                    self._staleness_info = staleness
                    # Only reset notification flag if we're actually up-to-date now
                    if not staleness.is_stale and not staleness.error:
                        self._last_notified_stale = False
                    self._refresh_project_state()
            else:
                label = "Auto-sync" if is_auto else "Sync"
                self.notify(f"{label} failed: {', '.join(result['errors'])}")

        except (Exception, SystemExit) as e:  # noqa: BLE001 — background worker; must not crash TUI
            label = "Auto-sync" if is_auto else "Sync"
            self.notify(f"{label} error: {e}")
