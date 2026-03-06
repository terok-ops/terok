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
import tarfile
from pathlib import Path
from typing import TypedDict

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
from .core.config import build_root, deleted_projects_dir, get_envs_base_dir, state_root
from .core.projects import load_project
from .security.auth import AUTH_PROVIDERS, AuthProvider, authenticate
from .security.gate_server import (  # noqa: F401 — re-exported public API
    GateServerStatus,
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
from .util.fs import archive_timestamp, create_archive_file

_logger = logging.getLogger(__name__)


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


class DeleteProjectResult(TypedDict):
    """Result of a project deletion."""

    deleted: list[str]
    skipped: list[str]
    archive: str | None


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
