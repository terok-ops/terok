# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Project and preset data models.

Pure data types with no filesystem or subprocess I/O.  The companion
``projects`` module handles discovery, loading, and serialisation.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Project:
    """Resolved project configuration loaded from ``project.yml``."""

    id: str
    security_class: str  # "online" | "gatekeeping"
    upstream_url: str | None
    default_branch: str
    root: Path

    tasks_root: Path  # workspace dirs
    gate_path: Path  # git gate (mirror) path
    staging_root: Path | None  # gatekeeping only

    ssh_key_name: str | None
    ssh_host_dir: Path | None
    # Optional path to an SSH config template (user-provided). If set, ssh-init
    # will render this template to the shared .ssh/config. Tokens supported:
    #   {{IDENTITY_FILE}}  -> absolute path of the generated private key
    #   {{KEY_NAME}}       -> filename of the generated key (no .pub)
    #   {{PROJECT_ID}}     -> project id
    ssh_config_template: Path | None = None
    # Whether to mount SSH credentials in online mode. Default: True.
    ssh_mount_in_online: bool = True
    # Whether to mount SSH credentials in gatekeeping mode. Default: False.
    ssh_mount_in_gatekeeping: bool = False
    # Whether to expose the upstream URL as a remote named "external" in gatekeeping mode.
    # This allows the container to also reference the real upstream.
    expose_external_remote: bool = False
    # Optional human credentials for git committer (while AI is the author)
    human_name: str | None = None
    human_email: str | None = None
    # Upstream polling configuration for gatekeeping mode
    upstream_polling_enabled: bool = True
    upstream_polling_interval_minutes: int = 5
    # Auto-sync configuration for gatekeeping mode
    auto_sync_enabled: bool = False
    auto_sync_branches: list[str] = field(default_factory=list)
    # Default agent preference (codex, claude, mistral) - used for Web UI and potentially CLI
    default_agent: str | None = None
    # Agent configuration dict (from project.yml agent: section)
    agent_config: dict = field(default_factory=dict)
    # Seconds to wait before SIGKILL when stopping a container (podman stop --time).
    # Default 10 matches podman's built-in default.
    shutdown_timeout: int = 10
    # Task name categories for unique-namer (from tasks.name_categories in project.yml)
    task_name_categories: list[str] | None = None

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


def effective_ssh_key_name(project: Project, key_type: str = "ed25519") -> str:
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


def validate_project_id(project_id: str) -> None:
    """Ensure a project ID is safe for use as a directory and OCI image name.

    Raises SystemExit if the ID is empty, contains uppercase letters, path
    separators or traversal sequences, or uses characters outside
    ``[a-z0-9_-]``.
    """
    if not project_id:
        raise SystemExit("Project ID must not be empty")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", project_id):
        raise SystemExit(
            f"Invalid project ID '{project_id}': "
            "must start with a lowercase letter or digit, followed by lowercase letters, "
            "digits, hyphens, or underscores"
        )
