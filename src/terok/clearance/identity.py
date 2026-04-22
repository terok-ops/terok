# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Turn a podman container ID into a task-aware :class:`ContainerIdentity`.

Composes :class:`terok_sandbox.PodmanInspector` (container name + OCI
annotations) with terok's task-metadata store so clearance clients
render "Task: project/task_id · name" bodies instead of raw short IDs.
Sandbox stays annotation-key-agnostic on purpose; the terok-specific
keys (``ai.terok.project`` / ``ai.terok.task``) are owned here and
written at ``podman run`` time in ``task_runners``.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from terok_dbus import ContainerIdentity
from terok_sandbox import PodmanInspector

from terok.lib.orchestration.tasks import load_task_meta

_log = logging.getLogger(__name__)

#: OCI annotations written on ``podman run`` for every task container.
#: Mirrored in :func:`terok.lib.orchestration.task_runners._run_container` —
#: changing either end needs a matching edit on the other.
ANNOTATION_PROJECT = "ai.terok.project"
ANNOTATION_TASK = "ai.terok.task"


class IdentityResolver:
    """Compose podman inspect + task metadata into :class:`ContainerIdentity`.

    Callable: ``resolver(container_id) -> ContainerIdentity``.  The
    stable facts (container name, terok annotations) come from
    ``podman inspect``; the one mutable piece — ``task_name`` — reads
    live from ``tasks/meta/<id>.yml`` so a popup fired after an
    operator rename shows the new label.

    Three soft-fail paths, all returning a degraded identity that
    keeps the notification pipeline usable:

    * ``podman inspect`` failed → empty :class:`ContainerIdentity`;
      the subscriber falls back to the raw container ID.
    * Container carries no terok annotations (a standalone container
      that happened to hit the firewall) → container-name-only.
    * ``load_task_meta`` failed → name + project + task_id without
      the ``task_name`` suffix.
    """

    def __init__(self, inspector: PodmanInspector | None = None) -> None:
        """Configure the resolver with an inspector (default: a fresh one)."""
        self._inspector = inspector or PodmanInspector()

    def __call__(self, container_id: str) -> ContainerIdentity:
        """Return the task-aware identity for *container_id*."""
        try:
            info = self._inspector(container_id)
        except Exception:
            # ``PodmanInspector`` normally soft-fails by returning an
            # empty ``ContainerInfo``, but a podman-side race or an
            # unexpected error path can still raise.  Clamp it here so
            # the caller (notifier / TUI) never takes a crash from
            # identity resolution.
            _log.debug("PodmanInspector raised for %s", container_id, exc_info=True)
            return ContainerIdentity()
        if not info.container_id:
            return ContainerIdentity()
        project = info.annotations.get(ANNOTATION_PROJECT, "")
        task_id = info.annotations.get(ANNOTATION_TASK, "")
        base = ContainerIdentity(container_name=info.name, project=project, task_id=task_id)
        if not (project and task_id):
            return base
        return replace(base, task_name=_task_name_for(project, task_id))


def _task_name_for(project: str, task_id: str) -> str:
    """Return the human-readable task name, or ``""`` on any lookup failure."""
    try:
        meta, _ = load_task_meta(project, task_id)
    except SystemExit:
        # load_task_meta raises SystemExit for unknown tasks — harmless
        # here (the task might have been deleted since the block fired).
        return ""
    except Exception:
        _log.debug("load_task_meta failed for %s/%s", project, task_id, exc_info=True)
        return ""
    name = meta.get("name", "")
    return name if isinstance(name, str) else ""
