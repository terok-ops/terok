# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Autopilot container lifecycle helpers.

Encapsulates the task-level exit-watching and log-follow operations behind
the executor API so the TUI (presentation layer) does not reach for raw
subprocess calls.
"""

from terok_executor import AgentRunner

from .tasks import update_task_exit_code


def wait_for_container_exit(
    container_name: str,
    project_id: str,
    task_id: str,
    timeout: int = 7200,
) -> tuple[int | None, str | None]:
    """Wait for *container_name* to exit and record its code in task metadata.

    Returns ``(exit_code, error_message)``.  On a successful wait
    *error_message* is ``None`` and the real exit code is persisted
    — including a legitimate exit code of 124, which is no longer
    conflated with the watcher's own timeout.  On timeout *exit_code*
    is ``None`` and the error message describes it.
    """
    try:
        exit_code = AgentRunner().wait_for_exit(container_name, timeout=float(timeout))
    except TimeoutError:
        return None, "Watcher timed out"
    except Exception as e:
        return None, str(e)

    update_task_exit_code(project_id, task_id, exit_code)
    return exit_code, None
