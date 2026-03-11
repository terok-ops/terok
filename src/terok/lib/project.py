# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rich Project domain object (DDD Aggregate Root).

Wraps :class:`~terok.lib.core.project_model.ProjectConfig` (value object)
with behavior and acts as the entry point for all project-scoped operations,
including task access.
"""

from __future__ import annotations

import logging
import shutil
import tarfile
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from .containers.agent_config import resolve_agent_config
from .containers.docker import build_images, generate_dockerfiles
from .containers.headless_providers import ProviderRegistry
from .containers.instructions import resolve_instructions
from .containers.project_state import get_project_state, is_task_image_old
from .containers.task_runners import HeadlessRunRequest, task_run_headless
from .containers.tasks import TaskManager, TaskMeta, get_tasks, task_delete
from .core.config import build_root, deleted_projects_dir, get_envs_base_dir, state_root
from .core.project_model import ProjectConfig
from .core.projects import list_presets, load_project
from .security.git_gate import GitGate, find_projects_sharing_gate
from .security.ssh import SSHManager
from .task import Task
from .util.fs import archive_timestamp, create_archive_file

if TYPE_CHECKING:
    from .core.project_model import PresetInfo

_logger = logging.getLogger(__name__)


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


def delete_project(project_id: str) -> DeleteProjectResult:
    """Delete a project and all its associated data.

    Removes task workspaces, task metadata, build artifacts, SSH credentials,
    the git gate (if not shared with other projects), and the project config
    directory.

    Returns a ``DeleteProjectResult`` (dict subclass) with:
    - ``deleted``: list of paths removed
    - ``skipped``: list of descriptions for items not removed
    - ``archive``: path to the ``.tar.gz`` archive, or ``None`` if archiving failed
    """
    # Archive project data before any destructive operations
    archive_path = _archive_project(project_id)
    if archive_path is None:
        raise SystemExit(
            f"Project archiving failed for '{project_id}'; aborting deletion to prevent data loss."
        )

    project = load_project(project_id)
    pid = project.id
    deleted: list[str] = []
    skipped: list[str] = []

    # 1. Delete all tasks (stops containers, removes workspaces + metadata)
    tasks = get_tasks(pid)
    for task in tasks:
        try:
            task_delete(pid, task.task_id)
            _logger.debug("Deleted task %s", task.task_id)
        except Exception as exc:
            _logger.warning("Failed to delete task %s: %s", task.task_id, exc)

    # 2. Remove tasks root directory (may still have leftover dirs)
    if project.tasks_root.is_dir():
        shutil.rmtree(project.tasks_root)
        deleted.append(str(project.tasks_root))

    # 3. Remove tasks metadata directory
    meta_dir = state_root() / "projects" / pid
    if meta_dir.is_dir():
        shutil.rmtree(meta_dir)
        deleted.append(str(meta_dir))

    # 4. Remove build artifacts
    build_dir = build_root() / pid
    if build_dir.is_dir():
        shutil.rmtree(build_dir)
        deleted.append(str(build_dir))

    # 5. Remove SSH credentials (respect configured ssh.host_dir)
    ssh_dir = project.ssh_host_dir or (get_envs_base_dir() / f"_ssh-config-{pid}")
    if ssh_dir.is_dir():
        shutil.rmtree(ssh_dir)
        deleted.append(str(ssh_dir))

    # 6. Remove git gate (only if not shared with other projects)
    sharing = find_projects_sharing_gate(project.gate_path, exclude_project=pid)
    if project.gate_path.exists():
        if sharing:
            names = ", ".join(pid for pid, _ in sharing)
            skipped.append(f"Gate {project.gate_path} shared with: {names}")
        else:
            shutil.rmtree(project.gate_path)
            deleted.append(str(project.gate_path))

    # 7. Remove staging root (gatekeeping mode)
    if project.staging_root and project.staging_root.is_dir():
        shutil.rmtree(project.staging_root)
        deleted.append(str(project.staging_root))

    # 8. Remove project config directory
    if project.root.is_dir():
        shutil.rmtree(project.root)
        deleted.append(str(project.root))

    return DeleteProjectResult(
        deleted=deleted,
        skipped=skipped,
        archive=archive_path,
    )


# ---------------------------------------------------------------------------
# Agent configuration manager
# ---------------------------------------------------------------------------


class AgentManager:
    """Strategy + Config Stack for agent configuration (project-scoped).

    Resolves layered agent config and provider selection for a project.
    """

    def __init__(self, config: ProjectConfig, provider_registry: ProviderRegistry) -> None:
        self._config = config
        self._registry = provider_registry

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

    def get_provider(self, name: str | None = None):
        """Resolve the active headless provider for this project."""
        return self._registry.get(name, self._config)


class Project:
    """Rich project object — Aggregate Root (DDD).

    Entry point for all project-scoped operations including task lifecycle,
    agent configuration, security setup, and image building.

    Obtain via :meth:`Terok.get_project`.
    """

    def __init__(self, config: ProjectConfig) -> None:
        self._config = config

    # --- Identity (delegates to frozen ProjectConfig) ---

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

    # --- Task containment (Factory Method) ---

    def create_task(self, *, name: str | None = None) -> Task:
        """Create a new task and return a rich Task entity."""
        mgr = TaskManager()
        task_id = mgr.create(self._config, name=name)
        meta = mgr.get_meta(self._config.id, task_id)
        return Task(self._config, meta)

    def get_task(self, task_id: str) -> Task:
        """Return a rich Task entity for an existing task."""
        mgr = TaskManager()
        meta = mgr.get_meta(self._config.id, task_id)
        return Task(self._config, meta)

    def list_tasks(self, *, status: str | None = None, mode: str | None = None) -> list[Task]:
        """Return all tasks, optionally filtered by status or mode."""
        metas = get_tasks(self._config.id)
        if mode:
            metas = [m for m in metas if m.mode == mode]
        if status:
            metas = [m for m in metas if m.status == status]
        return [Task(self._config, m) for m in metas]

    def run_headless(self, request: HeadlessRunRequest) -> Task:
        """Create and run a headless task atomically.  Returns the Task."""
        task_id = task_run_headless(request)
        mgr = TaskManager()
        meta = mgr.get_meta(self._config.id, task_id)
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

    @cached_property
    def gate(self) -> GitGate:
        """Return the project-scoped git gate manager."""
        return GitGate(self._config)

    @cached_property
    def ssh(self) -> SSHManager:
        """Return the project-scoped SSH manager."""
        return SSHManager(self._config)

    # --- Agent config ---

    @cached_property
    def agents(self) -> AgentManager:
        """Return the project-scoped agent configuration manager."""
        return AgentManager(self._config, ProviderRegistry())

    def list_presets(self) -> list[PresetInfo]:
        """Return available presets for this project."""
        return list_presets(self._config.id)

    def __repr__(self) -> str:
        return f"Project(id={self.id!r}, security={self.security_class!r})"
