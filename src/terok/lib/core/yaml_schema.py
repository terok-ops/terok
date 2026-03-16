# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v2 models mirroring the raw YAML structure of project.yml and config.yml.

These are **Tier 1** models: they validate types, enums, and unknown-key typos
(``extra="forbid"``) but do *not* resolve paths or merge config layers.  The
companion modules :mod:`~terok.lib.core.projects` and
:mod:`~terok.lib.core.config` transform these into resolved runtime objects.
"""

from __future__ import annotations

from typing import Annotated, Any, ClassVar

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

    id: str | None = None
    name: str | None = Field(default=None, description="Human-readable project name")
    security_class: str = Field(default="online", description="online or gatekeeping")

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


class RawGitSection(BaseModel):
    """The ``git:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    upstream_url: str | None = None
    default_branch: str | None = None
    human_name: str | None = None
    human_email: str | None = None
    authorship: str | None = None


class RawGlobalGitSection(BaseModel):
    """The ``git:`` section of global config.yml (identity fields only)."""

    model_config = ConfigDict(extra="forbid")

    human_name: str | None = None
    human_email: str | None = None
    authorship: str | None = None


class RawSSHSection(BaseModel):
    """The ``ssh:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    key_name: str | None = None
    host_dir: str | None = Field(default=None, description="Host-side SSH directory")
    config_template: str | None = None
    mount_in_online: bool = True
    mount_in_gatekeeping: bool = False


class RawTasksSection(BaseModel):
    """The ``tasks:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    root: str | None = None
    name_categories: NameCategories = None


class RawGateSection(BaseModel):
    """The ``gate:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    path: str | None = None


class RawUpstreamPolling(BaseModel):
    """Nested ``gatekeeping.upstream_polling`` settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    interval_minutes: int = 5


class RawAutoSync(BaseModel):
    """Nested ``gatekeeping.auto_sync`` settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    branches: list[str] = Field(default_factory=list)


class RawGatekeepingSection(BaseModel):
    """The ``gatekeeping:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    staging_root: str | None = None
    expose_external_remote: bool = False
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


class RawRunSection(BaseModel):
    """The ``run:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    shutdown_timeout: int = 10
    gpus: str | bool | None = None


class RawShieldProjectSection(BaseModel):
    """The ``shield:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    drop_on_task_start: bool = True


class RawDockerSection(BaseModel):
    """The ``docker:`` section of project.yml."""

    model_config = ConfigDict(extra="forbid")

    base_image: str = "ubuntu:24.04"
    user_snippet_inline: str | None = None
    user_snippet_file: str | None = None


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
    docker: RawDockerSection = Field(default_factory=RawDockerSection)
    default_agent: str | None = None
    default_login: str | None = None
    agent: dict[str, Any] = Field(default_factory=dict)

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
            "docker",
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


class RawUISection(BaseModel):
    """Global ``ui:`` section."""

    model_config = ConfigDict(extra="forbid")

    base_port: int = 7860


class RawEnvsSection(BaseModel):
    """Global ``envs:`` section."""

    model_config = ConfigDict(extra="forbid")

    base_dir: str | None = None


class RawPathsSection(BaseModel):
    """Global ``paths:`` section."""

    model_config = ConfigDict(extra="forbid")

    state_root: str | None = None
    build_root: str | None = None
    user_projects_root: str | None = None
    global_presets_dir: str | None = None


class RawTUISection(BaseModel):
    """Global ``tui:`` section."""

    model_config = ConfigDict(extra="forbid")

    default_tmux: bool = False


class RawLogsSection(BaseModel):
    """Global ``logs:`` section."""

    model_config = ConfigDict(extra="forbid")

    partial_streaming: bool = True


class RawShieldGlobalSection(BaseModel):
    """Global ``shield:`` section."""

    model_config = ConfigDict(extra="forbid")

    bypass_firewall_no_protection: bool = False
    profiles: dict[str, Any] | None = None
    audit: bool = True


class RawGateServerSection(BaseModel):
    """Global ``gate_server:`` section."""

    model_config = ConfigDict(extra="forbid")

    port: int = 9418
    suppress_systemd_warning: bool = False


class RawTasksGlobalSection(BaseModel):
    """Global ``tasks:`` section."""

    model_config = ConfigDict(extra="forbid")

    name_categories: NameCategories = None


# ---------------------------------------------------------------------------
# Top-level global config YAML
# ---------------------------------------------------------------------------


class RawGlobalConfig(BaseModel):
    """Validated structure of the global ``config.yml`` file."""

    model_config = ConfigDict(extra="forbid")

    ui: RawUISection = Field(default_factory=RawUISection)
    envs: RawEnvsSection = Field(default_factory=RawEnvsSection)
    paths: RawPathsSection = Field(default_factory=RawPathsSection)
    tui: RawTUISection = Field(default_factory=RawTUISection)
    logs: RawLogsSection = Field(default_factory=RawLogsSection)
    shield: RawShieldGlobalSection = Field(default_factory=RawShieldGlobalSection)
    gate_server: RawGateServerSection = Field(default_factory=RawGateServerSection)
    tasks: RawTasksGlobalSection = Field(default_factory=RawTasksGlobalSection)
    git: RawGlobalGitSection = Field(default_factory=RawGlobalGitSection)
    default_agent: str | None = None
    default_login: str | None = None
    agent: dict[str, Any] = Field(default_factory=dict)

    _SECTION_KEYS: ClassVar[frozenset[str]] = frozenset(
        {
            "ui",
            "envs",
            "paths",
            "tui",
            "logs",
            "shield",
            "gate_server",
            "tasks",
            "git",
            "agent",
        }
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_none_to_defaults(cls, data: Any) -> Any:
        """Coerce top-level ``None`` section values to ``{}``."""
        return _coerce_none_sections(data, cls._SECTION_KEYS)
