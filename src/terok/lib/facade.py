# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Service facade — stable import boundary for the terok library.

Re-exports key service classes and functions so that the presentation
layer (CLI, TUI) can import from a single stable module instead of
reaching into internal subpackages.

**Recommended entry points** for project-scoped operations::

    from terok.lib.facade import get_project

    project = get_project("myproj")  # → Project (Aggregate Root)
    task = project.create_task(name="x")  # → Task (Entity)
    task.run_cli()

Factory functions:

- :func:`get_project` — load a single project by ID
- :func:`list_projects` — return all known projects
- :func:`derive_project` — create a new project from an existing one

The facade also re-exports low-level service functions (``task_new``,
``task_run_cli``, ``build_images``, etc.) for callers that need direct
access without going through the ``Project`` object graph.  These are
used by CLI commands that operate on ``project_id`` strings directly.
"""

from __future__ import annotations

from .containers.docker import build_images, generate_dockerfiles
from .containers.environment import WEB_BACKENDS
from .containers.image_cleanup import (  # noqa: F401 — re-exported public API
    cleanup_images,
    find_orphaned_images,
    list_images,
)
from .containers.project_state import get_project_state, is_task_image_old
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
    delete_project,
)
from .security.auth import AUTH_PROVIDERS, AuthProvider, authenticate
from .security.gate_server import (  # noqa: F401 — re-exported public API
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
    GitGate,
    find_projects_sharing_gate,
)
from .security.shield import (  # noqa: F401 — re-exported public API
    get_shield_config,
    setup as shield_setup,
    status as shield_status,
)
from .security.ssh import SSHManager
from .task import Task  # noqa: F401 — re-exported public API

# ---------------------------------------------------------------------------
# Project factory functions
# ---------------------------------------------------------------------------


def get_project(project_id: str) -> Project:
    """Load a project by ID and return a rich :class:`Project` aggregate."""
    return Project(load_project(project_id))


def list_projects() -> list[Project]:
    """Return all known projects as rich :class:`Project` aggregates."""
    from .core.projects import list_projects as _list_projects

    return [Project(cfg) for cfg in _list_projects()]


def derive_project(source_id: str, new_id: str) -> Project:
    """Derive a new project from an existing one and return it."""
    from .core.projects import derive_project as _derive_project

    _derive_project(source_id, new_id)
    return Project(load_project(new_id))


# ---------------------------------------------------------------------------
# Workflow helpers
# ---------------------------------------------------------------------------


def maybe_pause_for_ssh_key_registration(project_id: str) -> None:
    """If the project's upstream uses SSH, pause so the user can register the deploy key.

    Call this right after ``SSHManager.init()`` — the public key will already
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
    # Project factory functions
    "get_project",
    "list_projects",
    "derive_project",
    # Rich domain objects
    "Project",
    "Task",
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
    "SSHManager",
    "GitGate",
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
    "GateStalenessInfo",
    "find_projects_sharing_gate",
    # Project state
    "get_project_state",
    "is_task_image_old",
    # Shield
    "get_shield_config",
    "shield_setup",
    "shield_status",
]
