# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Render task lifecycle, mode, and project badges as labels and emoji.

Houses the dataclasses and lookup tables that turn raw container state
into display strings, plus the small bit of logic that computes the
"effective" status from a task's lifecycle fields.

Split from ``tasks.py`` so presentation data does not pull in task
metadata I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..util.yaml import YAMLError, load as _yaml_load

# ── Display value objects ──────────────────────────────────────────────


@dataclass
class TaskState:
    """Container lifecycle state used for display status computation.

    Orchestration-level ``TaskMeta`` inherits from this to add identity,
    configuration, and runtime metadata fields.
    """

    container_state: str | None = None
    exit_code: int | None = None
    deleting: bool = False
    initialized: bool = False
    # UI-only flag the TUI flips while a launch worker is in flight but
    # podman has not yet created the container.  Bridges the gap between
    # "task created" and "container running (init)" so users see ⏳
    # instead of an ambiguous 🆕.
    starting: bool = False


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


@dataclass(frozen=True)
class ProjectBadge:
    """Display attributes for a project-level badge (security class, GPU, etc.)."""

    emoji: str
    label: str


# ── Lookup tables ──────────────────────────────────────────────────────


STATUS_DISPLAY: dict[str, StatusInfo] = {
    "running": StatusInfo(label="running", emoji="\U0001f7e2", color="green"),
    "init": StatusInfo(label="init", emoji="\U0001f7e1", color="yellow"),
    "starting": StatusInfo(label="starting", emoji="\u23f3", color="yellow"),
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

SECURITY_CLASS_DISPLAY: dict[str, ProjectBadge] = {
    "gatekeeping": ProjectBadge(emoji="\U0001f6aa", label="gate"),
    "online": ProjectBadge(emoji="\U0001f310", label="online"),
}

ISOLATION_DISPLAY: dict[str, ProjectBadge] = {
    "shared": ProjectBadge(emoji="\U0001f4c2", label="shared"),
    "sealed": ProjectBadge(emoji="\U0001f512", label="sealed"),
}

GPU_DISPLAY: dict[bool, ProjectBadge] = {
    True: ProjectBadge(emoji="\U0001f3ae", label="GPU"),
    False: ProjectBadge(emoji="\U0001f4bf", label="CPU"),
}


# ── Effective status ───────────────────────────────────────────────────


def effective_status(task: TaskState) -> str:
    """Compute the display status from task lifecycle state.

    Reads the following fields from a ``TaskState`` instance:

    - ``container_state`` (str | None): live podman state, or None
    - ``exit_code`` (int | None): process exit code, or None
    - ``deleting`` (bool): persisted to YAML before deletion starts
    - ``initialized`` (bool): True once ``ready_at`` is persisted to YAML

    Returns one of: ``"deleting"``, ``"running"``, ``"init"``,
    ``"starting"``, ``"stopped"``, ``"completed"``, ``"failed"``,
    ``"created"``, ``"not found"``.
    """
    if task.deleting:
        return "deleting"

    cs = task.container_state

    if cs == "running":
        return "running" if task.initialized else "init"

    if cs is not None:
        return _exit_code_status(task.exit_code) or "stopped"

    # No container yet — ``starting`` fills the launch-worker gap
    # before podman has created the container.  Once it's up, the
    # ``cs == "running"`` branch above takes over with ``init``.
    if task.starting:
        return "starting"
    if not task.initialized:
        return "created"
    return _exit_code_status(task.exit_code) or "not found"


def _exit_code_status(exit_code: int | None) -> str | None:
    """Map an exit code to a terminal status, or ``None`` if not terminal."""
    if exit_code is None:
        return None
    return "completed" if exit_code == 0 else "failed"


def mode_info(mode: str | None) -> ModeInfo:
    """Return the display info for a task mode string."""
    info = MODE_DISPLAY.get(mode if isinstance(mode, str) else None)
    return info if info else MODE_DISPLAY[None]


# ── Project queries ────────────────────────────────────────────────────


def has_gpu(project: Any) -> bool:
    """True when the project's ``project.yml`` opts into GPU passthrough.

    Accepts any object with a ``root`` attribute pointing to the project
    directory (typically a ``Project`` instance).  Returns ``False`` on
    any I/O or parse error.
    """
    root = getattr(project, "root", None)
    if root is None:
        return False
    try:
        cfg = _yaml_load((root / "project.yml").read_text()) or {}
    except (OSError, TypeError, AttributeError, YAMLError):
        return False
    gpus = (cfg.get("run") or {}).get("gpus")
    if isinstance(gpus, str):
        return gpus.lower() == "all"
    if isinstance(gpus, bool):
        return gpus
    return False


# ── Container naming ───────────────────────────────────────────────────

CONTAINER_MODES = ("cli", "web", "run", "toad")
"""All valid container mode suffixes used in container naming."""


def container_name(project_id: str, mode: str, task_id: str) -> str:
    """Return the canonical container name for a task."""
    return f"{project_id}-{mode}-{task_id}"
