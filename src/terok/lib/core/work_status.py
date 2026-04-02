# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Agent work-status reporting: read/write ``work-status.yml`` from agent-config dirs.

Agents report their current work phase by writing a small YAML file inside the
container at ``/home/dev/.terok/work-status.yml``.  On the host this maps to
``<tasks_root>/<tid>/agent-config/work-status.yml``.

The file can be a dict (``status: coding``, ``message: ...``) or a bare string
(``coding``).  Unknown status values are preserved so callers can decide how to
handle them.

This module also handles **pending-phase** files (``pending-phase.yml``), used
by external tools (e.g. kanban-tui) to queue deferred phase transitions on
running tasks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

from ..util.yaml import YAMLError, dump as _yaml_dump, load as _yaml_load


def _write_yaml_atomic(path: Path, data: dict[str, str]) -> None:
    """Write *data* as YAML to *path* atomically via temp-file + replace."""
    payload = _yaml_dump(data)
    with NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
    ) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


STATUS_FILE_NAME = "work-status.yml"
"""Filename agents write inside their agent-config directory."""

PENDING_PHASE_FILE = "pending-phase.yml"
"""Filename for deferred phase transitions on running tasks."""

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
        raw = _yaml_load(status_path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        from ..util.logging_utils import _log_debug

        _log_debug(f"Cannot read work status {status_path}: {exc}")
        return WorkStatus()
    if raw is None:
        return WorkStatus()
    if isinstance(raw, str):
        return WorkStatus(status=raw)
    if isinstance(raw, dict):
        status = raw.get("status")
        message = raw.get("message")
        return WorkStatus(
            status=status if isinstance(status, str) else None,
            message=message if isinstance(message, str) else None,
        )
    return WorkStatus()


def write_work_status(
    agent_config_dir: Path, status: str | None, message: str | None = None
) -> None:
    """Write ``work-status.yml`` into *agent_config_dir*.

    When *status* is ``None`` the file is removed (clearing the status).
    """
    path = agent_config_dir / STATUS_FILE_NAME
    if status is None:
        path.unlink(missing_ok=True)
        return
    if not isinstance(status, str) or not status:
        raise ValueError("status must be a non-empty string")
    if message is not None and not isinstance(message, str):
        raise ValueError("message must be a string or None")
    agent_config_dir.mkdir(parents=True, exist_ok=True)
    data: dict[str, str] = {"status": status}
    if message is not None:
        data["message"] = message
    _write_yaml_atomic(path, data)


# ---------- Pending phase I/O ----------


@dataclass(frozen=True)
class PendingPhase:
    """A queued phase transition for a running task."""

    phase: str
    prompt: str


def read_pending_phase(agent_config_dir: Path) -> PendingPhase | None:
    """Read ``pending-phase.yml`` from *agent_config_dir*.

    Returns ``None`` if the file is missing, empty, or malformed.
    """
    phase_path = agent_config_dir / PENDING_PHASE_FILE
    if not phase_path.is_file():
        return None
    try:
        raw = _yaml_load(phase_path.read_text(encoding="utf-8"))
    except (YAMLError, OSError, UnicodeDecodeError) as exc:
        from ..util.logging_utils import _log_debug

        _log_debug(f"Cannot read pending phase {phase_path}: {exc}")
        return None
    if not isinstance(raw, dict):
        return None
    phase = raw.get("phase")
    prompt = raw.get("prompt", "")
    if not isinstance(phase, str) or not phase:
        return None
    if not isinstance(prompt, str):
        return None
    return PendingPhase(phase=phase, prompt=prompt)


def write_pending_phase(agent_config_dir: Path, phase: str, prompt: str) -> None:
    """Write ``pending-phase.yml`` into *agent_config_dir*."""
    if not isinstance(phase, str) or not phase:
        raise ValueError("phase must be a non-empty string")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    agent_config_dir.mkdir(parents=True, exist_ok=True)
    phase_path = agent_config_dir / PENDING_PHASE_FILE
    data = {"phase": phase, "prompt": prompt}
    _write_yaml_atomic(phase_path, data)


def clear_pending_phase(agent_config_dir: Path) -> None:
    """Delete ``pending-phase.yml`` from *agent_config_dir* if it exists."""
    phase_path = agent_config_dir / PENDING_PHASE_FILE
    phase_path.unlink(missing_ok=True)
