# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v2 models mirroring the raw YAML structure of project.yml and config.yml.

These are **Tier 1** models: they validate types, enums, and unknown-key typos
(``extra="forbid"``) but do *not* resolve paths or merge config layers.  The
companion modules :mod:`~terok.lib.core.projects` and
:mod:`~terok.lib.core.config` transform these into resolved runtime objects.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Shared reusable validators / annotated types
# ---------------------------------------------------------------------------


def _coerce_name_categories(v: object) -> list[str] | None:
    """Normalize ``name_categories``: single string → list, empty → None.

    Raises :class:`ValueError` for non-string, non-list inputs (e.g. ``42``).
    """
    if v is None:
        return None
    if isinstance(v, str):
        return [v.strip()] if v.strip() else None
    if isinstance(v, list):
        if not v:
            return None
        if not all(isinstance(item, str) for item in v):
            raise ValueError("name_categories items must be strings")
        return v
    raise ValueError(f"name_categories must be a string or list of strings, got {type(v).__name__}")


NameCategories = Annotated[list[str] | None, BeforeValidator(_coerce_name_categories)]
"""Reusable type: ``list[str] | str | None`` coerced to ``list[str] | None``."""


def _coerce_none_sections(data: Any, section_keys: frozenset[str]) -> Any:
    """Pre-process raw YAML: coerce ``None`` section values to ``{}``.

    Only keys listed in *section_keys* are coerced — leaf keys that are
    legitimately ``None`` (e.g. ``upstream_url``) are left untouched.
    """
    if not isinstance(data, dict):
        return data
    return {k: ({} if k in section_keys and v is None else v) for k, v in data.items()}


# ---------------------------------------------------------------------------
# Project YAML section models
# ---------------------------------------------------------------------------


class RawProjectSection(BaseModel):
    """The ``project:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None, description="Unique project identifier (lowercase, ``[a-z0-9_-]``)"
    )
    name: str | None = Field(default=None, description="Human-readable project name (display only)")
    security_class: str = Field(
        default="online",
        description="Security mode: ``online`` (direct push) or ``gatekeeping`` (gated mirror)",
    )
    isolation: str = Field(
        default="shared", description="shared (bind mounts) or sealed (no mounts)"
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str | None) -> str | None:
        """Validate project ID format when explicitly set."""
        if v is None:
            return None
        import re

        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", v):
            raise ValueError(
                f"must match [a-z0-9][a-z0-9_-]* (lowercase, no path separators), got {v!r}"
            )
        return v

    @field_validator("security_class")
    @classmethod
    def _validate_security_class(cls, v: str) -> str:
        """Normalize and validate the security class enum."""
        v = v.strip().lower()
        if v not in ("online", "gatekeeping"):
            raise ValueError(f"must be 'online' or 'gatekeeping', got {v!r}")
        return v

    @field_validator("isolation")
    @classmethod
    def _validate_isolation(cls, v: str) -> str:
        """Normalize and validate the isolation mode enum."""
        v = v.strip().lower()
        if v not in ("shared", "sealed"):
            raise ValueError(f"must be 'shared' or 'sealed', got {v!r}")
        return v


class RawGitSection(BaseModel):
    """The ``git:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    upstream_url: str | None = Field(
        default=None, description="Repository URL to clone into task containers"
    )
    default_branch: str | None = Field(
        default=None, description="Default branch name (e.g. ``main``)"
    )
    human_name: str | None = Field(
        default=None, description="Human name for git committer identity"
    )
    human_email: str | None = Field(
        default=None, description="Human email for git committer identity"
    )
    authorship: str | None = Field(
        default=None,
        description=(
            "How agent/human map to git author/committer."
            " Values: ``agent-human``, ``human-agent``, ``agent``, ``human``"
        ),
    )


class RawGlobalGitSection(BaseModel):
    """The ``git:`` section of global config.yml (identity fields only)."""

    model_config = ConfigDict(extra="forbid")

    human_name: str | None = Field(
        default=None, description="Human name for git committer identity"
    )
    human_email: str | None = Field(
        default=None, description="Human email for git committer identity"
    )
    authorship: str | None = Field(
        default=None,
        description=(
            "How agent/human map to git author/committer."
            " Values: ``agent-human``, ``human-agent``, ``agent``, ``human``"
        ),
    )


class RawSSHSection(BaseModel):
    """The ``ssh:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    key_name: str | None = Field(
        default=None, description="SSH key filename (default: ``id_ed25519_<project_id>``)"
    )
    host_dir: str | None = Field(
        default=None,
        description="Host directory for SSH key storage (keys served via SSH agent proxy, not mounted)",
    )
    config_template: str | None = Field(
        default=None,
        description=(
            "Path to an SSH config template file"
            " (supports ``{{IDENTITY_FILE}}``, ``{{KEY_NAME}}``, ``{{PROJECT_ID}}``)"
        ),
    )
    allow_host_keys: bool = Field(
        default=False,
        description="Allow fallback to ~/.ssh host keys for gate operations",
    )


class RawTasksSection(BaseModel):
    """The ``tasks:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    root: str | None = Field(default=None, description="Override task workspace root directory")
    name_categories: NameCategories = Field(
        default=None,
        description="Word categories for auto-generated task names (string or list of strings)",
    )


class RawGateSection(BaseModel):
    """The ``gate:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(default=None, description="Override git gate (mirror) path")


class RawUpstreamPolling(BaseModel):
    """Nested ``gatekeeping.upstream_polling`` settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=True, description="Poll upstream for new commits")
    interval_minutes: int = Field(default=5, description="Polling interval in minutes")


class RawAutoSync(BaseModel):
    """Nested ``gatekeeping.auto_sync`` settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, description="Auto-sync branches from upstream to gate")
    branches: list[str] = Field(default_factory=list, description="Branch names to auto-sync")


class RawGatekeepingSection(BaseModel):
    """The ``gatekeeping:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    staging_root: str | None = Field(
        default=None, description="Staging directory for gatekeeping builds"
    )
    expose_external_remote: bool = Field(
        default=False,
        description="Add upstream URL as ``external`` remote in gatekeeping containers",
    )
    upstream_polling: RawUpstreamPolling = Field(default_factory=RawUpstreamPolling)
    auto_sync: RawAutoSync = Field(default_factory=RawAutoSync)

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_subsections(cls, data: Any) -> Any:
        """Coerce None sub-sections to empty dicts."""
        if isinstance(data, dict):
            for key in ("upstream_polling", "auto_sync"):
                if data.get(key) is None:
                    data[key] = {}
        return data


class RawHooksSection(BaseModel):
    """Task lifecycle hook commands (run on host, not inside containers)."""

    model_config = ConfigDict(extra="forbid")

    pre_start: str | None = None
    post_start: str | None = None
    post_ready: str | None = None
    post_stop: str | None = None


class RawRunSection(BaseModel):
    """The ``run:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    shutdown_timeout: int = Field(
        default=10, description="Seconds to wait before SIGKILL on container stop"
    )
    gpus: str | bool | None = Field(
        default=None,
        description='GPU passthrough: ``true``, ``"all"``, or omit to disable',
    )
    memory: str | None = None
    cpus: str | None = None
    nested_containers: bool = Field(
        default=False,
        description=(
            "Declares that the project runs podman/docker inside its container. "
            "When true, the outer container is launched with ``--security-opt "
            "label=nested`` and ``--device /dev/fuse`` so rootless nested "
            "containers work under SELinux without disabling labels wholesale."
        ),
    )
    hooks: RawHooksSection = Field(default_factory=RawHooksSection)

    @field_validator("memory", "cpus", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> str | None:
        """Normalise empty / whitespace-only strings to ``None``."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_subsections(cls, data: Any) -> Any:
        """Coerce None sub-sections to empty dicts."""
        if isinstance(data, dict):
            for key in ("hooks",):
                if data.get(key) is None:
                    data[key] = {}
        return data


class RawShieldProjectSection(BaseModel):
    """The ``shield:`` section of project.yml.

    Both fields default to ``None`` (inherit from global ``config.yml``).
    """

    model_config = ConfigDict(extra="forbid")

    drop_on_task_run: bool | None = Field(
        default=None,
        description="Drop shield (bypass firewall) when task container is created",
    )
    on_task_restart: Literal["retain", "up"] | None = Field(
        default=None,
        description="Shield policy on container restart: ``retain`` or ``up``",
    )


class RawImageSection(BaseModel):
    """The ``image:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    base_image: str = Field(default="ubuntu:24.04", description="Base container image for builds")
    family: Literal["deb", "rpm"] | None = Field(
        default=None,
        description=(
            "Package family for the L0/L1 build (``deb`` or ``rpm``). "
            "Leave unset to auto-detect from *base_image*; set explicitly "
            "when the image is outside the known allowlist."
        ),
    )
    agents: str | None = Field(
        default=None,
        description=(
            'Comma-separated roster entries to install in L1, or "all". '
            "Inherits from the global config when unset."
        ),
    )
    user_snippet_inline: str | None = Field(
        default=None, description="Inline Dockerfile snippet injected into the project image"
    )
    user_snippet_file: str | None = Field(
        default=None, description="Path to a file containing a Dockerfile snippet"
    )


# ---------------------------------------------------------------------------
# Top-level project YAML
# ---------------------------------------------------------------------------


class RawProjectYaml(BaseModel):
    """Validated structure of a ``project.yml`` file."""

    model_config = ConfigDict(extra="forbid")

    project: RawProjectSection = Field(default_factory=RawProjectSection)
    git: RawGitSection = Field(default_factory=RawGitSection)
    ssh: RawSSHSection = Field(default_factory=RawSSHSection)
    tasks: RawTasksSection = Field(default_factory=RawTasksSection)
    gate: RawGateSection = Field(default_factory=RawGateSection)
    gatekeeping: RawGatekeepingSection = Field(default_factory=RawGatekeepingSection)
    run: RawRunSection = Field(default_factory=RawRunSection)
    shield: RawShieldProjectSection = Field(default_factory=RawShieldProjectSection)
    image: RawImageSection = Field(default_factory=RawImageSection)
    default_agent: str | None = Field(
        default=None, description="Default agent provider (e.g. ``claude``, ``codex``)"
    )
    default_login: str | None = None
    shared_dir: bool | str | None = Field(
        default=None,
        description="Shared directory for multi-agent IPC (``true`` = auto-create under tasks root, or absolute path)",
    )
    agent: dict[str, Any] = Field(
        default_factory=dict,
        description="Agent configuration dict (model, subagents, MCP servers, etc.)",
    )

    _SECTION_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "project",
            "git",
            "ssh",
            "tasks",
            "gate",
            "gatekeeping",
            "run",
            "shield",
            "image",
            "agent",
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_to_defaults(cls, data: Any) -> Any:
        """Coerce top-level ``None`` section values to ``{}``."""
        return _coerce_none_sections(data, cls._SECTION_KEYS)


# ---------------------------------------------------------------------------
# Global config section models
# ---------------------------------------------------------------------------


class RawCredentialsSection(BaseModel):
    """Global ``credentials:`` section."""

    model_config = ConfigDict(extra="forbid")

    dir: str | None = Field(
        default=None,
        description="Shared credentials directory (proxy DB, agent config mounts)",
    )


class RawPathsSection(BaseModel):
    """Global ``paths:`` section.

    ``root`` is the namespace state root read by all ecosystem packages
    (Podman model).
    """

    model_config = ConfigDict(extra="forbid")

    root: str | None = Field(
        default=None,
        description=(
            "Namespace state root shared by all ecosystem packages"
            " (Podman model — one config, multiple readers)"
        ),
    )
    build_dir: str | None = Field(
        default=None, description="Build artifacts directory (generated Dockerfiles)"
    )
    sandbox_live_dir: str | None = Field(
        default=None,
        description=(
            "Container-writable runtime data (tasks, agent mounts)."
            " For hardened installs, mount the target with ``noexec,nosuid,nodev``"
        ),
    )
    user_projects_dir: str | None = Field(
        default=None, description="User projects directory (per-user project configs)"
    )
    user_presets_dir: str | None = Field(
        default=None, description="User presets directory (per-user preset configs)"
    )
    port_registry_dir: str | None = Field(
        default=None, description="Shared port registry directory for multi-user isolation"
    )


class RawTUISection(BaseModel):
    """Global ``tui:`` section."""

    model_config = ConfigDict(extra="forbid")

    default_tmux: bool = Field(
        default=False, description="Default to tmux mode when launching the TUI"
    )


class RawLogsSection(BaseModel):
    """Global ``logs:`` section."""

    model_config = ConfigDict(extra="forbid")

    partial_streaming: bool = Field(
        default=True, description="Enable typewriter-effect streaming for log viewing"
    )


class RawShieldGlobalSection(BaseModel):
    """Global ``shield:`` section."""

    model_config = ConfigDict(extra="forbid")

    bypass_firewall_no_protection: bool = Field(
        default=False, description="**Dangerous**: disable egress firewall entirely"
    )
    profiles: dict[str, Any] | None = Field(
        default=None, description="Named shield profiles for per-project firewall rules"
    )
    audit: bool = Field(default=True, description="Enable shield audit logging")
    drop_on_task_run: bool = True
    on_task_restart: Literal["retain", "up"] = "retain"


class RawServicesSection(BaseModel):
    """Global ``services:`` section — transport mode for host ↔ container IPC."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["tcp", "socket"] = "tcp"


class RawVaultSection(BaseModel):
    """Global ``vault:`` section (token broker + SSH signer)."""

    model_config = ConfigDict(extra="forbid")

    bypass_no_secret_protection: bool = False
    transport: Literal["direct", "socket"] = "direct"
    port: int | None = Field(default=None, ge=1, le=65535)
    ssh_signer_port: int | None = Field(default=None, ge=1, le=65535)


class RawGateServerSection(BaseModel):
    """Global ``gate_server:`` section."""

    model_config = ConfigDict(extra="forbid")

    port: int | None = Field(default=None, ge=1, le=65535, description="Gate server listen port")
    repos_dir: str | None = Field(
        default=None,
        description="Override gate repo directory (default: ``state_dir/gate``)",
    )
    suppress_systemd_warning: bool = Field(
        default=False, description="Suppress the systemd unit installation suggestion"
    )


class RawTasksGlobalSection(BaseModel):
    """Global ``tasks:`` section."""

    model_config = ConfigDict(extra="forbid")

    name_categories: NameCategories = Field(
        default=None,
        description="Word categories for auto-generated task names (string or list of strings)",
    )


class RawNetworkSection(BaseModel):
    """Global ``network:`` section — port range and future network settings."""

    model_config = ConfigDict(extra="forbid")

    port_range_start: int = Field(default=18700, ge=1024, le=65535)
    port_range_end: int = Field(default=32700, ge=1024, le=65535)

    @model_validator(mode="after")
    def _check_port_range(self) -> RawNetworkSection:
        if self.port_range_start > self.port_range_end:
            raise ValueError("port_range_start must be <= port_range_end")
        return self


# ---------------------------------------------------------------------------
# Top-level global config YAML
# ---------------------------------------------------------------------------


class RawGlobalConfig(BaseModel):
    """Validated structure of the global ``config.yml`` file."""

    model_config = ConfigDict(extra="forbid")

    credentials: RawCredentialsSection = Field(default_factory=RawCredentialsSection)
    paths: RawPathsSection = Field(default_factory=RawPathsSection)
    tui: RawTUISection = Field(default_factory=RawTUISection)
    logs: RawLogsSection = Field(default_factory=RawLogsSection)
    shield: RawShieldGlobalSection = Field(default_factory=RawShieldGlobalSection)
    services: RawServicesSection = Field(default_factory=RawServicesSection)
    vault: RawVaultSection = Field(default_factory=RawVaultSection)
    gate_server: RawGateServerSection = Field(default_factory=RawGateServerSection)
    network: RawNetworkSection = Field(default_factory=RawNetworkSection)
    tasks: RawTasksGlobalSection = Field(default_factory=RawTasksGlobalSection)
    git: RawGlobalGitSection = Field(default_factory=RawGlobalGitSection)
    hooks: RawHooksSection = Field(default_factory=RawHooksSection)
    image: RawImageSection = Field(default_factory=RawImageSection)
    experimental: bool = False
    default_agent: str | None = None
    default_login: str | None = None
    agent: dict[str, Any] = Field(default_factory=dict)

    _SECTION_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "credentials",
            "paths",
            "tui",
            "logs",
            "shield",
            "services",
            "vault",
            "gate_server",
            "network",
            "tasks",
            "git",
            "hooks",
            "image",
            "agent",
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_to_defaults(cls, data: Any) -> Any:
        """Coerce top-level ``None`` section values to ``{}``."""
        return _coerce_none_sections(data, cls._SECTION_KEYS)
