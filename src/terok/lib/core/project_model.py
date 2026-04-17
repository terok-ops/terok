# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project and preset data models — DDD Value Objects.

Pure data types with no filesystem or subprocess I/O.  These are the
**value objects** in the domain model: they carry configuration data but
have no behavior beyond computed paths.

:class:`ProjectConfig` is loaded from ``project.yml`` by the companion
:mod:`~terok.lib.core.projects` module and wrapped by the rich
:class:`~terok.lib.domain.project.Project` aggregate to provide behavior.
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field


class ProjectConfig(BaseModel):
    """Resolved project configuration loaded from ``project.yml``.

    Pure value object — holds configuration fields with no behavior beyond
    computed paths.  The rich domain object :class:`~terok.lib.domain.project.Project`
    wraps this and provides behavior.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    security_class: str  # "online" | "gatekeeping"
    isolation: str = "shared"  # "shared" | "sealed"
    upstream_url: str | None
    default_branch: str | None
    root: Path

    tasks_root: Path  # workspace dirs
    gate_path: Path  # git gate (mirror) path
    staging_root: Path | None  # gatekeeping only

    ssh_key_name: str | None
    ssh_host_dir: Path | None
    ssh_config_template: Path | None = None
    ssh_allow_host_keys: bool = False
    expose_external_remote: bool = False
    human_name: str | None = None
    human_email: str | None = None
    git_authorship: str = "agent-human"
    upstream_polling_enabled: bool = True
    upstream_polling_interval_minutes: int = 5
    auto_sync_enabled: bool = False
    auto_sync_branches: list[str] = Field(default_factory=list)
    default_agent: str | None = None
    default_login: str | None = None
    agent_config: dict[str, Any] = Field(default_factory=dict)
    shutdown_timeout: int = 10
    memory_limit: str | None = None
    """Podman ``--memory`` limit from ``run.memory`` in project.yml."""
    cpu_limit: str | None = None
    """Podman ``--cpus`` limit from ``run.cpus`` in project.yml."""
    task_name_categories: list[str] | None = None
    shield_drop_on_task_run: bool = True
    shield_on_task_restart: str = "retain"
    # Lifecycle hooks (host-side commands)
    hook_pre_start: str | None = None
    hook_post_start: str | None = None
    hook_post_ready: str | None = None
    hook_post_stop: str | None = None
    # Image configuration (flattened from image: section)
    base_image: str = "ubuntu:24.04"
    family: Literal["deb", "rpm"] | None = None
    """Package family override for L0/L1 builds.

    ``None`` lets terok-executor auto-detect from *base_image*; set
    explicitly when the auto-detect allowlist doesn't recognise the
    image (rocky, alma, suse, …).
    """
    snippet_inline: str | None = None
    snippet_file: str | None = None
    # Shared task directory (multi-agent IPC)
    shared_dir: Path | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_sealed(self) -> bool:
        """Whether this project uses sealed isolation (zero bind mounts)."""
        return self.isolation == "sealed"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def presets_dir(self) -> Path:
        """Directory for preset config files for this project."""
        return self.root / "presets"


@dataclass
class PresetInfo:
    """Metadata about a discovered preset."""

    name: str
    source: str  # "project" | "global" | "bundled"
    path: Path


def effective_ssh_key_name(project: ProjectConfig, key_type: str = "ed25519") -> str:
    """Return the SSH key filename that should be used for this project.

    Precedence:
      1. Explicit `ssh.key_name` from project.yml (project.ssh_key_name)
      2. Derived default: id_<type>_<project_id>, e.g. id_ed25519_myproj

    This helper centralizes the default so ssh-init, container env (SSH_KEY_NAME)
    and host-side git helpers all agree even when project.yml omits ssh.key_name.
    """

    if project.ssh_key_name:
        return project.ssh_key_name
    algo = "ed25519" if key_type == "ed25519" else "rsa"
    return f"id_{algo}_{project.id}"


_PROJECT_ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


def is_valid_project_id(project_id: str) -> bool:
    """Return whether *project_id* matches the ``[a-z0-9][a-z0-9_-]*`` contract."""
    return bool(project_id) and _PROJECT_ID_RE.fullmatch(project_id) is not None


def validate_project_id(project_id: str) -> None:
    """Ensure a project ID is safe for use as a directory and OCI image name.

    Raises SystemExit if the ID is empty, contains uppercase letters, path
    separators or traversal sequences, or uses characters outside
    ``[a-z0-9_-]``.
    """
    if not is_valid_project_id(project_id):
        raise SystemExit(
            f"Invalid project ID '{project_id}': "
            "must start with a lowercase letter or digit, followed by lowercase letters, "
            "digits, hyphens, or underscores"
        )
