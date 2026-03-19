# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container-based command execution for agent workspaces.

Runs git (and other commands) **inside** task containers via ``podman exec``
instead of on the host, eliminating the risk of poisoned git hooks or
scripts executing with host privileges.
"""

import subprocess

from ..util.logging_utils import _log_debug
from .runtime import container_name, get_container_state


def _podman_start(cname: str) -> bool:
    """Start a stopped container, returning ``True`` on success."""
    try:
        subprocess.run(
            ["podman", "start", cname],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        _log_debug(f"container_exec._podman_start({cname}): {exc}")
        return False
    return True


def _podman_stop(cname: str, timeout: int = 10) -> None:
    """Stop a container best-effort."""
    try:
        subprocess.run(
            ["podman", "stop", "--time", str(timeout), cname],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass


def container_git_diff(
    project_id: str,
    task_id: str,
    mode: str,
    *args: str,
    timeout: int = 30,
) -> str | None:
    """Run ``git diff`` inside a task container and return stdout.

    Args:
        project_id: Project identifier.
        task_id: Task identifier.
        mode: Container mode (``"cli"``, ``"web"``, ``"run"``, ``"toad"``).
        *args: Additional arguments passed to ``git diff`` (e.g.
            ``"--stat"``, ``"HEAD@{1}..HEAD"``).
        timeout: Subprocess timeout in seconds.

    Returns:
        The diff output on success, or ``None`` on any failure.

    If the container is stopped/exited it is temporarily restarted for the
    exec, then stopped again.  All git commands target the container-internal
    ``/workspace`` path — the host ``workspace-dangerous`` path is never
    passed to any subprocess.
    """
    cname = container_name(project_id, mode, task_id)
    state = get_container_state(cname)

    if state is None:
        _log_debug(f"container_git_diff: container {cname} not found")
        return None

    restarted = False
    if state != "running":
        if mode == "run":
            # Never restart exited headless containers — podman start replays
            # the original entrypoint (the agent command), causing duplicate
            # commits, network calls, and other side effects.
            _log_debug(f"container_git_diff: refusing to restart exited headless container {cname}")
            return None
        if not _podman_start(cname):
            return None
        restarted = True

    try:
        cmd = ["podman", "exec", cname, "git", "-C", "/workspace", "diff", *args]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            _log_debug(f"container_git_diff: git diff failed rc={result.returncode}")
            return None
        return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        _log_debug(f"container_git_diff: {exc}")
        return None
    finally:
        if restarted:
            _podman_stop(cname)
