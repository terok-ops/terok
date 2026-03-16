# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task display types and status computation.

Provides display-oriented dataclasses (``StatusInfo``, ``ModeInfo``),
status/mode lookup tables, and functions for computing the effective
status and mode emoji of a task.

Split from ``tasks.py`` to decouple presentation data from task
lifecycle and metadata I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from .tasks import TaskMeta


@dataclass(frozen=True)
class StatusInfo:
    """Display attributes for a task effective status."""

    label: str
    emoji: str
    color: str


@dataclass(frozen=True)
class ModeInfo:
    """Display attributes for a task mode."""

    emoji: str
    label: str


STATUS_DISPLAY: dict[str, StatusInfo] = {
    "running": StatusInfo(label="running", emoji="\U0001f7e2", color="green"),
    "init": StatusInfo(label="init", emoji="\U0001f7e1", color="yellow"),
    "stopped": StatusInfo(label="stopped", emoji="\U0001f534", color="red"),
    "completed": StatusInfo(label="completed", emoji="\u2705", color="green"),
    "failed": StatusInfo(label="failed", emoji="\u274c", color="red"),
    "created": StatusInfo(label="created", emoji="\U0001f195", color="yellow"),
    "not found": StatusInfo(label="not found", emoji="\u2753", color="yellow"),
    "deleting": StatusInfo(label="deleting", emoji="\U0001f9f9", color="yellow"),
}

MODE_DISPLAY: dict[str | None, ModeInfo] = {
    "cli": ModeInfo(emoji="\U0001f4bb", label="CLI"),
    "run": ModeInfo(emoji="\U0001f680", label="Autopilot"),
    "toad": ModeInfo(emoji="\U0001f438", label="Toad"),
    None: ModeInfo(emoji="\U0001f997", label=""),
}


@dataclass(frozen=True)
class ProjectBadge:
    """Display attributes for a project-level badge (security class, GPU, etc.)."""

    emoji: str
    label: str


SECURITY_CLASS_DISPLAY: dict[str, ProjectBadge] = {
    "gatekeeping": ProjectBadge(emoji="\U0001f6aa", label="gate"),
    "online": ProjectBadge(emoji="\U0001f310", label="online"),
}

GPU_DISPLAY: dict[bool, ProjectBadge] = {
    True: ProjectBadge(emoji="\U0001f3ae", label="GPU"),
    False: ProjectBadge(emoji="\U0001f4bf", label="CPU"),
}


def has_gpu(project: Any) -> bool:
    """Check whether a project has GPU enabled in its ``project.yml``.

    Accepts any object with a ``root`` attribute pointing to the project
    directory (typically a ``Project`` instance).  Returns ``False`` on
    any I/O or parse error.
    """
    root = getattr(project, "root", None)
    if root is None:
        return False
    try:
        cfg = yaml.safe_load((root / "project.yml").read_text()) or {}
    except (OSError, TypeError, AttributeError, yaml.YAMLError):
        return False
    gpus = (cfg.get("run") or {}).get("gpus")
    if isinstance(gpus, str):
        return gpus.lower() == "all"
    if isinstance(gpus, bool):
        return gpus
    return False


def _exit_code_status(exit_code: int | None) -> str | None:
    """Map an exit code to a terminal status, or ``None`` if not terminal."""
    if exit_code is None:
        return None
    return "completed" if exit_code == 0 else "failed"


def effective_status(task: TaskMeta) -> str:
    """Compute the display status from task metadata + live container state.

    Reads the following fields from a ``TaskMeta`` instance:

    - ``container_state`` (str | None): live podman state, or None
    - ``mode`` (str | None): task mode (cli/web/run/None)
    - ``exit_code`` (int | None): process exit code, or None
    - ``deleting`` (bool): persisted to YAML before deletion starts

    Returns one of: ``"deleting"``, ``"running"``, ``"init"``,
    ``"stopped"``, ``"completed"``, ``"failed"``, ``"created"``,
    ``"not found"``.
    """
    if task.deleting:
        return "deleting"

    cs = task.container_state

    if cs == "running":
        # Green only once the runner finished init (mode written to YAML).
        # While starting up, show yellow for monotonic new → init → running.
        return "running" if task.mode is not None else "init"

    if cs is not None:
        return _exit_code_status(task.exit_code) or "stopped"

    # No container found
    if task.mode is None:
        return "created"
    return _exit_code_status(task.exit_code) or "not found"


def mode_info(task: TaskMeta) -> ModeInfo:
    """Return the display info for a task's mode."""
    mode = task.mode
    info = MODE_DISPLAY.get(mode if isinstance(mode, str) else None)
    return info if info else MODE_DISPLAY[None]
