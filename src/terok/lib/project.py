# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rich Project domain object — DDD Aggregate Root.

The central domain object in terok's architecture.  :class:`Project` wraps a
:class:`~terok.lib.core.project_model.ProjectConfig` value object with
lifecycle behavior and serves as the **single entry point** for all
project-scoped operations:

- **Task management** — create, list, run, and stop tasks
  (``project.create_task()``, ``project.list_tasks(status="running")``)
- **Security setup** — SSH keypairs (``project.ssh``) and git gate mirrors
  (``project.gate``)
- **Agent configuration** — layered config resolution and provider selection
  (``project.agents``)
- **Infrastructure** — Dockerfile generation, image builds, and state queries

The object graph follows DDD conventions::

    facade.get_project("myproj")  →  Project (Aggregate Root)
        .config                   →  ProjectConfig (Value Object)
        .gate                     →  GitGate (Repository + Gateway)
        .ssh                      →  SSHManager (Service)
        .agents                   →  AgentManager (Strategy + Config Stack)
        .create_task()            →  Task (Entity)
        .get_task(id)             →  Task (Entity)

Subsystems (``gate``, ``ssh``, ``agents``) are lazy-initialized on first
access — constructing a ``Project`` performs no I/O beyond loading the
config that was already resolved by the caller.

This module also contains :func:`delete_project` and its helpers, which
handle the full teardown of a project including archiving, task cleanup,
and safe removal of managed directories.

See Also:
    :mod:`terok.lib.facade` — factory functions that return ``Project``
    :mod:`terok.lib.task` — the ``Task`` entity contained by ``Project``
    :mod:`terok.lib.core.project_model` — the ``ProjectConfig`` value object
"""

from __future__ import annotations

import logging
import shutil
import tarfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from .containers.agent_config import resolve_agent_config
from .containers.docker import build_images, generate_dockerfiles
from .containers.headless_providers import HeadlessProvider, get_provider
from .containers.instructions import resolve_instructions
from .containers.project_state import get_project_state, is_task_image_old
from .containers.task_runners import HeadlessRunRequest, task_run_headless
from .containers.tasks import (
    TaskMeta,
    get_all_task_states,
    get_task_meta,
    get_tasks,
    task_delete,
    task_new,
)
from .core.config import (
    build_root,
    config_root,
    deleted_projects_dir,
    get_envs_base_dir,
    state_root,
)
from .core.project_model import ProjectConfig
from .core.projects import list_presets, load_project
from .security.git_gate import GitGate, find_projects_sharing_gate
from .security.ssh import SSHManager
from .task import Task
from .util.fs import archive_timestamp, create_archive_file

if TYPE_CHECKING:
    from .core.project_model import PresetInfo

_logger = logging.getLogger(__name__)


def _is_under_terok_root(path: Path) -> bool:
    """Return True if *path* is under a known Terok-managed directory.

    Used as a safety guard before ``rmtree()`` on user-configurable paths
    to prevent accidentally deleting directories outside Terok storage
    (e.g. ``~/.ssh`` set as ``ssh.host_dir``).
    """
    resolved = path.resolve()
    managed_roots = [config_root(), state_root(), get_envs_base_dir(), build_root()]
    return any(resolved == root or root in resolved.parents for root in managed_roots)


# ---------------------------------------------------------------------------
# Project deletion helpers
# ---------------------------------------------------------------------------


class DeleteProjectResult(TypedDict):
    """Result of a project deletion."""

    deleted: list[str]
    skipped: list[str]
    archive: str | None


def _archive_project(project_id: str) -> str | None:
    """Create a compressed archive of project data before deletion.

    Collects project config, task metadata/archives, and build artifacts
    into a ``.tar.gz`` file under ``deleted_projects_dir()``.  SSH
    credentials and git gate contents are excluded for security.

    Returns the archive file path as a string, or ``None`` on failure.
    """
    try:
        project = load_project(project_id)
        pid = project.id

        archive_root = deleted_projects_dir()
        ts = archive_timestamp()
        base_name = f"{ts}_{pid}"
        archive_path = create_archive_file(archive_root, base_name)

        # Directories to include: (arcname_prefix, source_path)
        sources: list[tuple[str, Path]] = []

        # Project config
        if project.root.is_dir():
            sources.append(("config", project.root))

        # Task metadata + task archives
        project_state = state_root() / "projects" / pid
        if project_state.is_dir():
            sources.append(("state", project_state))

        # Build artifacts
        build_dir = build_root() / pid
        if build_dir.is_dir():
            sources.append(("build", build_dir))

        if not sources:
            _logger.debug("_archive_project: nothing to archive for %s", pid)
            return None

        with tarfile.open(archive_path, "w:gz") as tar:
            for prefix, src_dir in sources:
                for item in src_dir.rglob("*"):
                    if item.is_file():
                        arcname = f"{prefix}/{item.relative_to(src_dir)}"
                        tar.add(str(item), arcname=arcname)

        _logger.debug("_archive_project: archived %s to %s", pid, archive_path)
        return str(archive_path)
    except Exception as exc:
        _logger.warning("_archive_project: failed to archive %s: %s", project_id, exc)
        return None


def _rmtree_managed(path: Path, label: str, deleted: list[str], skipped: list[str]) -> None:
    """Remove a directory if it exists under a Terok-managed root.

    A safety guard: user-configurable paths (``ssh.host_dir``, ``gate_path``,
    ``staging_root``) might point outside managed storage — in that case the
    directory is skipped rather than deleted.
    """
    if not path.is_dir():
        return
    if not _is_under_terok_root(path):
        skipped.append(f"{label} {path} outside managed storage, not deleted")
        return
    shutil.rmtree(path)
    deleted.append(str(path))


def delete_project(project_id: str) -> DeleteProjectResult:
    """Delete a project and all its associated data.

    Removes task workspaces, task metadata, build artifacts, SSH credentials,
    the git gate (if not shared with other projects), and the project config
    directory.
    """
    archive_path = _archive_project(project_id)
    if archive_path is None:
        raise SystemExit(
            f"Project archiving failed for '{project_id}'; aborting deletion to prevent data loss."
        )

    project = load_project(project_id)
    pid = project.id
    deleted: list[str] = []
    skipped: list[str] = []

    # 1. Stop + remove all tasks
    for task in get_tasks(pid):
        try:
            task_delete(pid, task.task_id)
        except Exception as exc:
            _logger.warning("Failed to delete task %s: %s", task.task_id, exc)

    # 2. Remove tasks root (may be user-configured path)
    _rmtree_managed(project.tasks_root, "Tasks root", deleted, skipped)

    # 3-4. Remove state dir and build artifacts (always managed paths)
    for d in (state_root() / "projects" / pid, build_root() / pid):
        if d.is_dir():
            shutil.rmtree(d)
            deleted.append(str(d))

    # 5. SSH credentials (may be user-configured path)
    ssh_dir = project.ssh_host_dir or (get_envs_base_dir() / f"_ssh-config-{pid}")
    _rmtree_managed(ssh_dir, "SSH dir", deleted, skipped)

    # 6. Git gate (skip if shared with other projects)
    sharing = find_projects_sharing_gate(project.gate_path, exclude_project=pid)
    if sharing:
        names = ", ".join(p for p, _ in sharing)
        skipped.append(f"Gate {project.gate_path} shared with: {names}")
    else:
        _rmtree_managed(project.gate_path, "Gate", deleted, skipped)

    # 7. Staging root (gatekeeping mode, may be user-configured path)
    if project.staging_root:
        _rmtree_managed(project.staging_root, "Staging root", deleted, skipped)

    # 8. Project config directory
    if project.root.is_dir():
        shutil.rmtree(project.root)
        deleted.append(str(project.root))

    return DeleteProjectResult(deleted=deleted, skipped=skipped, archive=archive_path)


# ---------------------------------------------------------------------------
# Agent configuration manager
# ---------------------------------------------------------------------------


class AgentManager:
    """Project-scoped agent configuration manager (Strategy + Config Stack).

    Resolves the layered agent configuration stack (global → project →
    preset → CLI overrides) and selects the active headless provider for a
    project.  Used by :class:`Project` via ``project.agents``.

    The config stack is resolved lazily on each call — the manager holds no
    cached state, so config file changes take effect immediately.
    """

    __slots__ = ("_config",)

    def __init__(self, config: ProjectConfig) -> None:
        """Initialize with a resolved project configuration."""
        self._config = config

    def resolve_config(
        self,
        preset: str | None = None,
        cli_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the merged agent config dict."""
        return resolve_agent_config(self._config.id, preset=preset, cli_overrides=cli_overrides)

    def resolve_instructions(self, provider_name: str, preset: str | None = None) -> str:
        """Return resolved instructions text for the given provider."""
        effective = self.resolve_config(preset=preset)
        return resolve_instructions(effective, provider_name, project_root=self._config.root)

    def get_provider(self, name: str | None = None) -> HeadlessProvider:
        """Resolve the active headless provider for this project."""
        return get_provider(name, self._config)


class Project:
    """Rich project object — DDD Aggregate Root.

    The primary domain object that callers interact with.  Wraps a
    :class:`ProjectConfig` value object and exposes all project-scoped
    operations through a natural OOP interface::

        project = get_project("myproj")
        task = project.create_task(name="fix-bug")
        task.run_cli()
        task.stop()
        project.gate.sync()

    **Identity** is based on ``project.id`` — two ``Project`` instances with
    the same ID compare equal and hash identically, so they work correctly
    in sets and dicts.

    **Subsystem access** (``gate``, ``ssh``, ``agents``) uses lazy
    initialization: the service objects are created on first property access
    rather than at construction time.  This avoids unnecessary I/O when only
    a subset of functionality is needed.  Uses ``__slots__`` for memory
    efficiency; ``cached_property`` is not available because it requires
    ``__dict__``.

    Obtain via :func:`~terok.lib.facade.get_project` or
    :func:`~terok.lib.facade.list_projects`.
    """

    __slots__ = ("_config", "_gate", "_ssh", "_agents")

    def __init__(self, config: ProjectConfig) -> None:
        """Initialize with a resolved project configuration."""
        self._config = config
        self._gate: GitGate | None = None
        self._ssh: SSHManager | None = None
        self._agents: AgentManager | None = None

    # --- Identity (delegates to ProjectConfig) ---

    @property
    def id(self) -> str:
        """Return the project ID."""
        return self._config.id

    @property
    def config(self) -> ProjectConfig:
        """Return the underlying configuration value object."""
        return self._config

    @property
    def security_class(self) -> str:
        """Return the project's security class ('online' or 'gatekeeping')."""
        return self._config.security_class

    def __eq__(self, other: object) -> bool:
        """Two projects are equal iff they share the same ID."""
        return isinstance(other, Project) and self.id == other.id

    def __hash__(self) -> int:
        """Hash by project ID for use in sets and dicts."""
        return hash(self.id)

    # --- Task containment (Factory Method) ---

    def create_task(self, *, name: str | None = None) -> Task:
        """Create a new task and return a rich Task entity."""
        task_id = task_new(self._config.id, name=name)
        meta = get_task_meta(self._config.id, task_id)
        return Task(self._config, meta)

    def get_task(self, task_id: str) -> Task:
        """Return a rich Task entity for an existing task."""
        meta = get_task_meta(self._config.id, task_id)
        return Task(self._config, meta)

    def list_tasks(self, *, status: str | None = None, mode: str | None = None) -> list[Task]:
        """Return all tasks, optionally filtered by status or mode."""
        metas = get_tasks(self._config.id)
        if mode:
            metas = [m for m in metas if m.mode == mode]
        # Hydrate live container state so status filtering is accurate
        live_states = get_all_task_states(self._config.id, metas)
        for m in metas:
            m.container_state = live_states.get(m.task_id)
        if status:
            metas = [m for m in metas if m.status == status]
        return [Task(self._config, m) for m in metas]

    def run_headless(self, request: HeadlessRunRequest) -> Task:
        """Create and run a headless task atomically.  Returns the Task."""
        task_id = task_run_headless(request)
        meta = get_task_meta(self._config.id, task_id)
        return Task(self._config, meta)

    def followup_headless(self, task_id: str, prompt: str, follow: bool = True) -> None:
        """Send a follow-up prompt to a completed headless task."""
        from .containers.task_runners import task_followup_headless

        task_followup_headless(self._config.id, task_id, prompt, follow=follow)

    # --- Project lifecycle ---

    def delete(self) -> DeleteProjectResult:
        """Delete the project and all associated data."""
        return delete_project(self._config.id)

    # --- Infrastructure ---

    def generate_dockerfiles(self) -> None:
        """Render and write Dockerfiles for this project."""
        generate_dockerfiles(self._config.id)

    def build_images(
        self, *, include_dev: bool = False, rebuild_agents: bool = False, full: bool = False
    ) -> None:
        """Build container images for this project."""
        build_images(
            self._config.id,
            include_dev=include_dev,
            rebuild_agents=rebuild_agents,
            full_rebuild=full,
        )

    def get_state(self) -> dict:
        """Return the project's infrastructure state."""
        return get_project_state(self._config.id)

    def is_task_image_old(self, task: TaskMeta) -> bool:
        """Check whether the task's container image is outdated."""
        return is_task_image_old(self._config.id, task)

    # --- Security setup ---

    @property
    def gate(self) -> GitGate:
        """Return the project-scoped git gate manager (lazy-initialized)."""
        if self._gate is None:
            self._gate = GitGate(self._config)
        return self._gate

    @property
    def ssh(self) -> SSHManager:
        """Return the project-scoped SSH manager (lazy-initialized)."""
        if self._ssh is None:
            self._ssh = SSHManager(self._config)
        return self._ssh

    # --- Agent config ---

    @property
    def agents(self) -> AgentManager:
        """Return the project-scoped agent configuration manager (lazy-initialized)."""
        if self._agents is None:
            self._agents = AgentManager(self._config)
        return self._agents

    def list_presets(self) -> list[PresetInfo]:
        """Return available presets for this project."""
        return list_presets(self._config.id)

    def __repr__(self) -> str:
        """Return a developer-friendly string representation."""
        return f"Project(id={self.id!r}, security={self.security_class!r})"
