# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Web port allocation for task containers.

All port allocation flows through the shared port registry in
:mod:`terok_sandbox.port_registry` to prevent collisions between
users on shared hosts.
"""

from terok_sandbox import claim_port, release_port


def assign_web_port(project_id: str, task_id: str, preferred: int | None = None) -> int:
    """Claim a web port for a task via the shared registry.

    When *preferred* is set (e.g. from persisted task metadata), tries
    that port first before scanning.
    """
    return claim_port(f"web:{project_id}/{task_id}", preferred=preferred)


def release_web_port(project_id: str, task_id: str) -> None:
    """Release a previously claimed web port for a task."""
    release_port(f"web:{project_id}/{task_id}")
