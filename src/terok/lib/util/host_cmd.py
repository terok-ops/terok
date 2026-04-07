# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Subprocess safety guards for workspace-dangerous directories.

Prevents accidental host-side command execution in bind-mounted workspace
directories that agents have full write access to.  A compromised
``.git/hooks/`` could execute arbitrary code on the host — all legitimate
git operations should go through ``podman exec`` instead.
"""

from pathlib import Path

WORKSPACE_DANGEROUS_DIRNAME = "workspace-dangerous"
"""Sentinel directory name for agent-writable workspaces that must never be
targeted by host-side subprocess calls."""

SHARED_DIRNAME = "_shared"
"""Default directory name for per-project shared task IPC under ``tasks_root``."""


def is_in_dangerous_workspace(path: str | Path) -> bool:
    """Return ``True`` if *path* contains the ``workspace-dangerous`` sentinel.

    Checks whether any component of the resolved path includes the
    dangerous workspace directory name, catching both direct references
    and subdirectory paths.
    """
    return WORKSPACE_DANGEROUS_DIRNAME in Path(path).resolve().parts


def assert_not_in_dangerous_workspace(
    cmd: list[str],
    cwd: str | Path | None = None,
) -> None:
    """Raise ``RuntimeError`` if *cmd* or *cwd* targets a dangerous workspace.

    Inspects:
    - The *cwd* working directory (if provided)
    - All positional arguments in *cmd*
    - Any path following a ``-C`` flag (git's directory override)

    This is a defence-in-depth guard — the primary protection is the AST-based
    CI test that forbids co-occurrence of subprocess calls and
    ``workspace-dangerous`` references entirely.
    """
    # Check cwd
    if cwd is not None and is_in_dangerous_workspace(cwd):
        raise RuntimeError(f"Refusing to execute command in dangerous workspace: cwd={cwd}")

    # Check arguments, including -C targets
    check_next = False
    for arg in cmd:
        if check_next:
            if is_in_dangerous_workspace(arg):
                raise RuntimeError(
                    f"Refusing to execute command targeting dangerous workspace: -C {arg}"
                )
            check_next = False
            continue
        if arg == "-C":
            check_next = True
            continue
        if is_in_dangerous_workspace(arg):
            raise RuntimeError(
                f"Refusing to execute command referencing dangerous workspace: {arg}"
            )
