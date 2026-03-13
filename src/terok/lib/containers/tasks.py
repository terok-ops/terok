# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task metadata, lifecycle, and query operations.

Provides module-level functions for CRUD over YAML-backed task metadata.

Container runner functions (``task_run_cli``, ``task_run_web``,
``task_run_headless``, ``task_restart``) live in the companion
``task_runners`` module.  Display types and status computation live in
``task_display``.  Log viewing lives in ``task_logs``.
"""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml  # pip install pyyaml

from ..core.config import state_root
from ..core.projects import ProjectConfig, load_project
from ..util.ansi import (
    green as _green,
    red as _red,
    supports_color as _supports_color,
    yellow as _yellow,
)
from ..util.emoji import render_emoji
from ..util.fs import archive_timestamp, create_archive_dir, ensure_dir
from ..util.logging_utils import _log_debug
from .runtime import (
    container_name,
    get_container_state,
    get_project_container_states,
    stop_task_containers,
)
from .task_display import (
    STATUS_DISPLAY,
    effective_status,
    mode_info,
)
from .work_status import read_work_status


@dataclass
class TaskMeta:
    """Lightweight metadata snapshot for a single task."""

    task_id: str
    mode: str | None
    workspace: str
    web_port: int | None
    backend: str | None = None
    container_state: str | None = None
    exit_code: int | None = None
    deleting: bool = False
    preset: str | None = None
    name: str = ""
    provider: str | None = None
    unrestricted: bool | None = None
    work_status: str | None = None
    work_message: str | None = None
    shield_state: str | None = None

    @property
    def status(self) -> str:
        """Compute effective status from live container state + metadata."""
        return effective_status(self)


TASK_NAME_MAX_LEN = 60
"""Maximum length of a sanitized task name."""


def sanitize_task_name(raw: str | None) -> str | None:
    """Sanitize a raw task name into a slug-style identifier.

    Strips whitespace, lowercases, replaces spaces with hyphens,
    removes characters outside ``[a-z0-9_-]``, collapses consecutive
    hyphens, strips trailing hyphens, and truncates to
    ``TASK_NAME_MAX_LEN``.  Returns ``None`` if the result is empty.

    Leading hyphens are preserved so callers can detect and reject them
    (a name starting with ``-`` looks like a CLI flag).
    """
    if raw is None:
        return None
    name = raw.strip().lower()
    name = name.replace(" ", "-")
    name = re.sub(r"[^a-z0-9_-]", "", name)
    name = re.sub(r"-{2,}", "-", name)
    name = name.rstrip("-")
    name = name[:TASK_NAME_MAX_LEN]
    return name or None


def validate_task_name(sanitized: str) -> str | None:
    """Return an error message if *sanitized* is not a valid task name, else ``None``.

    A name is invalid if it starts with a hyphen (looks like a CLI flag).
    Callers should first check for ``None`` from :func:`sanitize_task_name`
    (which indicates the name was empty after sanitization).
    """
    if sanitized.startswith("-"):
        return "name must not start with a hyphen"
    return None


def generate_task_name(project_id: str | None = None) -> str:
    """Generate a random human-readable task name (e.g. ``talented-toucan``).

    When *project_id* is given, name categories are resolved from config:
    project ``tasks.name_categories`` → global ``tasks.name_categories``
    → deterministic 3-category selection based on project ID hash.
    """
    import namer

    categories = _resolve_name_categories(project_id) if project_id else None
    return namer.generate(separator="-", category=categories)


def _resolve_name_categories(project_id: str) -> list[str] | None:
    """Resolve task-name categories: project config → global config → hash default."""
    from ..core.config import get_task_name_categories

    # 1. Per-project override
    try:
        project = load_project(project_id)
        if project.task_name_categories:
            return project.task_name_categories
    except SystemExit:
        pass

    # 2. Global config
    global_cats = get_task_name_categories()
    if global_cats:
        return global_cats

    # 3. Hash-based default: pick 3 categories deterministically from project ID
    return _default_categories_for_project(project_id)


def _default_categories_for_project(project_id: str) -> list[str]:
    """Pick 3 categories deterministically based on a hash of the project ID."""
    import hashlib
    import random

    import namer

    categories = sorted(namer.list_categories())
    seed = int(hashlib.md5(project_id.encode()).hexdigest(), 16)
    rng = random.Random(seed)
    return rng.sample(categories, min(3, len(categories)))


def get_task_meta(project_id: str, task_id: str) -> TaskMeta:
    """Return metadata for a single task with live container state.

    Hydrates ``container_state`` from the running container so that
    ``TaskMeta.status`` reflects current reality rather than stale YAML.
    Raises ``SystemExit`` if the task metadata file is not found.
    """
    meta_dir = tasks_meta_dir(project_id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        raise SystemExit(f"Unknown task {task_id}")
    raw = yaml.safe_load(meta_path.read_text()) or {}
    mode = raw.get("mode")
    tid = str(raw.get("task_id", ""))
    # Hydrate live container state only for tasks that have actually been started
    live_state: str | None = None
    if mode is not None:
        try:
            cname = container_name(project_id, mode, task_id)
            live_state = get_container_state(cname)
        except Exception:
            pass
    # Hydrate work status from agent-config (same logic as _get_tasks)
    ws_status: str | None = None
    ws_message: str | None = None
    if tid:
        project = load_project(project_id)
        try:
            agent_cfg = project.tasks_root / tid / "agent-config"
            ws = read_work_status(agent_cfg)
            ws_status = ws.status
            ws_message = ws.message
        except Exception:  # noqa: BLE001 — best-effort; agent-config may not exist yet
            pass
    return TaskMeta(
        task_id=tid,
        mode=mode,
        workspace=raw.get("workspace", ""),
        web_port=raw.get("web_port"),
        backend=raw.get("backend"),
        container_state=live_state,
        exit_code=raw.get("exit_code"),
        deleting=bool(raw.get("deleting")),
        preset=raw.get("preset"),
        name=raw["name"],
        provider=raw.get("provider"),
        unrestricted=raw.get("unrestricted"),
        work_status=ws_status,
        work_message=ws_message,
    )


def get_workspace_git_diff(project_id: str, task_id: str, against: str = "HEAD") -> str | None:
    """Get git diff from a task's workspace.

    Args:
        project_id: The project ID
        task_id: The task ID
        against: What to diff against ("HEAD" or "PREV")

    Returns:
        The git diff output as a string, or None if failed
    """
    try:
        project = load_project(project_id)
        tasks_root = project.tasks_root
        workspace_dir = tasks_root / task_id / "workspace-dangerous"

        if not workspace_dir.exists() or not workspace_dir.is_dir():
            return None

        # Check if this is a git repository
        git_dir = workspace_dir / ".git"
        if not git_dir.exists():
            return None

        # Determine what to diff against
        if against == "PREV":
            # Diff against previous commit
            cmd = ["git", "-C", str(workspace_dir), "diff", "HEAD~1", "HEAD"]
        else:
            # Default: diff against HEAD (uncommitted changes)
            cmd = ["git", "-C", str(workspace_dir), "diff", "HEAD"]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            # Non-zero return code indicates an error; treat as failure
            return None

        # Successful run; stdout may be empty if there is no diff
        return result.stdout

    except Exception:
        # If anything goes wrong, return None - this is a best-effort operation
        return None


# ---------- Tasks ----------


def tasks_meta_dir(project_id: str) -> Path:
    """Return the directory containing task metadata YAML files for *project_id*."""
    return state_root() / "projects" / project_id / "tasks"


def tasks_archive_dir(project_id: str) -> Path:
    """Return the directory containing archived task data for *project_id*."""
    return state_root() / "projects" / project_id / "archive"


def update_task_exit_code(project_id: str, task_id: str, exit_code: int | None) -> None:
    """Update task metadata with exit code and final status.

    Args:
        project_id: The project ID
        task_id: The task ID
        exit_code: The exit code from the task, or None if unknown/failed
    """
    meta_dir = tasks_meta_dir(project_id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        return
    meta = yaml.safe_load(meta_path.read_text()) or {}
    meta["exit_code"] = exit_code
    meta_path.write_text(yaml.safe_dump(meta))


def _write_task_readme(task_dir: Path) -> None:
    """Write a README.md explaining the task directory layout and security."""
    readme = task_dir / "README.md"
    readme.write_text(
        "# Task Directory\n"
        "\n"
        "## workspace-dangerous/\n"
        "\n"
        "This directory contains a git repository checked out from the project\n"
        "source. It is mounted into the task container at `/workspace`.\n"
        "\n"
        "**WARNING: Do not execute code or run git commands in this directory\n"
        "from the host.** The container has full write access and could have\n"
        "rewritten git hooks, checked in malicious scripts, or otherwise\n"
        "poisoned the repository contents.\n"
        "\n"
        "The safe way to interact with the repository is through the **git\n"
        "gate** — a separate, host-controlled bare repo that agents push to.\n"
        "Even online-mode agents can be instructed to mirror their work to\n"
        "the gate.\n",
        encoding="utf-8",
    )


def _task_new(project: ProjectConfig, *, name: str | None = None) -> str:
    """Create a new task with a fresh workspace.  Returns the task ID."""
    if name is not None:
        task_name = sanitize_task_name(name)
        if task_name is None:
            raise SystemExit(f"Invalid task name: {name!r}")
        err = validate_task_name(task_name)
        if err:
            raise SystemExit(f"Invalid task name: {err}")
    else:
        task_name = generate_task_name(project.id)
    tasks_root = project.tasks_root
    ensure_dir(tasks_root)
    meta_dir = tasks_meta_dir(project.id)
    ensure_dir(meta_dir)

    existing = sorted([p.stem for p in meta_dir.glob("*.yml") if p.stem.isdigit()], key=int)
    next_id = str(int(existing[-1]) + 1 if existing else 1)

    ws = tasks_root / next_id
    ensure_dir(ws)

    workspace_dir = ws / "workspace-dangerous"
    ensure_dir(workspace_dir)
    workspace_dir.chmod(0o700)
    marker_path = workspace_dir / ".new-task-marker"
    marker_path.write_text(
        "# This marker signals that the workspace should be reset to the latest remote HEAD.\n"
        "# It is created by 'terokctl task new' and removed by init-ssh-and-repo.sh after reset.\n"
        "# If you see this file in an initialized workspace, something went wrong.\n",
        encoding="utf-8",
    )

    _write_task_readme(ws)

    meta = {
        "task_id": next_id,
        "name": task_name,
        "mode": None,
        "workspace": str(ws),
        "web_port": None,
    }
    (meta_dir / f"{next_id}.yml").write_text(yaml.safe_dump(meta))
    print(f"Created task {next_id} ({task_name}) in {ws}")
    return next_id


def task_new(project_id: str, *, name: str | None = None) -> str:
    """Create a new task with a fresh workspace for a project.

    Args:
        project_id: The project to create the task under.
        name: Optional human-readable name.  Allowed characters are
            lowercase letters, digits, hyphens, and underscores.
            If ``None``, a random slug-style name is generated via
            :func:`generate_task_name`.

    Workspace Initialization Protocol:
    ----------------------------------
    Each task gets its own workspace directory that persists across container
    runs. When a container starts, the init script (init-ssh-and-repo.sh) needs
    to know whether this is:

    1. A NEW task that should be reset to the latest remote HEAD
    2. A RESTARTED task where local changes should be preserved

    We use a marker file (.new-task-marker) to signal intent:

    - task_new() creates the marker in the workspace directory
    - init-ssh-and-repo.sh checks for the marker:
      - If marker exists: reset to origin/HEAD, then delete marker
      - If no marker: fetch only, preserve local state
    - Subsequent container runs on the same task won't see the marker,
      so local work is preserved

    This handles edge cases like:
    - Stale workspace from incompletely deleted previous task with same ID
    - Ensuring new tasks always start with latest code
    """
    return _task_new(load_project(project_id), name=name)


def _task_rename(project: ProjectConfig, task_id: str, new_name: str) -> None:
    """Rename a task by updating its metadata YAML."""
    meta_dir = tasks_meta_dir(project.id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        raise SystemExit(f"Unknown task {task_id}")
    meta = yaml.safe_load(meta_path.read_text()) or {}
    sanitized = sanitize_task_name(new_name)
    if sanitized is None:
        raise SystemExit(f"Invalid task name: {new_name!r}")
    err = validate_task_name(sanitized)
    if err:
        raise SystemExit(f"Invalid task name: {err}")
    meta["name"] = sanitized
    meta_path.write_text(yaml.safe_dump(meta))
    print(f"Renamed task {task_id} to {sanitized}")


def task_rename(project_id: str, task_id: str, new_name: str) -> None:
    """Rename a task by updating its metadata YAML.

    Sanitizes *new_name* and writes the result to the task's metadata file.
    Raises ``SystemExit`` if the task is unknown or the sanitized name is invalid.
    """
    _task_rename(load_project(project_id), task_id, new_name)


def _get_tasks(project_id: str, reverse: bool = False) -> list[TaskMeta]:
    """Return all task metadata for *project_id*, sorted by task ID."""
    meta_dir = tasks_meta_dir(project_id)
    tasks: list[TaskMeta] = []
    if not meta_dir.is_dir():
        return tasks
    try:
        project = load_project(project_id)
        tasks_root = project.tasks_root
    except SystemExit:
        tasks_root = None
    for f in meta_dir.glob("*.yml"):
        try:
            meta = yaml.safe_load(f.read_text()) or {}
            tid = str(meta.get("task_id", ""))
            ws_status = None
            ws_message = None
            if tasks_root and tid:
                agent_cfg = tasks_root / tid / "agent-config"
                ws = read_work_status(agent_cfg)
                ws_status = ws.status
                ws_message = ws.message
            tasks.append(
                TaskMeta(
                    task_id=tid,
                    mode=meta.get("mode"),
                    workspace=meta.get("workspace", ""),
                    web_port=meta.get("web_port"),
                    backend=meta.get("backend"),
                    exit_code=meta.get("exit_code"),
                    deleting=bool(meta.get("deleting")),
                    preset=meta.get("preset"),
                    name=meta["name"],
                    provider=meta.get("provider"),
                    unrestricted=meta.get("unrestricted"),
                    work_status=ws_status,
                    work_message=ws_message,
                )
            )
        except Exception:
            continue

    def _sort_key(t: TaskMeta) -> tuple[bool, int, str]:
        """Sort numeric IDs first (ascending), then non-numeric lexically."""
        try:
            return (False, int(t.task_id), t.task_id)
        except (ValueError, TypeError):
            return (True, 0, t.task_id or "")

    tasks.sort(key=_sort_key, reverse=reverse)
    return tasks


def get_tasks(project_id: str, reverse: bool = False) -> list[TaskMeta]:
    """Return all task metadata for *project_id*, sorted by task ID."""
    return _get_tasks(project_id, reverse=reverse)


def get_all_task_states(
    project_id: str,
    tasks: list[TaskMeta],
) -> dict[str, str | None]:
    """Map each task to its live container state via a single batch query.

    Args:
        project_id: The project whose containers to query.
        tasks: List of ``TaskMeta`` instances (must have ``task_id`` and ``mode``).

    Returns:
        ``{task_id: container_state_or_None}`` dict.
    """
    container_states = get_project_container_states(project_id)
    result: dict[str, str | None] = {}
    for t in tasks:
        if t.mode:
            cname = container_name(project_id, t.mode, str(t.task_id))
            result[str(t.task_id)] = container_states.get(cname)
        else:
            result[str(t.task_id)] = None
    return result


def task_list(
    project_id: str,
    *,
    status: str | None = None,
    mode: str | None = None,
    agent: str | None = None,
) -> None:
    """List tasks for a project, optionally filtered by status, mode, or agent preset.

    Status is computed live from podman container state + task metadata.
    """
    tasks = get_tasks(project_id)

    # Pre-filter by mode/agent before the podman query to reduce work
    if mode:
        tasks = [t for t in tasks if t.mode == mode]
    if agent:
        tasks = [t for t in tasks if t.preset == agent]

    if not tasks:
        print("No tasks found")
        return

    # Batch-query podman for all container states in one call
    live_states = get_all_task_states(project_id, tasks)
    for t in tasks:
        t.container_state = live_states.get(t.task_id)

    # Filter by effective status (computed live)
    if status:
        tasks = [t for t in tasks if effective_status(t) == status]

    if not tasks:
        print("No tasks found")
        return

    for t in tasks:
        t_status = effective_status(t)
        extra = []
        if t.mode:
            extra.append(f"mode={t.mode}")
        if t.web_port:
            extra.append(f"port={t.web_port}")
        if t.work_status:
            extra.append(f"work={t.work_status}")
        extra_s = f" [{'; '.join(extra)}]" if extra else ""
        print(f"- {t.task_id:>3}: {t.name} {t_status}{extra_s}")


def _check_mode(meta: dict, expected: str) -> None:
    """Raise SystemExit if the task's mode conflicts with *expected*."""
    mode = meta.get("mode")
    if mode and mode != expected:
        raise SystemExit(f"Task already ran in mode '{mode}', cannot run in '{expected}'")


def load_task_meta(
    project_id: str, task_id: str, expected_mode: str | None = None
) -> tuple[dict, Path]:
    """Load task metadata and optionally validate mode.

    Returns (meta, meta_path). Raises SystemExit if task is unknown or mode
    conflicts with *expected_mode*.
    """
    meta_dir = tasks_meta_dir(project_id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        raise SystemExit(f"Unknown task {task_id}")
    meta = yaml.safe_load(meta_path.read_text()) or {}
    if expected_mode is not None:
        _check_mode(meta, expected_mode)
    return meta, meta_path


def mark_task_deleting(project_id: str, task_id: str) -> None:
    """Persist ``deleting: true`` to the task's YAML metadata file."""
    try:
        meta_dir = tasks_meta_dir(project_id)
        meta_path = meta_dir / f"{task_id}.yml"
        if not meta_path.is_file():
            return
        meta = yaml.safe_load(meta_path.read_text()) or {}
        meta["deleting"] = True
        meta_path.write_text(yaml.safe_dump(meta))
    except Exception as e:
        _log_debug(f"mark_task_deleting: failed project_id={project_id} task_id={task_id}: {e}")


def capture_task_logs(project: ProjectConfig | str, task_id: str, mode: str) -> Path | None:
    """Capture container logs to the task's ``logs/`` directory on the host.

    Writes stdout/stderr from ``podman logs`` to
    ``<tasks_root>/<task_id>/logs/container.log``.  Returns the log file
    path on success, or ``None`` if the container doesn't exist or podman
    fails.

    *project* may be a :class:`ProjectConfig` or a project-ID string
    (the string form loads the config internally for backward compat).
    """
    if isinstance(project, str):
        project = load_project(project)
    task_dir = project.tasks_root / str(task_id)
    logs_dir = task_dir / "logs"
    ensure_dir(logs_dir)
    log_file = logs_dir / "container.log"

    cname = container_name(project.id, mode, task_id)
    try:
        with log_file.open("wb") as f:
            result = subprocess.run(
                ["podman", "logs", "--timestamps", cname],
                stdout=f,
                stderr=subprocess.PIPE,
                timeout=60,
            )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        log_file.unlink(missing_ok=True)
        return None

    if result.returncode != 0:
        log_file.unlink(missing_ok=True)
        return None

    return log_file


def _archive_task(project: ProjectConfig, task_id: str, meta: dict) -> Path | None:
    """Archive task metadata and logs before deletion.

    Creates an archive entry at
    ``<state_root>/projects/<project_id>/archive/<timestamp>_<task_id>_<name>/``
    containing the task metadata YAML and any captured logs.

    The archive directory name uses the archival timestamp as the primary
    identifier because task numbers and names are not globally unique —
    they can be reused when tasks are deleted and recreated.

    Returns the archive directory path, or ``None`` if archiving failed.
    """
    try:
        task_name = meta.get("name", "")
        ts = archive_timestamp()
        # Build archive dir name: timestamp_taskid_name (name may be empty)
        dir_name = f"{ts}_{task_id}"
        if task_name:
            dir_name = f"{dir_name}_{task_name}"

        archive_root = tasks_archive_dir(project.id)
        archive_dir = create_archive_dir(archive_root, dir_name)

        # Save metadata snapshot
        (archive_dir / "task.yml").write_text(yaml.safe_dump(meta))

        # Copy logs if they exist
        task_dir = project.tasks_root / str(task_id)
        logs_dir = task_dir / "logs"
        if logs_dir.is_dir():
            archive_logs_dir = archive_dir / "logs"
            shutil.copytree(logs_dir, archive_logs_dir, dirs_exist_ok=True)

        _log_debug(f"_archive_task: archived task {task_id} to {archive_dir}")
        return archive_dir
    except Exception as e:
        _log_debug(f"_archive_task: failed to archive task {task_id}: {e}")
        return None


def _task_delete(project: ProjectConfig, task_id: str) -> None:
    """Delete a task's workspace, metadata, and associated containers."""
    _log_debug(f"task_delete: start project_id={project.id} task_id={task_id}")

    workspace = project.tasks_root / str(task_id)
    meta_dir = tasks_meta_dir(project.id)
    meta_path = meta_dir / f"{task_id}.yml"
    _log_debug(f"task_delete: workspace={workspace} meta_path={meta_path}")

    meta = {}
    if meta_path.is_file():
        meta = yaml.safe_load(meta_path.read_text()) or {}

    mode = meta.get("mode")
    if mode:
        _log_debug("task_delete: capturing container logs")
        capture_task_logs(project, task_id, mode)

    if meta:
        _log_debug("task_delete: archiving task")
        _archive_task(project, task_id, meta)

    _log_debug("task_delete: revoking gate tokens")
    from ..security.gate_tokens import revoke_token_for_task

    try:
        revoke_token_for_task(project.id, task_id)
    except Exception as exc:
        _log_debug(f"task_delete: token revoke failed: {exc}")

    _log_debug("task_delete: calling _stop_task_containers")
    stop_task_containers(project, str(task_id))
    _log_debug("task_delete: _stop_task_containers returned")

    if workspace.is_dir():
        _log_debug("task_delete: removing workspace directory")
        shutil.rmtree(workspace)
        _log_debug("task_delete: workspace directory removed")

    if meta_path.is_file():
        _log_debug("task_delete: removing metadata file")
        meta_path.unlink()
        _log_debug("task_delete: metadata file removed")

    _log_debug("task_delete: finished")


def task_delete(project_id: str, task_id: str) -> None:
    """Delete a task's workspace, metadata, and any associated containers.

    Before removing the task, captures container logs and archives the task
    metadata and logs to ``<state_root>/projects/<project_id>/archive/``.
    The archive directory is named by archival timestamp + task ID + name
    for unique identification (task numbers and names can be reused).

    This mirrors the behavior used by the TUI when deleting a task, but is
    exposed here so both CLI and TUI share the same logic. Containers are
    stopped best-effort via podman using the naming scheme
    "<project.id>-<mode>-<task_id>".
    """
    _task_delete(load_project(project_id), task_id)


def _validate_login(project: ProjectConfig, task_id: str) -> tuple[str, str]:
    """Validate that a task exists and its container is running.

    Returns ``(container_name, mode)`` on success.
    Raises ``SystemExit`` with actionable messages on failure.
    """
    meta_dir = tasks_meta_dir(project.id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        raise SystemExit(f"Unknown task {task_id}")
    meta = yaml.safe_load(meta_path.read_text()) or {}

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(
            f"Task {task_id} has never been run (no mode set). "
            f"Start it first via 'terokctl task run-cli {project.id} {task_id}' "
            f"or 'terokctl task run-web {project.id} {task_id}'."
        )

    cname = container_name(project.id, mode, task_id)
    state = get_container_state(cname)
    if state is None:
        raise SystemExit(
            f"Container {cname} does not exist. "
            f"Run 'terokctl task restart {project.id} {task_id}' first."
        )
    if state != "running":
        raise SystemExit(
            f"Container {cname} is not running (state: {state}). "
            f"Run 'terokctl task restart {project.id} {task_id}' first."
        )
    return cname, mode


def _get_login_command(project: ProjectConfig, task_id: str) -> list[str]:
    """Return the podman exec command to log into a task container."""
    cname, _mode = _validate_login(project, task_id)
    return [
        "podman",
        "exec",
        "-it",
        cname,
        "tmux",
        "new-session",
        "-A",
        "-s",
        "main",
    ]


def _task_login(project: ProjectConfig, task_id: str) -> None:
    """Open an interactive shell in a running task container."""
    cmd = _get_login_command(project, task_id)
    try:
        os.execvp(cmd[0], cmd)
    except FileNotFoundError:
        raise SystemExit(
            f"'{cmd[0]}' not found on PATH. Please install podman or add it to your PATH."
        )


def _task_stop(project: ProjectConfig, task_id: str, *, timeout: int | None = None) -> None:
    """Gracefully stop a running task container."""
    effective_timeout = timeout if timeout is not None else project.shutdown_timeout
    meta_dir = tasks_meta_dir(project.id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        raise SystemExit(f"Unknown task {task_id}")
    meta = yaml.safe_load(meta_path.read_text()) or {}

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(f"Task {task_id} has never been run (no mode set)")

    cname = container_name(project.id, mode, task_id)

    state = get_container_state(cname)
    if state is None:
        raise SystemExit(f"Task {task_id} container does not exist")
    if state not in ("running", "paused"):
        raise SystemExit(f"Task {task_id} container is not stoppable (state: {state})")

    try:
        subprocess.run(
            ["podman", "stop", "--time", str(effective_timeout), cname],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"Failed to stop container: {e}")

    color_enabled = _supports_color()
    print(f"Stopped task {task_id}: {_green(cname, color_enabled)}")
    print(f"Restart with: terokctl task restart {project.id} {task_id}")


def get_login_command(project_id: str, task_id: str) -> list[str]:
    """Return the podman exec command to log into a task container."""
    return _get_login_command(load_project(project_id), task_id)


def task_login(project_id: str, task_id: str) -> None:
    """Open an interactive shell in a running task container."""
    _task_login(load_project(project_id), task_id)


def task_stop(project_id: str, task_id: str, *, timeout: int | None = None) -> None:
    """Gracefully stop a running task container.

    Uses ``podman stop --time <N>`` to give the container *timeout* seconds
    before SIGKILL.  When *timeout* is ``None`` the project's
    ``run.shutdown_timeout`` setting is used (default 10 s).
    """
    _task_stop(load_project(project_id), task_id, timeout=timeout)


def task_status(project_id: str, task_id: str) -> None:
    """Show live task status with container state diagnostics."""
    project = load_project(project_id)
    meta_dir = tasks_meta_dir(project.id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        raise SystemExit(f"Unknown task {task_id}")
    meta = yaml.safe_load(meta_path.read_text()) or {}

    mode = meta.get("mode")
    web_port = meta.get("web_port")
    exit_code = meta.get("exit_code")

    color_enabled = _supports_color()

    # Query live container state
    cname = None
    cs = None
    if mode:
        cname = container_name(project.id, mode, task_id)
        cs = get_container_state(cname)

    # Build TaskMeta for effective_status / mode_emoji computation
    task = TaskMeta(
        task_id=task_id,
        mode=mode,
        workspace=meta.get("workspace", ""),
        web_port=web_port,
        backend=meta.get("backend"),
        exit_code=exit_code,
        deleting=bool(meta.get("deleting")),
        container_state=cs,
        name=meta["name"],
        provider=meta.get("provider"),
        unrestricted=meta.get("unrestricted"),
    )
    status = effective_status(task)
    info = STATUS_DISPLAY.get(status, STATUS_DISPLAY["created"])

    status_color = {"green": _green, "yellow": _yellow, "red": _red}.get(info.color, _yellow)
    m = mode_info(task)
    m_emoji = render_emoji(m)

    print(f"Task {task_id}:")
    print(f"  Name:            {task.name}")
    print(f"  Status:          {render_emoji(info)} {status_color(info.label, color_enabled)}")
    print(f"  Mode:            {m_emoji} {m.label or 'not set'}")
    if cname:
        print(f"  Container:       {cname}")
    if cs:
        state_color = _green if cs == "running" else _yellow
        print(f"  Container state: {state_color(cs, color_enabled)}")
    elif mode:
        print(f"  Container state: {_red('not found', color_enabled)}")
    if task.unrestricted is not None:
        perm_label = "unrestricted" if task.unrestricted else "restricted"
        print(f"  Permissions:     {perm_label}")
    if exit_code is not None:
        print(f"  Exit code:       {exit_code}")
    if web_port:
        print(f"  Web port:        {web_port}")
    # Work status from agent
    tasks_root = project.tasks_root
    agent_cfg = tasks_root / task_id / "agent-config"
    ws = read_work_status(agent_cfg)
    if ws.status:
        print(f"  Work status:     {ws.status}")
        if ws.message:
            print(f"  Work message:    {ws.message}")


# ---------- Archive operations ----------


@dataclass
class ArchivedTask:
    """Metadata snapshot of an archived (deleted) task."""

    archive_dir: Path
    archived_at: str
    task_id: str
    name: str
    mode: str | None
    exit_code: int | None


def list_archived_tasks(project_id: str) -> list[ArchivedTask]:
    """Return archived tasks for *project_id*, sorted newest-first."""
    archive_root = tasks_archive_dir(project_id)
    if not archive_root.is_dir():
        return []
    results: list[ArchivedTask] = []
    for entry in sorted(archive_root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        meta_path = entry / "task.yml"
        if not meta_path.is_file():
            continue
        try:
            meta = yaml.safe_load(meta_path.read_text()) or {}
        except Exception:
            continue
        # Parse archive timestamp from directory name: <timestamp>_<task_id>[_<name>]
        parts = entry.name.split("_", 2)
        archived_at = parts[0] if parts else entry.name
        results.append(
            ArchivedTask(
                archive_dir=entry,
                archived_at=archived_at,
                task_id=str(meta.get("task_id", "")),
                name=meta.get("name", ""),
                mode=meta.get("mode"),
                exit_code=meta.get("exit_code"),
            )
        )
    return results


def task_archive_list(project_id: str) -> None:
    """Print archived tasks for *project_id*."""
    archived = list_archived_tasks(project_id)
    if not archived:
        print("No archived tasks found")
        return
    for a in archived:
        extra = []
        if a.mode:
            extra.append(f"mode={a.mode}")
        if a.exit_code is not None:
            extra.append(f"exit={a.exit_code}")
        extra_s = f" [{'; '.join(extra)}]" if extra else ""
        print(f"- {a.archived_at} #{a.task_id}: {a.name}{extra_s}")


def task_archive_logs(project_id: str, archive_id: str) -> Path | None:
    """Return the log file path for an archived task identified by *archive_id*.

    *archive_id* is matched against archive directory names (prefix match).
    Returns the log file path if found, or ``None``.
    """
    archive_root = tasks_archive_dir(project_id)
    if not archive_root.is_dir():
        return None
    for entry in sorted(archive_root.iterdir(), reverse=True):
        if entry.is_dir() and entry.name.startswith(archive_id):
            log_file = entry / "logs" / "container.log"
            if log_file.is_file():
                return log_file
    return None
