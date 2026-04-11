# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Global configuration, directory helpers, and preset/image path resolution."""

import os
import sys
from collections.abc import Callable
from importlib import resources as _pkg_resources
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..util.yaml import YAMLError, load as _yaml_load
from .paths import config_root as _config_root_base
from .yaml_schema import RawGlobalConfig

# ---------- Prefix & roots ----------


def get_prefix() -> Path:
    """
    Minimal prefix helper used primarily for pip/venv installs.

    Order:
    - If TEROK_PREFIX is set, use it.
    - Otherwise, use sys.prefix.

    Note: Do not use this for config/data discovery - see the dedicated
    helpers below which follow common Linux/XDG conventions.
    """
    env = os.environ.get("TEROK_PREFIX")
    if env:
        return Path(env).expanduser().resolve()
    return Path(sys.prefix).resolve()


def projects_dir() -> Path:
    """
    System projects directory. Uses FHS/XDG via terok.lib.paths.

    Returns the ``projects`` subdirectory under the base config root.
    """
    return _config_root_base().resolve() / "projects"


def global_config_search_paths() -> list[Path]:
    """Return the ordered list of paths that will be checked for global config.

    Behavior matches global_config_path():
    - If TEROK_CONFIG_FILE is set, only that single path is considered.
    - Otherwise, check in order:
        1) ${XDG_CONFIG_HOME:-~/.config}/terok/config.yml
        2) sys.prefix/etc/terok/config.yml
        3) /etc/terok/config.yml
    """
    env_file = os.environ.get("TEROK_CONFIG_FILE")
    if env_file:
        return [Path(env_file).expanduser().resolve()]

    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    user_cfg = (Path(xdg_home) if xdg_home else Path.home() / ".config") / "terok" / "config.yml"
    sp_cfg = Path(sys.prefix) / "etc" / "terok" / "config.yml"
    etc_cfg = Path("/etc/terok/config.yml")
    return [user_cfg, sp_cfg, etc_cfg]


def global_config_path() -> Path:
    """Global config file path (resolved based on search paths).

    Resolution order (first existing wins, except explicit override is returned even
    if missing to make intent visible to the user):
    - TEROK_CONFIG_FILE env (returned as-is)
    - ${XDG_CONFIG_HOME:-~/.config}/terok/config.yml (user override)
    - sys.prefix/etc/terok/config.yml (pip wheels)
    - /etc/terok/config.yml (system default)
    If none exist, return the last path (/etc/terok/config.yml).
    """
    candidates = global_config_search_paths()
    # If TEROK_CONFIG_FILE is set, candidates has a single element and we
    # want to return it even if it doesn't exist.
    if len(candidates) == 1:
        return candidates[0]

    for c in candidates:
        if c.is_file():
            return c.resolve()
    return candidates[-1]


# ---------- Global config (cached) ----------


_validated_config_cache: RawGlobalConfig | None = None


def _load_validated() -> RawGlobalConfig:
    """Load and validate the global config, returning a typed model (cached).

    Warnings are emitted once on first load; subsequent calls return the
    cached result without re-parsing or re-warning.
    """
    global _validated_config_cache  # noqa: PLW0603
    if _validated_config_cache is not None:
        return _validated_config_cache

    from ..util.logging_utils import warn_user

    cfg_path = global_config_path()
    if not cfg_path.is_file():
        _validated_config_cache = RawGlobalConfig()
        return _validated_config_cache
    try:
        raw = _yaml_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError) as exc:
        warn_user("config", f"Cannot read {cfg_path}: {exc}. Using defaults.")
        _validated_config_cache = RawGlobalConfig()
        return _validated_config_cache
    except YAMLError as exc:
        warn_user("config", f"Malformed YAML in {cfg_path}: {exc}. Using defaults.")
        _validated_config_cache = RawGlobalConfig()
        return _validated_config_cache
    try:
        _validated_config_cache = RawGlobalConfig.model_validate(raw)
    except ValidationError as exc:
        field_errors = "; ".join(
            f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
        )
        warn_user("config", f"Invalid config {cfg_path}: {field_errors}. Using defaults.")
        _validated_config_cache = RawGlobalConfig()
    return _validated_config_cache


def load_global_config() -> dict[str, Any]:
    """Load and return the global terok configuration as a dict."""
    cfg_path = global_config_path()
    if not cfg_path.is_file():
        return {}
    return _yaml_load(cfg_path.read_text()) or {}


def get_global_section(key: str) -> dict[str, Any]:
    """Return a top-level section from the global config, defaulting to ``{}``.

    If the value under *key* is not a dict (e.g. the user wrote ``git: "oops"``),
    returns ``{}`` to avoid ``AttributeError`` in callers that expect ``.get()``.
    """
    cfg = load_global_config()
    value = cfg.get(key, {})
    return value if isinstance(value, dict) else {}


# ---------- Path resolution ----------


def _resolve_path(
    env_var: str | None,
    config_key: tuple[str, str] | None,
    default: Callable[[], Path],
) -> Path:
    """Resolve a path: env var → global config → computed default.

    This replaces the repeated try/except + load_global_config() pattern
    that was duplicated across ``state_dir``, ``build_dir``, etc.
    """
    if env_var:
        env = os.environ.get(env_var)
        if env:
            return Path(env).expanduser().resolve()

    if config_key:
        try:
            section = get_global_section(config_key[0])
            val = section.get(config_key[1])
            if val:
                return Path(val).expanduser().resolve()
        except (OSError, KeyError, TypeError, ValueError, YAMLError) as exc:
            from ..util.logging_utils import log_warning

            log_warning(
                f"Config key {config_key[0]}.{config_key[1]} lookup failed: {exc}; "
                f"using default path"
            )

    return default().resolve()


def state_dir() -> Path:
    """Terok core state directory (host-only: build artifacts, task metadata).

    Precedence:
    - ``TEROK_STATE_DIR`` environment variable (per-package escape hatch).
    - Namespace root (``TEROK_ROOT`` / ``config.yml`` ``paths.root``) + ``core/``.
    - Platform default (``~/.local/share/terok/core``).
    """
    from terok_sandbox.paths import namespace_state_dir

    return namespace_state_dir("core", "TEROK_STATE_DIR").resolve()


def sandbox_live_dir() -> Path:
    """Container-writable runtime data (tasks, agent mounts).

    All directories that are bind-mounted into containers live under this
    tree.  For hardened installations, mount on a separate partition with
    ``noexec,nosuid,nodev``.

    Precedence:
    - ``TEROK_SANDBOX_LIVE_DIR`` environment variable.
    - Global config ``paths.sandbox_live_dir``.
    - Namespace root + ``sandbox-live/``.
    """
    from terok_sandbox.paths import namespace_state_dir

    return _resolve_path(
        "TEROK_SANDBOX_LIVE_DIR",
        ("paths", "sandbox_live_dir"),
        lambda: namespace_state_dir("sandbox-live"),
    )


def sandbox_live_mounts_dir() -> Path:
    """Provider config mounts directory (container-writable).

    Each agent/tool gets a subdirectory (e.g. ``_claude-config/``) that is
    bind-mounted into task containers.
    """
    return sandbox_live_dir() / "mounts"


def gate_repos_dir() -> Path:
    """Directory that holds per-project bare gate repos.

    Precedence:
    - ``gate_server.repos_dir`` in global config (explicit override).
    - Sandbox's default ``gate_base_path`` (``sandbox/gate/``).
    """
    custom = _load_validated().gate_server.repos_dir
    if custom:
        return Path(custom).expanduser().resolve()
    return make_sandbox_config().gate_base_path


def _xdg_config_subdir(subdir: str) -> Path:
    """Return ``$XDG_CONFIG_HOME/terok/<subdir>`` (or ``~/.config/…`` fallback)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "terok" / subdir


def user_projects_dir() -> Path:
    """User projects directory.

    Precedence:
    - Global config: paths.user_projects_dir
    - XDG_CONFIG_HOME/terok/projects
    - ~/.config/terok/projects
    """
    return _resolve_path(
        None, ("paths", "user_projects_dir"), lambda: _xdg_config_subdir("projects")
    )


def user_presets_dir() -> Path:
    """User presets directory (shared across all projects).

    Precedence:
    - Global config: paths.user_presets_dir
    - XDG_CONFIG_HOME/terok/core/presets
    - ~/.config/terok/core/presets
    """
    return _resolve_path(
        None, ("paths", "user_presets_dir"), lambda: _xdg_config_subdir("core") / "presets"
    )


def bundled_presets_dir() -> Path:
    """Presets shipped with the terok package.

    These serve as ready-to-use defaults that users can reference directly
    (``--preset solo``) or copy to their global presets dir to customize.
    Lowest priority in the search order: project > global > bundled.
    """
    return Path(str(_pkg_resources.files("terok") / "resources" / "presets"))


def build_dir() -> Path:
    """
    Directory for build artifacts (generated Dockerfiles, etc.).

    Resolution order:
    - Global config: paths.build_dir
    - Otherwise: state_dir()/build
    """
    return _resolve_path(None, ("paths", "build_dir"), lambda: state_dir() / "build")


def archive_dir() -> Path:
    """Namespace archive tree for project and task archives.

    All archived data lives under one discoverable location::

        archive/
            <ts>_<project>.tar.gz     # deleted-project snapshots
            <project>/tasks/          # task archives (live projects)

    The ``<project>/`` subdirectory is bundled into the project tar and
    removed on project deletion — freeing the name for reuse.
    """
    from terok_sandbox.paths import namespace_state_dir

    return namespace_state_dir("archive").resolve()


def get_ui_base_port() -> int:
    """Return the base port for the web UI (default 7860)."""
    return _load_validated().ui.base_port


def credentials_dir() -> Path:
    """Return the base directory for shared credentials.

    Precedence:
    - ``TEROK_CREDENTIALS_DIR`` environment variable.
    - Global config ``credentials.dir``.
    - Namespace root + ``credentials/`` (honors ``paths.root``).
    """
    from terok_sandbox.paths import namespace_state_dir

    return _resolve_path(
        "TEROK_CREDENTIALS_DIR",
        ("credentials", "dir"),
        lambda: namespace_state_dir("credentials"),
    )


def make_sandbox_config() -> "SandboxConfig":  # noqa: F821 — forward ref
    """Construct a :class:`SandboxConfig` for sandbox operations.

    Bridges terok's config layer (env vars → config.yml → XDG defaults) to
    sandbox's plain dataclass.  Sandbox uses its own ``state_dir`` default
    (``~/.local/share/terok/sandbox/``) — terok no longer overrides it.

    This is the **single source of truth** for config bridging — every
    SandboxConfig field that terok controls must be set here.
    """
    from terok_sandbox import SandboxConfig

    return SandboxConfig(
        credentials_dir=credentials_dir(),
        gate_port=get_gate_server_port(),
        shield_bypass=get_shield_bypass_firewall_no_protection(),
        shield_audit=get_shield_audit(),
    )


def get_global_human_name() -> str | None:
    """Return git.human_name from global config, or None if not set."""
    return _load_validated().git.human_name


def get_global_human_email() -> str | None:
    """Return git.human_email from global config, or None if not set."""
    return _load_validated().git.human_email


def get_global_default_agent() -> str | None:
    """Return default_agent from global config, or None if not set."""
    return _load_validated().default_agent


def get_global_default_login() -> str | None:
    """Return default_login from global config, or None if not set."""
    return _load_validated().default_login


def get_tui_default_tmux() -> bool:
    """Return whether to default to tmux mode for TUI, or False if not set."""
    return _load_validated().tui.default_tmux


def get_logs_partial_streaming() -> bool:
    """Return whether partial streaming is enabled for log viewing (default True).

    Global config (config.yml)::

        logs:
          partial_streaming: false  # disable typewriter effect
    """
    return _load_validated().logs.partial_streaming


def get_task_name_categories() -> list[str] | None:
    """Return ``tasks.name_categories`` from global config, or ``None`` if unset.

    The value may be a list of category strings (e.g. ``["animals", "food"]``)
    or a single string.  Returns ``None`` when the key is absent or empty.
    """
    return _load_validated().tasks.name_categories


# Presentation-layer hint appended to CLI/TUI messages when the shield is weakened.
SHIELD_SECURITY_HINT = "See: https://terok-ai.github.io/terok/shield-security/"


def get_credential_proxy_bypass() -> bool:
    """Return whether the credential proxy is globally bypassed.

    .. danger::

        When True, real API keys and OAuth tokens are mounted directly into
        task containers via shared config directories — the same behavior as
        before the credential proxy was implemented.  Use only for debugging
        or environments where the proxy cannot run.

    Global config (config.yml)::

        credential_proxy:
          bypass_no_secret_protection: true
    """
    return _load_validated().credential_proxy.bypass_no_secret_protection


def get_credential_proxy_transport() -> str:
    """Return the credential proxy transport mode (``"direct"`` or ``"socket"``).

    Global config (config.yml)::

        credential_proxy:
          transport: socket   # or "direct"
    """
    return _load_validated().credential_proxy.transport


def get_shield_bypass_firewall_no_protection() -> bool:
    """Return whether the shield firewall is globally bypassed.

    .. danger::

        This is a **dangerous transitional override** that disables the egress
        firewall entirely.  It exists only as an escape hatch for users whose
        podman version is incompatible with the OCI-hook-based shield.  It
        will be removed once terok-shield supports all target podman versions.

    Global config (config.yml)::

        shield:
          bypass_firewall_no_protection: true
    """
    return _load_validated().shield.bypass_firewall_no_protection


def get_shield_drop_on_task_run() -> bool:
    """Return the global default for ``shield.drop_on_task_run``."""
    return _load_validated().shield.drop_on_task_run


def get_shield_on_task_restart() -> str:
    """Return the global default for ``shield.on_task_restart``."""
    return _load_validated().shield.on_task_restart


def get_shield_audit() -> bool:
    """Return the global default for ``shield.audit``."""
    return _load_validated().shield.audit


def get_public_host() -> str:
    """Return the advertised hostname from ``TEROK_PUBLIC_HOST``, or ``127.0.0.1``."""
    return os.environ.get("TEROK_PUBLIC_HOST", "").strip() or "127.0.0.1"


def get_gate_server_port() -> int:
    """Return the gate server port from global config (default 9418)."""
    return _load_validated().gate_server.port


def get_gate_server_suppress_warning() -> bool:
    """Return whether to suppress the systemd suggestion warning."""
    return _load_validated().gate_server.suppress_systemd_warning


def get_global_hooks() -> tuple[str | None, str | None, str | None, str | None]:
    """Return ``(pre_start, post_start, post_ready, post_stop)`` from global config hooks."""
    h = _load_validated().hooks
    return h.pre_start, h.post_start, h.post_ready, h.post_stop


def get_global_agent_config() -> dict[str, Any]:
    """Return the ``agent:`` section from the global config, or ``{}``."""
    return get_global_section("agent")


# ---------- Experimental feature flag ----------

_experimental: bool = False


def is_experimental() -> bool:
    """Return whether experimental features are enabled (CLI flag or config).

    Checks the runtime flag (set by ``--experimental``) first, then falls
    back to the ``experimental:`` key in ``config.yml``.
    """
    return _experimental or _load_validated().experimental


def set_experimental(value: bool) -> None:
    """Enable or disable experimental features globally."""
    global _experimental  # noqa: PLW0603
    _experimental = value


# ---------- Agent-specific config (agent: section) ----------


def _claude_agent_config() -> dict:
    """Return the ``agent.claude`` sub-dict, guarding against non-dict values."""
    claude = _load_validated().agent.get("claude")
    return claude if isinstance(claude, dict) else {}


def get_claude_allow_oauth() -> bool:
    """Return ``agent.claude.allow_oauth`` from global config (default False).

    When True (and experimental is enabled), the credential proxy handles
    Claude OAuth credentials normally.  The shield blocks
    ``api.anthropic.com`` to prevent phantom token leaks to Claude Code's
    hardcoded ``BASE_API_URL``.

    Global config (config.yml)::

        agent:
          claude:
            allow_oauth: true
    """
    return _claude_agent_config().get("allow_oauth", False) is True


def get_claude_expose_oauth_token() -> bool:
    """Return ``agent.claude.expose_oauth_token`` from global config (default False).

    When True (and experimental is enabled), the credential proxy is
    bypassed for Claude entirely.  The real ``.credentials.json`` in the
    shared mount is exposed to the container, and Claude Code manages its
    own token refresh.  Shield must allow ``api.anthropic.com`` and
    ``platform.claude.com``.

    Global config (config.yml)::

        agent:
          claude:
            expose_oauth_token: true
    """
    return _claude_agent_config().get("expose_oauth_token", False) is True


def is_claude_oauth_proxied() -> bool:
    """Return True when Claude OAuth is in tier 2 (proxy active, not exposed).

    Centralises the three-tier decision so callers don't duplicate the
    flag combination logic:

    - **Tier 1** (default): experimental off or allow_oauth off — skip Claude OAuth.
    - **Tier 2**: experimental + allow_oauth — proxy handles OAuth, shield denies
      ``api.anthropic.com``.
    - **Tier 3**: experimental + expose_oauth_token — proxy bypassed for Claude.
    """
    return is_experimental() and get_claude_allow_oauth() and not get_claude_expose_oauth_token()
