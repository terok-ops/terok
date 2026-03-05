# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Service facade for common cross-cutting operations.

Provides a single entry point for operations that both the CLI and TUI
frontends use, reducing the number of direct service-module imports
required by the presentation layer.

The facade re-exports key service functions and provides composite
helpers for multi-step workflows like project initialization.
"""

import logging
import shutil

from .containers.docker import build_images, generate_dockerfiles
from .containers.environment import WEB_BACKENDS
from .containers.project_state import get_project_state, is_task_image_old
from .containers.task_logs import task_logs  # noqa: F401 — re-exported public API
from .containers.task_runners import (  # noqa: F401 — re-exported public API
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_web,
)
from .containers.tasks import (  # noqa: F401 — re-exported public API
    get_tasks,
    task_delete,
    task_list,
    task_login,
    task_new,
    task_rename,
    task_status,
    task_stop,
)
from .core.config import build_root, get_envs_base_dir, state_root
from .core.projects import load_project
from .security.auth import AUTH_PROVIDERS, AuthProvider, authenticate
from .security.git_gate import (
    GateStalenessInfo,
    compare_gate_vs_upstream,
    find_projects_sharing_gate,
    get_gate_last_commit,
    sync_gate_branches,
    sync_project_gate,
)
from .security.ssh import init_project_ssh

_logger = logging.getLogger(__name__)


def delete_project(project_id: str) -> dict[str, list[str]]:
    """Delete a project and all its associated data.

    Removes task workspaces, task metadata, build artifacts, SSH credentials,
    the git gate (if not shared with other projects), and the project config
    directory.

    Returns a dict with ``deleted`` (list of paths removed) and ``skipped``
    (list of descriptions for items that were not removed).
    """
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

    return {"deleted": deleted, "skipped": skipped}


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
    # Docker / image management
    "generate_dockerfiles",
    "build_images",
    # Environment
    "WEB_BACKENDS",
    # Project lifecycle
    "delete_project",
    # Task lifecycle
    "task_new",
    "task_delete",
    "task_rename",
    "task_login",
    "task_list",
    "task_status",
    "task_stop",
    "get_tasks",
    # Task runners
    "task_run_cli",
    "task_run_web",
    "task_run_headless",
    "task_restart",
    "task_followup_headless",
    # Task logs
    "task_logs",
    # Security setup
    "init_project_ssh",
    "sync_project_gate",
    # Workflow helpers
    "maybe_pause_for_ssh_key_registration",
    # Auth
    "AUTH_PROVIDERS",
    "AuthProvider",
    "authenticate",
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
