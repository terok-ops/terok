# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Reusable Textual widgets for the terok TUI.

Re-exports widget classes and render helpers from focused submodules.
"""

from ...lib.containers.tasks import TaskMeta  # noqa: F401 — re-exported public API
from .project_list import ProjectActions, ProjectList, ProjectListItem  # noqa: F401
from .project_state import (  # noqa: F401
    ProjectState,
    render_project_details,
    render_project_loading,
)
from .status_bar import StatusBar  # noqa: F401
from .task_detail import TaskDetails, render_task_details  # noqa: F401
from .task_list import TaskList, TaskListItem, get_backend_name  # noqa: F401

__all__ = [
    # Project widgets
    "ProjectActions",
    "ProjectList",
    "ProjectListItem",
    "ProjectState",
    # Task widgets
    "TaskDetails",
    "TaskList",
    "TaskListItem",
    # Render helpers
    "render_project_details",
    "render_project_loading",
    "render_task_details",
    "get_backend_name",
    # Re-exported types
    "TaskMeta",
    # Status bar
    "StatusBar",
]
