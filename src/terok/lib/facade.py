# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Service facade — Composition Root for the terok library.

The :class:`Terok` class is the primary entry point for all library
consumers (CLI, TUI, scripts).  It wires global services and provides
factory methods for project-scoped aggregate roots.

For backward compatibility, the module also re-exports key service
functions used directly by the presentation layer.
"""

from __future__ import annotations

from functools import cached_property
from typing import TYPE_CHECKING

from .containers.docker import build_images, generate_dockerfiles
from .containers.environment import WEB_BACKENDS
from .containers.image_cleanup import (  # noqa: F401 — re-exported public API
    ImageManager,
    cleanup_images,
    find_orphaned_images,
    list_images,
)
from .containers.project_state import get_project_state, is_task_image_old
from .containers.runtime import ContainerRuntime
from .containers.task_logs import LogViewOptions, task_logs  # noqa: F401 — re-exported public API
from .containers.task_runners import (  # noqa: F401 — re-exported public API
    HeadlessRunRequest,
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_web,
)
from .containers.tasks import (  # noqa: F401 — re-exported public API
    get_tasks,
    task_archive_list,
    task_archive_logs,
    task_delete,
    task_list,
    task_login,
    task_new,
    task_rename,
    task_status,
    task_stop,
)
from .core.projects import load_project
from .project import (  # noqa: F401 — re-exported public API
    DeleteProjectResult,
    Project,
    _archive_project,
    delete_project,
)
from .security.auth import AUTH_PROVIDERS, AuthManager, AuthProvider, authenticate
from .security.gate_server import (  # noqa: F401 — re-exported public API
    GateServerManager,
    GateServerStatus,
    check_units_outdated,
    get_gate_base_path,
    get_gate_server_port,
    get_server_status,
    install_systemd_units,
    is_daemon_running,
    is_systemd_available,
    start_daemon,
    stop_daemon,
    uninstall_systemd_units,
)
from .security.git_gate import (
    GateStalenessInfo,
    compare_gate_vs_upstream,
    find_projects_sharing_gate,
    get_gate_last_commit,
    sync_gate_branches,
    sync_project_gate,
)
from .security.ssh import init_project_ssh

if TYPE_CHECKING:
    from .core.project_model import ProjectConfig


# ---------------------------------------------------------------------------
# Terok — Composition Root
# ---------------------------------------------------------------------------


class Terok:
    """Composition Root — primary entry point for the terok library.

    Wires global services and provides factory methods for project-scoped
    aggregate roots.  CLI and TUI instantiate this once and call methods.

    Usage::

        terok = Terok()
        project = terok.get_project("myproj")
        task = project.create_task(name="fix-bug")
        task.run_cli()
    """

    # --- Project access (Abstract Factory) ---

    @staticmethod
    def get_project(project_id: str) -> Project:
        """Load a project by ID and return a rich :class:`Project` aggregate."""
        config: ProjectConfig = load_project(project_id)
        return Project(config)

    @staticmethod
    def list_projects() -> list[Project]:
        """Return all known projects as rich :class:`Project` aggregates."""
        from .core.projects import list_projects as _list_projects

        return [Project(cfg) for cfg in _list_projects()]

    @staticmethod
    def derive_project(source_id: str, new_id: str) -> Project:
        """Derive a new project from an existing one and return it."""
        from .core.projects import derive_project as _derive_project

        _derive_project(source_id, new_id)
        config: ProjectConfig = load_project(new_id)
        return Project(config)

    def delete_project(self, project_id: str) -> DeleteProjectResult:
        """Delete a project and all its associated data."""
        return delete_project(project_id)

    # --- Global services (composed singletons) ---

    @cached_property
    def gate_server(self) -> GateServerManager:
        """Return the global gate server manager."""
        return GateServerManager()

    @cached_property
    def auth(self) -> AuthManager:
        """Return the global authentication manager."""
        return AuthManager()

    @cached_property
    def images(self) -> ImageManager:
        """Return the global image manager."""
        return ImageManager()

    @cached_property
    def runtime(self) -> ContainerRuntime:
        """Return the global container runtime gateway."""
        return ContainerRuntime()

    def __repr__(self) -> str:
        return "Terok()"


def maybe_pause_for_ssh_key_registration(project_id: str) -> None:
    """If the project's upstream uses SSH, pause so the user can register the deploy key.

    Call this right after ``init_project_ssh()`` — the public key will already
    have been printed to the terminal.  For HTTPS upstreams this is a no-op.
    """
    project = load_project(project_id)
    upstream = project.upstream_url or ""
    if upstream.startswith("git@") or upstream.startswith("ssh://"):
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Add the public key shown above as a")
        print("deploy key (or to your SSH keys) on the git remote.")
        print("=" * 60)
        input("Press Enter once the key is registered... ")


__all__ = [
    # Composition Root
    "Terok",
    # Rich domain objects
    "Project",
    # Docker / image management
    "generate_dockerfiles",
    "build_images",
    # Image listing & cleanup
    "list_images",
    "find_orphaned_images",
    "cleanup_images",
    # Environment
    "WEB_BACKENDS",
    # Project lifecycle
    "delete_project",
    "DeleteProjectResult",
    # Task lifecycle
    "task_new",
    "task_delete",
    "task_rename",
    "task_login",
    "task_list",
    "task_status",
    "task_stop",
    "task_archive_list",
    "task_archive_logs",
    "get_tasks",
    # Task runners
    "task_run_cli",
    "task_run_web",
    "task_run_headless",
    "HeadlessRunRequest",
    "task_restart",
    "task_followup_headless",
    # Task logs
    "task_logs",
    "LogViewOptions",
    # Security setup
    "init_project_ssh",
    "sync_project_gate",
    # Workflow helpers
    "maybe_pause_for_ssh_key_registration",
    # Auth
    "AUTH_PROVIDERS",
    "AuthProvider",
    "authenticate",
    # Gate server
    "GateServerStatus",
    "check_units_outdated",
    "get_server_status",
    "get_gate_base_path",
    "get_gate_server_port",
    "install_systemd_units",
    "uninstall_systemd_units",
    "start_daemon",
    "stop_daemon",
    "is_daemon_running",
    "is_systemd_available",
    # Git gate
    "compare_gate_vs_upstream",
    "sync_gate_branches",
    "get_gate_last_commit",
    "GateStalenessInfo",
    "find_projects_sharing_gate",
    # Project state
    "get_project_state",
    "is_task_image_old",
]
