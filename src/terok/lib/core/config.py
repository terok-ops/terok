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
from terok_sandbox import ServicesMode

from ..util.yaml import YAMLError, load as _yaml_load
from .paths import config_root as _config_root_base
from .yaml_schema import RawGlobalConfig

__all_public_reexports__ = ("ServicesMode",)
"""Re-exported from :mod:`terok_sandbox.config_schema` — one SSOT for
the ``services.mode`` Literal; sandbox owns the schema, terok just
forwards the type so downstream callers can stay inside the terok
namespace if they prefer."""

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


def _config_layers() -> list[tuple[str, Path]]:
    """Ordered config layers for merging (lowest → highest priority).

    ``TEROK_CONFIG_FILE`` → single override (no layering).
    Otherwise: ``/etc/terok`` → ``sys.prefix/etc/terok`` → ``~/.config/terok``.
    """
    env_file = os.environ.get("TEROK_CONFIG_FILE")
    if env_file:
        return [("override", Path(env_file).expanduser().resolve())]

    etc_cfg = Path("/etc/terok/config.yml")
    sp_cfg = Path(sys.prefix) / "etc" / "terok" / "config.yml"
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    user_cfg = (Path(xdg_home) if xdg_home else Path.home() / ".config") / "terok" / "config.yml"

    layers: list[tuple[str, Path]] = [("system", etc_cfg)]
    if sp_cfg.resolve() != etc_cfg.resolve():
        layers.append(("prefix", sp_cfg))
    layers.append(("user", user_cfg))
    return layers


def global_config_search_paths() -> list[Path]:
    """Config file merge order (lowest → highest priority).

    When ``TEROK_CONFIG_FILE`` is set, returns only that single path.
    Otherwise: ``/etc/terok`` → ``sys.prefix/etc/terok`` → ``~/.config/terok``.
    """
    return [path for _, path in _config_layers()]


def global_config_path() -> Path:
    """User-editable global config file path.

    Returns the highest-priority writable config location — the file the
    user would edit to customise their setup.  Used for locating sibling
    files (e.g. ``instructions.md``) in the user's config directory.

    When ``TEROK_CONFIG_FILE`` is set, returns that path directly.
    Otherwise, returns ``~/.config/terok/config.yml`` (or XDG equivalent).
    """
    env_file = os.environ.get("TEROK_CONFIG_FILE")
    if env_file:
        return Path(env_file).expanduser().resolve()
    xdg_home = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg_home) if xdg_home else Path.home() / ".config") / "terok" / "config.yml"


# ---------- Global config (cached) ----------


_validated_config_cache: RawGlobalConfig | None = None
_raw_config_cache: dict[str, Any] | None = None


def _build_config_stack():
    """Build a :class:`ConfigStack` from all existing config layer files.

    Loads each layer independently; unreadable or malformed files are
    skipped with a stderr warning so the remaining layers still apply.
    """
    from terok_sandbox import ConfigScope, ConfigStack

    from ..util.logging_utils import warn_user

    stack = ConfigStack()
    for label, path in _config_layers():
        if not path.is_file():
            continue
        try:
            raw = _yaml_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError) as exc:
            warn_user("config", f"Cannot read {path}: {exc}. Skipping layer.")
            continue
        except YAMLError as exc:
            warn_user("config", f"Malformed YAML in {path}: {exc}. Skipping layer.")
            continue
        if not isinstance(raw, dict):
            warn_user(
                "config", f"{path}: expected mapping, got {type(raw).__name__}. Skipping layer."
            )
            continue
        stack.push(ConfigScope(label, path, raw))
    return stack


def _load_validated() -> RawGlobalConfig:
    """Merge all config layers, validate, and return a typed model (cached).

    Warnings are emitted once on first load; subsequent calls return the
    cached result without re-parsing or re-warning.
    """
    global _validated_config_cache  # noqa: PLW0603
    if _validated_config_cache is not None:
        return _validated_config_cache

    stack = _build_config_stack()
    merged = stack.resolve()
    if not merged:
        _validated_config_cache = RawGlobalConfig()
        return _validated_config_cache

    try:
        _validated_config_cache = RawGlobalConfig.model_validate(merged)
    except ValidationError as exc:
        from ..util.logging_utils import warn_user

        sources = ", ".join(str(s.source) for s in stack.scopes if s.data)
        field_errors = "; ".join(
            f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
        )
        warn_user("config", f"Invalid config ({sources}): {field_errors}. Using defaults.")
        _validated_config_cache = RawGlobalConfig()
    return _validated_config_cache


def load_global_config() -> dict[str, Any]:
    """Load and return the merged global terok configuration as a dict."""
    global _raw_config_cache  # noqa: PLW0603
    if _raw_config_cache is not None:
        return _raw_config_cache
    _raw_config_cache = _build_config_stack().resolve()
    return _raw_config_cache


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


def vault_dir() -> Path:
    """Return the base directory for the vault (token broker DB, SSH signer keys).

    Precedence:
    - ``TEROK_VAULT_DIR`` environment variable (``TEROK_CREDENTIALS_DIR``
      accepted as deprecated fallback).
    - Global config ``credentials.dir``.
    - Namespace root + ``vault/`` (honors ``paths.root``).
    """
    import warnings

    from terok_sandbox.paths import namespace_state_dir

    env = "TEROK_VAULT_DIR"
    if not os.environ.get(env) and os.environ.get("TEROK_CREDENTIALS_DIR"):
        warnings.warn(
            "TEROK_CREDENTIALS_DIR is deprecated; use TEROK_VAULT_DIR instead",
            DeprecationWarning,
            stacklevel=2,
        )
        env = "TEROK_CREDENTIALS_DIR"

    return _resolve_path(
        env,
        ("credentials", "dir"),
        lambda: namespace_state_dir("vault"),
    )


def make_sandbox_config() -> "SandboxConfig":  # noqa: F821 — forward ref
    """Construct a :class:`SandboxConfig` for sandbox operations.

    Bridges terok's config layer (env vars → config.yml → XDG defaults) to
    sandbox's plain dataclass.  Sandbox uses its own ``state_dir`` default
    (``~/.local/share/terok/sandbox/``) — terok no longer overrides it.

    Port fields pass ``None`` (auto-allocate via sandbox's shared port
    registry) or an explicit ``int`` from config.yml.  Resolution happens
    in ``SandboxConfig.__post_init__``.

    This is the **single source of truth** for config bridging — every
    SandboxConfig field that terok controls must be set here.
    """
    from terok_sandbox import SandboxConfig

    return SandboxConfig(
        vault_dir=vault_dir(),
        gate_port=get_gate_server_port(),
        token_broker_port=get_vault_token_broker_port(),
        ssh_signer_port=get_vault_ssh_signer_port(),
        shield_bypass=get_shield_bypass_firewall_no_protection(),
        shield_audit=get_shield_audit(),
        services_mode=get_services_mode(),
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


def get_global_image_agents() -> str:
    """Return ``image.agents`` from the global config (defaults to ``"all"``).

    The value is a comma-separated list of roster entries (or the literal
    string ``"all"``) that drives which agents are baked into L1 builds.
    Project YAML may override this via its own ``image.agents``.
    """
    return _load_validated().image.agents or "all"


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


def get_vault_bypass() -> bool:
    """Return whether the vault is globally bypassed.

    .. danger::

        When True, real API keys and OAuth tokens are mounted directly into
        task containers via shared config directories — the same behavior as
        before the vault was implemented.  Use only for debugging or
        environments where the vault cannot run.

    Global config (config.yml)::

        vault:
          bypass_no_secret_protection: true
    """
    return _load_validated().vault.bypass_no_secret_protection


def get_services_mode() -> ServicesMode:
    """Return the service transport mode (``"tcp"`` or ``"socket"``).

    Global config (config.yml)::

        services:
          mode: tcp   # or "socket"
    """
    return _load_validated().services.mode


def get_vault_transport() -> str:
    """Return the vault transport mode (``"direct"`` or ``"socket"``).

    Derived from ``services.mode``: ``socket`` → ``"socket"`` (containers
    read the mounted Unix socket), anything else → ``"direct"``
    (containers connect to the broker's TCP port).  One knob, two
    vocabularies — kept aligned so the listener and the container-side
    routing cannot disagree.
    """
    return "socket" if _load_validated().services.mode == "socket" else "direct"


def get_vault_token_broker_port() -> int | None:
    """Return the explicit vault token-broker port, or ``None`` for auto-allocation.

    Global config (config.yml)::

        vault:
          port: 18700   # omit for auto-allocation
    """
    return _load_validated().vault.port


def get_vault_ssh_signer_port() -> int | None:
    """Return the explicit vault SSH-signer port, or ``None`` for auto-allocation.

    Global config (config.yml)::

        vault:
          ssh_signer_port: 18701   # omit for auto-allocation
    """
    return _load_validated().vault.ssh_signer_port


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


def get_gate_server_port() -> int | None:
    """Return the explicit gate server port, or ``None`` for auto-allocation."""
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

    When True (and experimental is enabled), the vault handles
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

    When True (and experimental is enabled), the vault is
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
    """Return True when Claude OAuth traffic is routed through the proxy.

    Centralises the three-mode decision so callers don't duplicate the
    flag combination logic:

    - **Skipped** (default): experimental off or allow_oauth off — Claude
      OAuth bypasses the proxy entirely.
    - **Proxied**: experimental + allow_oauth — proxy handles OAuth auth,
      shield denies ``api.anthropic.com`` to prevent direct access.
    - **Exposed**: experimental + expose_oauth_token — real OAuth token
      mounted directly for Claude Code subscription features.
    """
    return is_experimental() and get_claude_allow_oauth() and not get_claude_expose_oauth_token()


def is_claude_oauth_exposed() -> bool:
    """Return True when the real Claude OAuth token is intentionally exposed.

    Exposed mode trades token security for Claude Code subscription
    features — the real ``.credentials.json`` is mounted directly
    instead of being replaced with a phantom marker.
    """
    return is_experimental() and get_claude_expose_oauth_token()


# ---------- Codex OAuth config (agent.codex.*) ----------


def _codex_agent_config() -> dict:
    """Return the ``agent.codex`` sub-dict, guarding against non-dict values."""
    codex = _load_validated().agent.get("codex")
    return codex if isinstance(codex, dict) else {}


def get_codex_allow_oauth() -> bool:
    """Return ``agent.codex.allow_oauth`` from global config (default False).

    When True (and experimental is enabled), the vault brokers Codex
    OAuth end-to-end: the in-container ``auth.json`` carries a phantom
    access/refresh token and the real id_token JWT; inference requests
    ride through ``OPENAI_BASE_URL`` to the vault socket where the
    phantom is swapped for the live bearer.  Shield denies
    ``api.openai.com`` to prevent accidental direct hits.

    Global config (config.yml)::

        agent:
          codex:
            allow_oauth: true
    """
    return _codex_agent_config().get("allow_oauth", False) is True


def get_codex_expose_oauth_token() -> bool:
    """Return ``agent.codex.expose_oauth_token`` from global config (default False).

    When True (and experimental is enabled), the real Codex ``auth.json``
    is copied into the shared mount so the in-container Codex CLI reads
    the live OAuth token directly.  The vault is bypassed for Codex.
    Shield must allow ``api.openai.com``.

    Global config (config.yml)::

        agent:
          codex:
            expose_oauth_token: true
    """
    return _codex_agent_config().get("expose_oauth_token", False) is True


def is_codex_oauth_proxied() -> bool:
    """Return True when Codex OAuth traffic is routed through the proxy.

    Kept in lockstep with :func:`is_claude_oauth_proxied` so shield
    rules and env overrides stay symmetrical.  The proxied path relies
    on the ``oauth_refresh`` block in ``codex.yaml`` for background
    token rotation and on the phantom ``auth.json`` written by
    :func:`~terok_executor.credentials.auth._codex_oauth_mount_writer`
    for in-container auth brokering.
    """
    return is_experimental() and get_codex_allow_oauth() and not get_codex_expose_oauth_token()


def is_codex_oauth_exposed() -> bool:
    """Return True when the real Codex OAuth token is intentionally exposed.

    Exposed mode trades token security for working Codex OAuth — the real
    ``auth.json`` is mounted into every task container instead of being
    wiped post-capture.  This is Phase 1's only path to a usable Codex.
    """
    return is_experimental() and get_codex_expose_oauth_token()
