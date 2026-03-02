# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Agent work-status reporting: read ``work-status.yml`` from agent-config dirs.

Agents report their current work phase by writing a small YAML file inside the
container at ``/home/dev/.luskctl/work-status.yml``.  On the host this maps to
``<tasks_root>/<tid>/agent-config/work-status.yml``.

The file can be a dict (``status: coding``, ``message: ...``) or a bare string
(``coding``).  Unknown status values are preserved so callers can decide how to
handle them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

STATUS_FILE_NAME = "work-status.yml"
"""Filename agents write inside their agent-config directory."""

WORK_STATUSES: dict[str, str] = {
    "planning": "Planning approach",
    "coding": "Writing code",
    "testing": "Running tests",
    "debugging": "Investigating issues",
    "reviewing": "Reviewing changes",
    "documenting": "Writing documentation",
    "done": "Work completed",
    "blocked": "Waiting for input",
    "error": "Unrecoverable error",
}
"""Canonical work-status vocabulary with human-readable labels."""


@dataclass(frozen=True)
class WorkStatusInfo:
    """Display metadata for a work status value."""

    label: str
    emoji: str


WORK_STATUS_DISPLAY: dict[str, WorkStatusInfo] = {
    "planning": WorkStatusInfo("Planning", "📋"),
    "coding": WorkStatusInfo("Coding", "💻"),
    "testing": WorkStatusInfo("Testing", "🧪"),
    "debugging": WorkStatusInfo("Debugging", "🐛"),
    "reviewing": WorkStatusInfo("Reviewing", "🔍"),
    "documenting": WorkStatusInfo("Documenting", "📝"),
    "done": WorkStatusInfo("Done", "✅"),
    "blocked": WorkStatusInfo("Blocked", "🚧"),
    "error": WorkStatusInfo("Error", "🚫"),
}
"""Emoji and label for each work status, used by TUI and kanban-tui."""


@dataclass(frozen=True)
class WorkStatus:
    """Parsed work status from an agent's status file."""

    status: str | None = None
    message: str | None = None


def read_work_status(agent_config_dir: Path) -> WorkStatus:
    """Read ``work-status.yml`` from *agent_config_dir*.

    Returns an empty ``WorkStatus`` if the file is missing, empty, or
    malformed.  A bare string (e.g. ``coding``) is accepted as a
    status-only value.
    """
    status_path = agent_config_dir / STATUS_FILE_NAME
    if not status_path.is_file():
        return WorkStatus()
    try:
        raw = yaml.safe_load(status_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return WorkStatus()
    if raw is None:
        return WorkStatus()
    if isinstance(raw, str):
        return WorkStatus(status=raw)
    if isinstance(raw, dict):
        return WorkStatus(
            status=raw.get("status"),
            message=raw.get("message"),
        )
    return WorkStatus()
