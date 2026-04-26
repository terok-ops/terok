# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Rich Project domain object — DDD Aggregate Root.

The central domain object in terok's architecture.  [`Project`][terok.lib.domain.project.Project] wraps a
[`ProjectConfig`][terok.lib.core.project_model.ProjectConfig] value object with
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

This module also contains [`delete_project`][terok.lib.domain.project.delete_project] and its helpers, which
handle the full teardown of a project including archiving, task cleanup,
and safe removal of managed directories.

See Also:
    [`terok.lib.domain.facade`][terok.lib.domain.facade] — factory functions that return ``Project``
    [`terok.lib.domain.task`][terok.lib.domain.task] — the ``Task`` entity contained by ``Project``
    [`terok.lib.core.project_model`][terok.lib.core.project_model] — the ``ProjectConfig`` value object
"""

from __future__ import annotations

import json
import logging
import shutil
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from terok_executor import (
    ACPEndpointStatus,
    get_provider,
    list_authenticated_agents,
    resolve_instructions,
)
from terok_sandbox import GitGate, SSHManager

from ..core.config import (
    archive_dir,
    build_dir,
    make_sandbox_config,
    projects_dir,
    sandbox_live_dir,
    user_projects_dir,
    vault_dir,
)
from ..core.paths import acp_bound_path, acp_socket_path, core_state_dir
from ..core.project_model import ProjectConfig
from ..core.projects import list_presets, load_project
from ..orchestration.agent_config import resolve_agent_config
from ..orchestration.image import build_images, generate_dockerfiles
from ..orchestration.task_runners import HeadlessRunRequest, task_run_headless
from ..orchestration.tasks import (
    TaskMeta,
    get_all_task_states,
    get_task_meta,
    get_tasks,
    task_delete,
    task_new,
)
from ..util.fs import archive_timestamp, create_archive_file
from .project_state import get_project_state, is_task_image_old
from .task import Task

if TYPE_CHECKING:
    from terok_executor import AgentProvider

    from ..core.project_model import PresetInfo

_logger = logging.getLogger(__name__)


def _is_under_terok_root(path: Path) -> bool:
    """Return True if *path* is under a known Terok-managed directory.

    Used as a safety guard before ``rmtree()`` on user-configurable paths
    to prevent accidentally deleting directories outside Terok storage
    (e.g. ``~/.ssh`` set as ``ssh.host_dir``).
    """
    resolved = path.resolve()
    managed_roots = [
        projects_dir(),
        user_projects_dir(),
        core_state_dir(),
        sandbox_live_dir(),
        make_sandbox_config().state_dir,
        vault_dir(),
        build_dir(),
        archive_dir(),
    ]
    return any(resolved == root or root in resolved.parents for root in managed_roots)


# ---------------------------------------------------------------------------
# Gate sharing validation (multi-project coordination — terok policy)
# ---------------------------------------------------------------------------


def find_projects_sharing_gate(
    gate_path: Path, exclude_project: str | None = None
) -> list[tuple[str, str | None]]:
    """Find all projects configured to use the same gate path.

    Args:
        gate_path: The gate path to check for
        exclude_project: Project ID to exclude from results (usually the current project)

    Returns:
        List of (project_id, upstream_url) tuples for projects sharing this gate
    """
    from ..core.projects import list_projects as _list_projects

    gate_path = gate_path.resolve()
    return [
        (project.id, project.upstream_url)
        for project in _list_projects()
        if project.id != exclude_project and project.gate_path.resolve() == gate_path
    ]


def validate_gate_upstream_match(project_id: str) -> None:
    """Validate that no other project uses the same gate with a different upstream.

    Raises SystemExit if another project uses the same gate path but has a
    different upstream_url configured.

    Args:
        project_id: The project to validate
    """
    project = load_project(project_id)
    sharing = find_projects_sharing_gate(project.gate_path, exclude_project=project_id)

    for other_id, other_url in sharing:
        if other_url is None or project.upstream_url is None or other_url != project.upstream_url:
            this_display = (
                project.upstream_url if project.upstream_url is not None else "<not configured>"
            )
            other_display = other_url if other_url is not None else "<not configured>"
            missing_note = ""
            if other_url is None or project.upstream_url is None:
                missing_note = (
                    "\nNote: One or more projects sharing this gate do not have an "
                    "upstream_url configured in project.yml.\n"
                )
            raise SystemExit(
                f"Gate path conflict detected!\n"
                f"\n"
                f"  Gate path: {project.gate_path}\n"
                f"\n"
                f"  This project ({project_id}):\n"
                f"    upstream_url: {this_display}\n"
                f"\n"
                f"  Conflicting project ({other_id}):\n"
                f"    upstream_url: {other_display}\n"
                f"\n"
                f"Projects sharing a gate must have the same upstream_url.\n"
                f"Either change the gate.path in one project's project.yml,\n"
                f"or ensure both projects point to the same upstream repository.\n"
                f"{missing_note}"
            )


def make_git_gate(config: ProjectConfig, *, use_personal_ssh: bool | None = None) -> GitGate:
    """Construct a `GitGate` from a [`ProjectConfig`][terok.cli.commands.sickbay.ProjectConfig] (adapter factory).

    Injects ``validate_gate_upstream_match`` as the gate validation callback.
    The ``use_personal_ssh`` flag resolves per-invocation override (e.g.
    ``terok gate-sync --use-personal-ssh``) > per-project YAML
    (``ssh.use_personal``) > default ``False``.
    """
    effective = use_personal_ssh if use_personal_ssh is not None else config.ssh_use_personal
    return GitGate(
        scope=config.id,
        gate_path=config.gate_path,
        upstream_url=config.upstream_url,
        default_branch=config.default_branch,
        use_personal_ssh=effective,
        validate_gate_fn=validate_gate_upstream_match,
        clone_cache_base=make_sandbox_config().clone_cache_base_path,
    )


def make_ssh_manager(config: ProjectConfig) -> SSHManager:
    """Return an `SSHManager` for *config* that owns its vault DB.

    Use it as a context manager (``with make_ssh_manager(cfg) as m: ...``);
    the DB connection closes on exit.
    """
    return SSHManager.open(scope=config.id, db_path=make_sandbox_config().db_path)


# ---------------------------------------------------------------------------
# Project deletion helpers
# ---------------------------------------------------------------------------


class DeleteProjectResult(TypedDict):
    """Result of a project deletion."""

    deleted: list[str]
    skipped: list[str]
    archive: str | None


@dataclass(frozen=True)
class ACPEndpoint:
    """One per-task ACP endpoint as visible from the host.

    Constructed by :meth:`Project.acp_endpoints`; consumed by the CLI
    (``terok acp list``) and the TUI panel.  Carries enough state to
    render a status row without forcing the listing path to actually
    probe or open the socket.
    """

    project_id: str
    """The owning project's id."""

    task_id: str
    """The task this endpoint serves."""

    socket_path: Path
    """Where the proxy daemon would bind (or has bound) the socket.

    The path is computed deterministically from the task id and may not
    yet exist on disk — :attr:`status` records whether it does.
    """

    status: ACPEndpointStatus
    """Live state — ``active``, ``ready``, or ``unsupported``."""

    bound_agent: str | None = None
    """Set only when ``status == ACTIVE`` and the daemon has bound an
    agent for the open session; ``None`` otherwise."""


def _read_bound_agent(project_id: str, task_id: str) -> str | None:
    """Read the bound-agent name from the proxy daemon's sidecar JSON.

    The daemon writes ``{"agent": "<name>"}`` atomically (via os.replace)
    when a session binds.  Tolerates partial / missing files — every
    error path collapses to ``None`` so the listing surface keeps
    working when the daemon is mid-update or the file is absent.
    """
    path = acp_bound_path(project_id, task_id)
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    agent = payload.get("agent") if isinstance(payload, dict) else None
    return agent if isinstance(agent, str) else None


def _task_has_any_authed_agent(
    project_id: str,
    task: Task,
    authed: set[str],
    *,
    sandbox: Any,
    label_cache: dict[str, set[str]],
) -> bool:
    """Return ``True`` if the task's image declares any agent in *authed*.

    The image label is the source of truth for "what agents could this
    task run"; intersecting with the live auth set tells us whether
    ``acp connect`` would succeed.  Empty image labels surface as
    ``unsupported`` rather than failing at connect time — that case
    is real for legacy task images pre-dating the agents label.

    *sandbox* and *label_cache* are threaded through from the listing
    so a project with N running tasks does not pay N podman inspects
    or N sandbox constructions.
    """
    image_agents = _image_agents_for_task(
        project_id, task, sandbox=sandbox, label_cache=label_cache
    )
    return bool(image_agents & authed) if image_agents else False


def _image_agents_for_task(
    project_id: str,
    task: Task,
    *,
    sandbox: Any,
    label_cache: dict[str, set[str]],
) -> set[str]:
    """Return the agent set declared on a running task's container image.

    Reads ``ai.terok.agents`` (CSV) via the runtime — memoised in
    *label_cache* by image-id so co-located tasks (most projects ship
    one image per security class) share one inspect.  Returns an empty
    set on the expected error paths (podman unreachable, container
    gone, image absent) so the caller can classify the endpoint
    cleanly; any other error propagates so real bugs surface.
    """
    from terok_executor import AGENTS_LABEL

    from ..orchestration.tasks import container_name

    try:
        cname = container_name(project_id, task.meta.mode, task.task_id)
        container = sandbox.runtime.container(cname)
        image = container.image
        if image is None:
            return set()
        cache_key = image.id or image.ref or cname
        if cache_key in label_cache:
            return label_cache[cache_key]
        raw = image.labels().get(AGENTS_LABEL, "")
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        _logger.debug("_image_agents_for_task(%s): %s", task.task_id, exc)
        return set()
    parsed = {token for token in (s.strip() for s in raw.split(",")) if token}
    label_cache[cache_key] = parsed
    return parsed


def _archive_project(project_id: str) -> str | None:
    """Create a compressed archive of project data before deletion.

    Collects project config, task metadata, task archives, and build
    artifacts into a ``.tar.gz`` under ``archive_dir()``.  SSH credentials
    and git gate contents are excluded for security.

    After a successful tar, the project's task-archive subtree
    (``archive/<pid>/``) is removed — freeing the project name for reuse.

    Returns the archive file path as a string, or ``None`` on failure.
    """
    try:
        project = load_project(project_id)
        pid = project.id

        archive_root = archive_dir()
        ts = archive_timestamp()
        base_name = f"{ts}_{pid}"
        archive_path = create_archive_file(archive_root, base_name)

        # Directories to include: (arcname_prefix, source_path)
        sources: list[tuple[str, Path]] = []

        # Project config
        if project.root.is_dir():
            sources.append(("config", project.root))

        # Task metadata (core state)
        project_state = core_state_dir() / "projects" / pid
        if project_state.is_dir():
            sources.append(("state", project_state))

        # Task archives (namespace archive tree)
        task_archive_path = archive_root / pid
        if task_archive_path.is_dir():
            sources.append(("task-archives", task_archive_path))

        # Build artifacts
        build_path = build_dir() / pid
        if build_path.is_dir():
            sources.append(("build", build_path))

        if not sources:
            _logger.debug("_archive_project: nothing to archive for %s", pid)
            return None

        with tarfile.open(archive_path, "w:gz") as tar:
            for prefix, src_dir in sources:
                for item in src_dir.rglob("*"):
                    if item.is_file():
                        arcname = f"{prefix}/{item.relative_to(src_dir)}"
                        tar.add(str(item), arcname=arcname)

        # Remove the project's task-archive subtree to free the name.
        # Non-fatal: the tar already succeeded.
        if task_archive_path.is_dir():
            try:
                shutil.rmtree(task_archive_path)
            except OSError as cleanup_exc:
                _logger.warning(
                    "_archive_project: task-archive cleanup failed for %s: %s",
                    pid,
                    cleanup_exc,
                )

        _logger.debug("_archive_project: archived %s to %s", pid, archive_path)
        return str(archive_path)
    except Exception as exc:
        _logger.warning("_archive_project: failed to archive %s: %s", project_id, exc)
        return None


def _unassign_vault_ssh_keys(scope: str, deleted: list[str]) -> None:
    """Drop every SSH-key assignment for *scope*; record the count in *deleted*."""
    from .vault import vault_db

    with vault_db() as db:
        count = db.unassign_all_ssh_keys(scope)
    if count:
        deleted.append(f"{count} SSH key assignment(s) for project (scope) {scope!r}")


def _rmtree_managed(path: Path, label: str, deleted: list[str], skipped: list[str]) -> None:
    """Remove a directory if it exists under a Terok-managed root.

    A safety guard: user-configurable paths (``gate_path``, ``staging_root``)
    might point outside managed storage — in that case the directory is
    skipped rather than deleted.
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

    # 3-4. Remove state dir, build artifacts, and any remaining task archives
    for d in (core_state_dir() / "projects" / pid, build_dir() / pid, archive_dir() / pid):
        if d.is_dir():
            shutil.rmtree(d)
            deleted.append(str(d))

    # 5. SSH credentials — unassign from vault; orphan keys cascade-delete.
    _unassign_vault_ssh_keys(pid, deleted)

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
    project.  Used by [`Project`][terok.lib.domain.project.Project] via ``project.agents``.

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
        return resolve_agent_config(
            self._config.id,
            agent_config=self._config.agent_config,
            project_root=self._config.root,
            preset=preset,
            cli_overrides=cli_overrides,
        )

    def resolve_instructions(self, provider_name: str, preset: str | None = None) -> str:
        """Return resolved instructions text for the given provider."""
        effective = self.resolve_config(preset=preset)
        return resolve_instructions(effective, provider_name, project_root=self._config.root)

    def get_provider(self, name: str | None = None) -> AgentProvider:
        """Resolve the active headless provider for this project."""
        return get_provider(name, default_agent=self._config.default_agent)


class Project:
    """Rich project object — DDD Aggregate Root.

    The primary domain object that callers interact with.  Wraps a
    [`ProjectConfig`][terok.cli.commands.sickbay.ProjectConfig] value object and exposes all project-scoped
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

    Obtain via [`get_project`][terok.lib.domain.facade.get_project] or
    [`list_projects`][terok.lib.domain.facade.list_projects].
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

    def acp_endpoints(self) -> list[ACPEndpoint]:
        """Return one :class:`ACPEndpoint` per running task.

        Cheap discovery surface — walks running tasks, classifies each
        endpoint as ``active`` (daemon up, socket bound), ``ready``
        (task running with at least one authed agent, daemon would
        spawn on first connect), or ``unsupported`` (no agents authed
        for this task's image; connect would fail).

        No probing, no socket traffic — one credential-DB read for
        the whole listing, one ``Sandbox`` instance shared across
        tasks, and image-label lookups memoised by image-id (most
        tasks share an image).  ``terok acp list`` and the TUI panel
        share this entry point.
        """
        running = self.list_tasks(status="running")
        if not running:
            return []
        from terok_sandbox import Sandbox

        # One DB read + one Sandbox + per-image label cache for the
        # whole listing.  Auth is global today — same set for every task.
        authed = set(list_authenticated_agents())
        sandbox = Sandbox(config=make_sandbox_config())
        label_cache: dict[str, set[str]] = {}
        out: list[ACPEndpoint] = []
        for task in running:
            sock = acp_socket_path(self._config.id, task.task_id)
            sock_exists = sock.exists()
            bound = _read_bound_agent(self._config.id, task.task_id) if sock_exists else None
            if sock_exists:
                status = ACPEndpointStatus.ACTIVE
            elif _task_has_any_authed_agent(
                self._config.id, task, authed, sandbox=sandbox, label_cache=label_cache
            ):
                status = ACPEndpointStatus.READY
            else:
                status = ACPEndpointStatus.UNSUPPORTED
            out.append(
                ACPEndpoint(
                    project_id=self._config.id,
                    task_id=task.task_id,
                    socket_path=sock,
                    status=status,
                    bound_agent=bound,
                )
            )
        return out

    def run_headless(self, request: HeadlessRunRequest) -> Task:
        """Create and run a headless task atomically.  Returns the Task."""
        task_id = task_run_headless(request)
        meta = get_task_meta(self._config.id, task_id)
        return Task(self._config, meta)

    def followup_headless(self, task_id: str, prompt: str, follow: bool = True) -> None:
        """Send a follow-up prompt to a completed headless task."""
        from ..orchestration.task_runners import task_followup_headless

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
        self, *, include_dev: bool = False, refresh_agents: bool = False, full: bool = False
    ) -> None:
        """Build container images for this project."""
        build_images(
            self._config.id,
            include_dev=include_dev,
            refresh_agents=refresh_agents,
            full_rebuild=full,
        )

    def get_state(self) -> dict:
        """Return the project's infrastructure state."""
        return get_project_state(self._config.id, project=self._config)

    def is_task_image_old(self, task: TaskMeta) -> bool:
        """Check whether the task's container image is outdated."""
        return is_task_image_old(self._config.id, task)

    # --- Security setup ---

    @property
    def gate(self) -> GitGate:
        """Return the project-scoped git gate manager (lazy-initialized)."""
        if self._gate is None:
            self._gate = make_git_gate(self._config)
        return self._gate

    @property
    def ssh(self) -> SSHManager:
        """Return the project-scoped SSH manager (lazy-initialized)."""
        if self._ssh is None:
            self._ssh = make_ssh_manager(self._config)
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
