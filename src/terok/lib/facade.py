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

from .containers.docker import build_images, generate_dockerfiles
from .containers.environment import WEB_BACKENDS
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
