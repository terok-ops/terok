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
    "stopped": StatusInfo(label="stopped", emoji="\U0001f7e1", color="yellow"),
    "completed": StatusInfo(label="completed", emoji="\u2705", color="green"),
    "failed": StatusInfo(label="failed", emoji="\u274c", color="red"),
    "created": StatusInfo(label="created", emoji="\U0001f195", color="yellow"),
    "not found": StatusInfo(label="not found", emoji="\u2753", color="yellow"),
    "deleting": StatusInfo(label="deleting", emoji="\U0001f9f9", color="yellow"),
}

MODE_DISPLAY: dict[str | None, ModeInfo] = {
    "cli": ModeInfo(emoji="\U0001f4bb", label="CLI"),
    "web": ModeInfo(emoji="\U0001f30d", label="Web"),
    "run": ModeInfo(emoji="\U0001f680", label="Autopilot"),
    None: ModeInfo(emoji="\U0001f997", label=""),
}

WEB_BACKEND_DISPLAY: dict[str, ModeInfo] = {
    "claude": ModeInfo(emoji="\U0001f4a0", label="Claude"),
    "codex": ModeInfo(emoji="\U0001f338", label="Codex"),
    "mistral": ModeInfo(emoji="\U0001f3f0", label="Mistral"),
    "copilot": ModeInfo(emoji="\U0001f916", label="Copilot"),
}

WEB_BACKEND_DEFAULT = MODE_DISPLAY["web"]


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


def effective_status(task: TaskMeta) -> str:
    """Compute the display status from task metadata + live container state.

    Reads the following fields from a ``TaskMeta`` instance:

    - ``container_state`` (str | None): live podman state, or None
    - ``mode`` (str | None): task mode (cli/web/run/None)
    - ``exit_code`` (int | None): process exit code, or None
    - ``deleting`` (bool): persisted to YAML before deletion starts

    Returns one of: ``"deleting"``, ``"running"``, ``"stopped"``,
    ``"completed"``, ``"failed"``, ``"created"``, ``"not found"``.
    """
    if task.deleting:
        return "deleting"

    cs = task.container_state
    mode = task.mode
    exit_code = task.exit_code

    if cs == "running":
        return "running"

    if cs is not None:
        # Container exists but is not running
        if exit_code is not None and exit_code == 0:
            return "completed"
        if exit_code is not None and exit_code != 0:
            return "failed"
        return "stopped"

    # No container found
    if mode is None:
        return "created"
    if exit_code is not None and exit_code == 0:
        return "completed"
    if exit_code is not None and exit_code != 0:
        return "failed"
    return "not found"


def mode_info(task: TaskMeta) -> ModeInfo:
    """Return the display info for a task's mode, resolving web backends.

    For ``mode="web"``, the info is looked up from ``WEB_BACKEND_DISPLAY``
    using the task's ``backend`` field.  Other modes use ``MODE_DISPLAY``.
    """
    mode = task.mode
    if mode == "web":
        backend = task.backend
        if isinstance(backend, str):
            return WEB_BACKEND_DISPLAY.get(backend, WEB_BACKEND_DEFAULT)
        return WEB_BACKEND_DEFAULT
    info = MODE_DISPLAY.get(mode if isinstance(mode, str) else None)
    return info if info else MODE_DISPLAY[None]
