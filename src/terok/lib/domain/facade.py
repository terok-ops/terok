# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Service facade — stable import boundary for the terok library.

Re-exports key service classes and functions so that the presentation
layer (CLI, TUI) can import from a single stable module instead of
reaching into internal subpackages.

**Recommended entry points** for project-scoped operations::

    from terok.lib.domain.facade import get_project

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

from typing import TYPE_CHECKING

from terok_executor import (
    authenticate as _authenticate_raw,
)

if TYPE_CHECKING:
    from terok_sandbox.credentials.ssh import SSHInitResult

from ..core.images import project_cli_image
from ..core.projects import derive_project as _derive_project, load_project
from ..orchestration.image import build_images, generate_dockerfiles, image_exists
from ..orchestration.task_runners import (  # noqa: F401 — re-exported public API
    HeadlessRunRequest,
    task_followup_headless,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
)
from ..orchestration.tasks import (  # noqa: F401 — re-exported public API
    TaskDeleteResult,
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
from .image_cleanup import (  # noqa: F401 — re-exported public API
    cleanup_images,
    find_orphaned_images,
    list_images,
)
from .project import (  # noqa: F401 — re-exported public API
    DeleteProjectResult,
    Project,
    delete_project,
    find_projects_sharing_gate,
    make_git_gate,
    make_ssh_manager,
)
from .project_state import get_project_state, is_task_image_old
from .task import Task  # noqa: F401 — re-exported public API
from .task_logs import LogViewOptions, task_logs  # noqa: F401 — re-exported public API
from .vault import vault_db  # noqa: F401 — re-exported public API

# ---------------------------------------------------------------------------
# Project factory functions
# ---------------------------------------------------------------------------


def get_project(project_id: str) -> Project:
    """Load a project by ID and return a rich :class:`Project` aggregate."""
    return Project(load_project(project_id))


def project_image_exists(project_id: str) -> bool:
    """Return ``True`` when the project's L2 CLI image is present locally."""
    return image_exists(project_cli_image(project_id))


def list_projects() -> list[Project]:
    """Return all known projects as rich :class:`Project` aggregates."""
    from ..core.projects import list_projects as _list_projects

    return [Project(cfg) for cfg in _list_projects()]


def derive_project(source_id: str, new_id: str) -> Project:
    """Copy *source_id*'s gate mirror and vault SSH assignments under *new_id*."""
    _derive_project(source_id, new_id)
    _share_ssh_key_assignments(source_id, new_id)
    return Project(load_project(new_id))


def _share_ssh_key_assignments(source_id: str, new_id: str) -> None:
    """Copy every SSH key assignment from *source_id* to *new_id*."""
    with vault_db() as db:
        for row in db.list_ssh_keys_for_scope(source_id):
            db.assign_ssh_key(new_id, row.id)


# ---------------------------------------------------------------------------
# SSH provisioning — the three public verbs form the user-facing ``ssh-init``
# story: mint the keypair, bind it to the project, render the result for the
# human.  ``maybe_pause_for_ssh_key_registration`` is the follow-up step for
# projects that need the public key registered upstream before gate-sync.
# ---------------------------------------------------------------------------


def provision_ssh_key(
    project_id: str,
    *,
    key_type: str = "ed25519",
    comment: str | None = None,
    force: bool = False,
) -> SSHInitResult:
    """Mint a vault-backed keypair for *project_id* and bind it to the project (scope).

    Single entry point for both the CLI and the TUI.  Rendering the
    result for the user is the caller's job — see :func:`summarize_ssh_init`.
    """
    from .project import make_ssh_manager

    project = load_project(project_id)
    with make_ssh_manager(project) as ssh:
        result = ssh.init(key_type=key_type, comment=comment, force=force)
    register_ssh_key(project_id, result["key_id"])
    return result


def register_ssh_key(project_id: str, key_id: int) -> None:
    """Bind an already-minted *key_id* to *project_id* (idempotent)."""
    with vault_db() as db:
        db.assign_ssh_key(project_id, key_id)


def summarize_ssh_init(result: SSHInitResult) -> None:
    """Render an ``ssh-init`` result for the terminal."""
    print(f"  id:          {result['key_id']}")
    print(f"  type:        {result['key_type']}")
    print(f"  fingerprint: SHA256:{result['fingerprint']}")
    print(f"  comment:     {result['comment']}")
    print("Public key (register as a deploy key on the remote):")
    print(f"  {result['public_line']}")


def maybe_pause_for_ssh_key_registration(project_id: str) -> None:
    """Pause so the user can register the deploy key, but only for SSH upstreams."""
    from terok_sandbox import is_ssh_url

    project = load_project(project_id)
    if is_ssh_url(project.upstream_url):
        print("\n" + "=" * 60)
        print("ACTION REQUIRED: Add the public key shown above as a")
        print("deploy key (or to your SSH keys) on the git remote.")
        print("=" * 60)
        input("Press Enter once the key is registered... ")


def authenticate(project_id: str, provider: str) -> None:
    """Run the auth flow for *provider*, injecting terok-specific config.

    Thin wrapper around the instrumentation-layer ``authenticate()`` that
    supplies ``mounts_dir`` and ``image`` from terok's config/image system.
    When ``expose_oauth_token`` is active (exposed mode), passes
    ``expose_token`` so the real credential file is preserved instead of
    being replaced with a phantom marker.
    """
    from ..core.config import (
        get_claude_expose_oauth_token,
        is_experimental,
        sandbox_live_mounts_dir,
    )

    expose = provider == "claude" and is_experimental() and get_claude_expose_oauth_token()
    _authenticate_raw(
        project_id,
        provider,
        mounts_dir=sandbox_live_mounts_dir(),
        image=project_cli_image(project_id),
        expose_token=expose,
    )


__all__ = [
    # Project factory functions
    "get_project",
    "list_projects",
    "derive_project",
    # Rich domain objects
    "Project",
    "Task",
    # Image management
    "generate_dockerfiles",
    "build_images",
    "project_image_exists",
    # Image listing & cleanup
    "list_images",
    "find_orphaned_images",
    "cleanup_images",
    # Project lifecycle
    "delete_project",
    "DeleteProjectResult",
    # Task lifecycle
    "TaskDeleteResult",
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
    "task_run_toad",
    "task_run_headless",
    "HeadlessRunRequest",
    "task_restart",
    "task_followup_headless",
    # Task logs
    "task_logs",
    "LogViewOptions",
    # Security setup
    "make_ssh_manager",
    "make_git_gate",
    # Workflow helpers
    "provision_ssh_key",
    "register_ssh_key",
    "summarize_ssh_init",
    "vault_db",
    "maybe_pause_for_ssh_key_registration",
    # Auth
    "authenticate",
    # Project state
    "get_project_state",
    "is_task_image_old",
    "find_projects_sharing_gate",
]
