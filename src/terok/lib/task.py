# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rich Task domain object (DDD Entity).

Wraps :class:`~terok.lib.containers.tasks.TaskMeta` (value object) with
behavior: run, stop, delete, rename, logs, login.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .containers.task_logs import LogViewOptions, task_logs
from .containers.task_runners import (
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_web,
)
from .containers.tasks import (
    TaskMeta,
    get_login_command,
    get_workspace_git_diff,
    task_delete,
    task_login,
    task_rename,
    task_stop,
)

if TYPE_CHECKING:
    from .core.project_model import ProjectConfig


class Task:
    """Rich task entity with identity and lifecycle behavior.

    Obtained via :meth:`Project.get_task` or :meth:`Project.create_task`.
    Delegates to the underlying service functions for backward compatibility.
    """

    def __init__(self, config: ProjectConfig, meta: TaskMeta) -> None:
        self._config = config
        self._meta = meta

    # --- Identity ---

    @property
    def id(self) -> str:
        """Return the task's numeric ID."""
        return self._meta.task_id

    @property
    def name(self) -> str:
        """Return the task's human-readable name."""
        return self._meta.name

    @property
    def mode(self) -> str | None:
        """Return the task's mode ('cli', 'web', 'run') or ``None``."""
        return self._meta.mode

    @property
    def status(self) -> str:
        """Return the effective status computed from container state + metadata."""
        return self._meta.status

    @property
    def meta(self) -> TaskMeta:
        """Return the underlying metadata value object."""
        return self._meta

    # --- Lifecycle ---

    def run_cli(self, *, agents: list[str] | None = None, preset: str | None = None) -> None:
        """Launch a CLI-mode task container."""
        task_run_cli(self._config.id, self.id, agents=agents, preset=preset)

    def run_web(
        self,
        *,
        backend: str | None = None,
        agents: list[str] | None = None,
        preset: str | None = None,
    ) -> None:
        """Launch a web-mode task container."""
        task_run_web(self._config.id, self.id, backend=backend, agents=agents, preset=preset)

    def stop(self, *, timeout: int | None = None) -> None:
        """Gracefully stop the task container."""
        task_stop(self._config.id, self.id, timeout=timeout)

    def restart(self, *, backend: str | None = None) -> None:
        """Restart the task container."""
        task_restart(self._config.id, self.id, backend=backend)

    def delete(self) -> None:
        """Delete the task (workspace, metadata, containers)."""
        task_delete(self._config.id, self.id)

    def rename(self, new_name: str) -> None:
        """Rename the task."""
        task_rename(self._config.id, self.id, new_name)

    def followup(self, prompt: str, follow: bool = True) -> None:
        """Send a follow-up prompt to a completed headless task."""
        task_followup_headless(self._config.id, self.id, prompt, follow=follow)

    # --- Observation ---

    def logs(self, options: LogViewOptions | None = None) -> None:
        """View task logs."""
        task_logs(self._config.id, self.id, options or LogViewOptions())

    def login(self) -> None:
        """Open an interactive shell in the task container."""
        task_login(self._config.id, self.id)

    def get_login_command(self) -> list[str]:
        """Return the podman exec command for login."""
        return get_login_command(self._config.id, self.id)

    def get_workspace_diff(self, against: str = "HEAD") -> str | None:
        """Get git diff from the task's workspace."""
        return get_workspace_git_diff(self._config.id, self.id, against=against)

    def __repr__(self) -> str:
        return f"Task(id={self.id!r}, name={self.name!r}, mode={self.mode!r})"
