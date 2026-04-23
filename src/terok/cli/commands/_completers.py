# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared argcomplete completers and helpers for CLI commands.

All completers assume the standard ``project_id`` / ``task_id`` dest
names.  Parsers whose positionals display as ``<project>`` / ``<task>``
(e.g. ``sickbay``) should set ``dest="project_id"`` / ``dest="task_id"``
with a custom ``metavar=`` for display, so completers and argparse help
stay decoupled.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any

from ...lib.core.projects import list_presets, list_projects
from ...lib.domain.facade import get_tasks
from ...lib.orchestration.tasks import normalize_task_id_input


def complete_project_ids(
    prefix: str, parsed_args: argparse.Namespace, **kwargs: object
) -> list[str]:  # pragma: no cover
    """Return project IDs matching *prefix* for argcomplete."""
    try:
        ids = [p.id for p in list_projects()]
    except Exception:
        return []
    if prefix:
        ids = [i for i in ids if str(i).startswith(prefix)]
    return ids


def complete_task_ids(
    prefix: str, parsed_args: argparse.Namespace, **kwargs: object
) -> list[str]:  # pragma: no cover
    """Return task IDs matching *prefix* within ``parsed_args.project_id``.

    Returns an empty list when the project arg hasn't been typed yet —
    argcomplete uses the partially-parsed namespace, which is exactly
    what we want to scope task-ID suggestions.

    The prefix is run through :func:`normalize_task_id_input`, so
    ``K3V<TAB>`` or ``k3-v<TAB>`` rewrite to the canonical lowercase
    form — the same surface-form tolerance ``resolve_task_id`` gives
    at dispatch time.
    """
    project_id = getattr(parsed_args, "project_id", None)
    if not project_id:
        return []
    try:
        tids = [t.task_id for t in get_tasks(project_id) if t.task_id]
    except Exception:
        return []
    normalized = normalize_task_id_input(prefix)
    if normalized:
        tids = [t for t in tids if t.startswith(normalized)]
    return tids


def complete_preset_names(
    prefix: str, parsed_args: argparse.Namespace, **kwargs: object
) -> list[str]:  # pragma: no cover
    """Return preset names matching *prefix* for the scoped project.

    ``list_presets`` requires a project ID to resolve the full tier
    (bundled → global → project), so we only suggest presets once the
    user has typed the project arg.  No project typed yet → empty list,
    which leaves argcomplete silent rather than misleading.
    """
    project_id = getattr(parsed_args, "project_id", None)
    if not project_id:
        return []
    try:
        names = [p.name for p in list_presets(project_id)]
    except Exception:
        return []
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return names


def set_completer(action: argparse.Action, fn: Callable[..., Any]) -> None:
    """Attach an argcomplete completer to *action*, ignoring missing argcomplete."""
    action.completer = fn  # type: ignore[attr-defined]


def add_project_id(parser: argparse.ArgumentParser, **kwargs: object) -> argparse.Action:
    """Add a ``project_id`` positional with the project-ID completer attached.

    Returns the argparse action so callers can further customise it.
    Accepts any argparse kwargs (``nargs``, ``metavar``, ``help``, etc.).
    """
    action = parser.add_argument("project_id", **kwargs)
    set_completer(action, complete_project_ids)
    return action


def add_task_id(parser: argparse.ArgumentParser, **kwargs: object) -> argparse.Action:
    """Add a ``task_id`` positional with the task-ID completer attached.

    Returns the argparse action.  Callers should typically precede this
    with :func:`add_project_id` so argcomplete has a project scope to
    look up tasks under.
    """
    action = parser.add_argument("task_id", **kwargs)
    set_completer(action, complete_task_ids)
    return action
