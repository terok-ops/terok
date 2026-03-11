# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rich Task domain object — DDD Entity.

Wraps a :class:`~terok.lib.containers.tasks.TaskMeta` value object with
lifecycle behavior (run, stop, delete, rename) and observation methods
(logs, login, workspace diff).

Tasks are always obtained through a :class:`~terok.lib.project.Project`::

    project = get_project("myproj")
    task = project.create_task(name="fix-bug")
    task.run_cli()
    task.logs(LogViewOptions(follow=True))
    task.stop()

**Snapshot semantics:** a ``Task`` captures a point-in-time snapshot of
:class:`TaskMeta` at construction.  Mutations (``rename()``, ``run_cli()``,
``stop()``) modify the underlying storage but do *not* update the in-memory
snapshot.  To observe the new state after a mutation, obtain a fresh
``Task`` via ``project.get_task(id)``.  This keeps the entity free of
implicit I/O and consistent with how ``TaskMeta`` is used throughout the
codebase.

See Also:
    :mod:`terok.lib.project` — the ``Project`` aggregate that contains tasks
    :mod:`terok.lib.containers.tasks` — ``TaskMeta`` value object and
        low-level task functions
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
    """Rich task entity — DDD Entity with identity and lifecycle behavior.

    Each task has a unique identity within its project, defined by the tuple
    ``(project_id, task_id)``.  Two ``Task`` instances are equal iff they
    share this identity, regardless of metadata differences.

    Obtained via :meth:`~terok.lib.project.Project.get_task`,
    :meth:`~terok.lib.project.Project.create_task`, or
    :meth:`~terok.lib.project.Project.list_tasks`.  Delegates lifecycle
    operations to the underlying task service functions in
    :mod:`~terok.lib.containers.tasks` and
    :mod:`~terok.lib.containers.task_runners`.
    """

    __slots__ = ("_config", "_meta")

    def __init__(self, config: ProjectConfig, meta: TaskMeta) -> None:
        """Initialize with project config and task metadata snapshot."""
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

    def __eq__(self, other: object) -> bool:
        """Two tasks are equal iff they belong to the same project and share the same ID."""
        return (
            isinstance(other, Task) and self._config.id == other._config.id and self.id == other.id
        )

    def __hash__(self) -> int:
        """Hash by (project_id, task_id) for use in sets and dicts."""
        return hash((self._config.id, self.id))

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
        """Return a developer-friendly string representation."""
        return f"Task(id={self.id!r}, name={self.name!r}, mode={self.mode!r})"
