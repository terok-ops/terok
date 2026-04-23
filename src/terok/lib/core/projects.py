# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project discovery, loading, and preset management."""

import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from terok_sandbox import ConfigScope, ConfigStack

from ..util.yaml import YAMLError, dump as _yaml_dump, load as _yaml_load
from .config import (
    build_dir,
    bundled_presets_dir,
    gate_repos_dir,
    get_global_default_agent,
    get_global_default_login,
    get_global_hooks,
    get_global_image_agents,
    get_global_section,
    get_shield_drop_on_task_run,
    get_shield_on_task_restart,
    projects_dir,
    sandbox_live_dir,
    user_presets_dir,
    user_projects_dir,
)
from .git_authorship import normalize_git_authorship
from .project_model import (  # noqa: F401 — re-exported public API
    PresetInfo,
    ProjectConfig,
    is_valid_project_id,
    validate_project_id,
)
from .yaml_schema import RawGlobalGitSection, RawProjectYaml

logger = logging.getLogger(__name__)

_PROJECT_YML = "project.yml"
_INSTRUCTIONS_MD = "instructions.md"


def _get_global_git_config(key: str) -> str | None:
    """Get a value from the user's global git config.

    Returns None if git is not available or the key is not set.
    """
    try:
        result = subprocess.run(
            ["git", "config", "--global", "--get", key], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
        return None
    except (FileNotFoundError, subprocess.SubprocessError):
        return None


def _git_global_identity() -> dict[str, str]:
    """Return human_name/human_email from global git config as a dict."""
    result: dict[str, str] = {}
    name = _get_global_git_config("user.name")
    if name:
        result["human_name"] = name
    email = _get_global_git_config("user.email")
    if email:
        result["human_email"] = email
    return result


def _resolve_subagent_files(subagents: list[dict[str, Any]] | None, base_dir: Path) -> None:
    """Resolve relative ``file`` paths in subagent entries against *base_dir*."""
    for sa in subagents or []:
        if not isinstance(sa, dict):
            continue
        raw_file = sa.get("file")
        if not isinstance(raw_file, str) or not raw_file.strip():
            continue
        file_path = Path(raw_file).expanduser()
        if not file_path.is_absolute():
            file_path = base_dir / file_path
        sa["file"] = str(file_path.resolve())


def _format_validation_error(exc: ValidationError, cfg_path: Path) -> str:
    """Format a Pydantic ValidationError into a user-friendly message."""
    lines = [f"Invalid {_PROJECT_YML} ({cfg_path}):"]
    for err in exc.errors():
        loc = " → ".join(str(p) for p in err["loc"])
        lines.append(f"  {loc}: {err['msg']}")
    return "\n".join(lines)


def _parse_project_yaml(cfg_path: Path) -> RawProjectYaml:
    """Parse and validate a project.yml file, returning a typed model.

    Any error reading or parsing the file — including internal crashes
    from the YAML library itself — is converted into a single
    ``SystemExit`` with the path embedded in the message.  The narrow
    catch list (OSError, UnicodeDecodeError, YAMLError) would let
    ruamel.yaml's own quirks (``IndexError`` on certain inputs,
    ``AttributeError`` mid-scan, etc.) escape and crash whatever
    called us — the TUI's project-list keypress handler was one such
    path.  ``discover_projects`` already treats ``SystemExit`` from
    this module as "broken project" and surfaces the entry in-UI, so
    the robust policy is: no matter what goes wrong per file, the app
    keeps running and the user sees a damaged project in the list.
    """
    try:
        raw = _yaml_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise SystemExit(f"Failed to read {cfg_path}: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — YAML parsers can raise anything; quarantine it
        raise SystemExit(f"Failed to read {cfg_path}: {type(exc).__name__}: {exc}") from exc
    try:
        return RawProjectYaml.model_validate(raw)
    except ValidationError as exc:
        raise SystemExit(_format_validation_error(exc, cfg_path)) from exc
    except Exception as exc:  # noqa: BLE001 — defensive against non-Validation pydantic surprises
        raise SystemExit(f"Failed to validate {cfg_path}: {type(exc).__name__}: {exc}") from exc


def _resolve_shield_config(raw: RawProjectYaml) -> tuple[bool, str]:
    """Resolve shield settings with project-overrides-global fallback."""
    drop = (
        raw.shield.drop_on_task_run
        if raw.shield.drop_on_task_run is not None
        else get_shield_drop_on_task_run()
    )
    restart = raw.shield.on_task_restart or get_shield_on_task_restart()
    return drop, restart


def _resolve_hooks(raw: RawProjectYaml) -> tuple[str | None, str | None, str | None, str | None]:
    """Merge project run.hooks over global hook defaults."""
    g_pre, g_post, g_ready, g_stop = get_global_hooks()
    h = raw.run.hooks
    return (
        h.pre_start or g_pre,
        h.post_start or g_post,
        h.post_ready or g_ready,
        h.post_stop or g_stop,
    )


def _build_project_config(
    raw: RawProjectYaml,
    identity: dict[str, str | None],
    root: Path,
    project_id: str,
) -> ProjectConfig:
    """Transform a validated raw YAML model + resolved identity into a flat ProjectConfig."""
    pid = raw.project.id or project_id
    validate_project_id(pid)
    sec = raw.project.security_class
    tasks_root = Path(raw.tasks.root or (sandbox_live_dir() / "tasks" / pid)).resolve()
    gate_path = Path(raw.gate.path or (gate_repos_dir() / f"{pid}.git")).resolve()

    # ``gatekeeping`` mode is defined *by* the gate enforcing human review,
    # so disabling the gate in that mode is an incoherent configuration.
    # Reject at load time with a pointer at the two coherent resolutions.
    if sec == "gatekeeping" and not raw.gate.enabled:
        raise SystemExit(
            f"Project {pid!r}: security_class 'gatekeeping' requires gate.enabled: true "
            "(gatekeeping *is* the gate-enforced mode).  Either set security_class: online "
            "to drop the gate, or set gate.enabled: true."
        )

    staging_root: Path | None = None
    if sec == "gatekeeping":
        staging_root = Path(raw.gatekeeping.staging_root or (build_dir() / pid)).resolve()

    match raw.shared_dir:
        case True:
            from ..util.host_cmd import SHARED_DIRNAME

            shared_dir: Path | None = tasks_root / SHARED_DIRNAME
        case str() as s:
            p = Path(s).expanduser()
            if not p.is_absolute():
                raise SystemExit(f"shared_dir must be an absolute path, got: {s!r}")
            shared_dir = p.resolve()
        case _:
            shared_dir = None

    agent_cfg = dict(raw.agent)
    _resolve_subagent_files(agent_cfg.get("subagents", []), root)

    shield_drop, shield_restart = _resolve_shield_config(raw)
    hook_pre, hook_post, hook_ready, hook_stop = _resolve_hooks(raw)

    return ProjectConfig(
        id=pid,
        security_class=sec,
        isolation=raw.project.isolation,
        # Normalise "" → None so downstream ``is None`` checks and truthy
        # checks agree — the wizard and the template both emit an empty
        # string for a no-upstream project, but the rest of the stack
        # treats None as the canonical "no upstream" sentinel.
        upstream_url=raw.git.upstream_url or None,
        default_branch=raw.git.default_branch or None,
        root=root.resolve(),
        tasks_root=tasks_root,
        gate_path=gate_path,
        gate_enabled=raw.gate.enabled,
        staging_root=staging_root,
        # ``RawSSHSection.use_personal`` defaults to ``None`` (unset) so future
        # layering with the global ``config.yml`` ssh section can distinguish
        # *unset* from *explicitly false* via ``model_dump(exclude_none=True)``.
        # PR 4 wires that layering; for now this just preserves the False default.
        ssh_use_personal=raw.ssh.use_personal or False,
        expose_external_remote=raw.gatekeeping.expose_external_remote,
        human_name=identity.get("human_name") or "Nobody",
        human_email=identity.get("human_email") or "nobody@localhost",
        git_authorship=normalize_git_authorship(identity.get("authorship")),
        upstream_polling_enabled=raw.gatekeeping.upstream_polling.enabled,
        upstream_polling_interval_minutes=raw.gatekeeping.upstream_polling.interval_minutes,
        auto_sync_enabled=raw.gatekeeping.auto_sync.enabled,
        auto_sync_branches=raw.gatekeeping.auto_sync.branches,
        default_agent=raw.default_agent or get_global_default_agent(),
        default_login=raw.default_login or get_global_default_login(),
        agent_config=agent_cfg,
        shutdown_timeout=raw.run.shutdown_timeout,
        memory_limit=raw.run.memory,
        cpu_limit=raw.run.cpus,
        nested_containers=raw.run.nested_containers,
        timezone=raw.run.timezone,
        task_name_categories=raw.tasks.name_categories,
        shield_drop_on_task_run=shield_drop,
        shield_on_task_restart=shield_restart,
        hook_pre_start=hook_pre,
        hook_post_start=hook_post,
        hook_post_ready=hook_ready,
        hook_post_stop=hook_stop,
        base_image=raw.image.base_image,
        family=raw.image.family,
        agents=raw.image.agents or get_global_image_agents(),
        snippet_inline=raw.image.user_snippet_inline,
        snippet_file=raw.image.user_snippet_file,
        shared_dir=shared_dir,
    )


def find_preset_path(project: ProjectConfig, preset_name: str) -> Path | None:
    """Return the path of a preset file, or ``None`` if not found.

    Search order: project presets → global presets → bundled presets.
    """
    for search_dir in (project.presets_dir, user_presets_dir(), bundled_presets_dir()):
        for ext in (".yml", ".yaml"):
            path = search_dir / f"{preset_name}{ext}"
            if path.is_file():
                return path
    return None


def list_presets(project_id: str) -> list[PresetInfo]:
    """Return sorted preset info for a project.

    Search tiers (higher priority overwrites lower):
    bundled (shipped with terok) → global (user-wide) → project.
    """
    project = load_project(project_id)

    seen: dict[str, PresetInfo] = {}
    # Higher-priority tiers overwrite lower ones
    for source, search_dir in [
        ("bundled", bundled_presets_dir()),
        ("global", user_presets_dir()),
        ("project", project.presets_dir),
    ]:
        if search_dir.is_dir():
            for p in search_dir.iterdir():
                if p.is_file() and p.suffix in (".yml", ".yaml"):
                    seen[p.stem] = PresetInfo(name=p.stem, source=source, path=p)
    return sorted(seen.values(), key=lambda info: info.name)


def load_preset(project_id: str, preset_name: str) -> tuple[dict[str, Any], Path]:
    """Load a preset file and return ``(data, path)``.

    Search order: project → global → bundled.
    Raises SystemExit if the preset is not found.
    """
    project = load_project(project_id)
    path = find_preset_path(project, preset_name)
    if path is None:
        available = list_presets(project_id)
        names = ", ".join(info.name for info in available)
        hint = f"  Available: {names}" if available else "  No presets found."
        raise SystemExit(f"Preset '{preset_name}' not found.\n{hint}")
    try:
        data = _yaml_load(path.read_text(encoding="utf-8")) or {}
    except YAMLError as exc:
        raise SystemExit(f"Failed to parse preset '{preset_name}' ({path}): {exc}")
    # Resolve subagent file: paths relative to the preset file's directory
    _resolve_subagent_files(data.get("subagents", []), path.parent)
    return data, path


def derive_project(source_id: str, new_id: str) -> Path:
    """Create a new project config that *shares infrastructure* with an existing one.

    The derived project points at the same git-gate mirror and the same SSH
    keypair as the source — only ``project.id`` and the ``agent:`` section
    differ.  This is the "sibling project" use case: rerun the same repo
    through a different image or agent without re-provisioning keys or
    re-cloning the mirror.  The source's ``instructions.md``, if present, is
    copied over so the derived project starts with the same user-provided
    guidance.

    Returns the new project's root directory.

    Raises SystemExit if the source project is not found or the target already exists.
    """
    validate_project_id(new_id)
    source = load_project(source_id)
    projects_root = user_projects_dir().resolve()
    target_root = (projects_root / new_id).resolve()

    # Guard against directory traversal (belt-and-suspenders with the regex above)
    if not target_root.is_relative_to(projects_root):
        raise SystemExit(f"Invalid project ID '{new_id}': path escapes projects directory")

    if target_root.exists():
        raise SystemExit(f"Project '{new_id}' already exists at {target_root}")

    source_cfg = _yaml_load((source.root / _PROJECT_YML).read_text(encoding="utf-8")) or {}

    source_cfg.setdefault("project", {})["id"] = new_id
    source_cfg.pop("agent", None)
    _pin_shared_infra(source_cfg, source)

    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / _PROJECT_YML).write_text(
        _yaml_dump(source_cfg),
        encoding="utf-8",
    )

    instructions_src = source.root / _INSTRUCTIONS_MD
    if instructions_src.is_file():
        shutil.copy2(instructions_src, target_root / _INSTRUCTIONS_MD)

    return target_root


def _pin_shared_infra(cfg: dict, source: ProjectConfig) -> None:
    """Pin *source*'s resolved gate path into *cfg*.

    SSH keys are shared through the vault DB (assignments table) — no
    filesystem-level pinning required.  The gate path stays explicit so a
    derived project lands on the same mirror as its source.
    """
    cfg.setdefault("gate", {})["path"] = str(source.gate_path)


def _find_project_root(project_id: str) -> Path:
    """Return the root directory for *project_id*, preferring user over system."""
    user_root = user_projects_dir() / project_id
    sys_root = projects_dir() / project_id
    if (user_root / _PROJECT_YML).is_file():
        return user_root
    if (sys_root / _PROJECT_YML).is_file():
        return sys_root
    raise SystemExit(f"Project '{project_id}' not found in {user_root} or {sys_root}")


# ---------- Project listing ----------


@dataclass(frozen=True)
class BrokenProject:
    """A project directory whose ``project.yml`` failed to load.

    Carries just enough context for the TUI to render a row and show the
    validation error in the details pane, without forcing callers to
    re-run the failing ``load_project`` to rediscover the message.
    """

    id: str
    config_path: Path
    error: str


def discover_projects() -> tuple[list[ProjectConfig], list[BrokenProject]]:
    """Load every project on disk, splitting successes from config-level failures.

    The broken list lets the TUI render damaged projects alongside healthy
    ones (issue #565) — silently hiding them turns "project vanished" into
    a mystery.  ``_parse_project_yaml`` wraps every config error (bad YAML,
    schema drift, filesystem issues) in ``SystemExit`` with a human-readable
    message; anything else propagates as a genuine bug.
    """
    paths_by_id = _discover_project_paths()
    valid: list[ProjectConfig] = []
    broken: list[BrokenProject] = []
    for pid in sorted(paths_by_id):
        try:
            valid.append(load_project(pid))
        except SystemExit as exc:
            msg = _sanitize_for_tty(str(exc))
            broken.append(BrokenProject(id=pid, config_path=paths_by_id[pid], error=msg))
    return valid, broken


def list_projects() -> list[ProjectConfig]:
    """Discover all projects (user + system), warning on broken configs.

    Thin wrapper over :func:`discover_projects` that preserves the existing
    stderr + logger diagnostics for CLI callers.  The TUI uses
    :func:`discover_projects` directly to render broken entries in-place.

    User projects override system ones with the same id.
    """
    valid, broken = discover_projects()
    for bp in broken:
        # Log records are one-line structured entries; a message carrying
        # embedded newlines would split across records and could be read
        # as injected log lines.  stderr print keeps newlines so pydantic's
        # multi-line validation output is readable on the console.
        logger.warning("Skipping broken project '%s': %s", bp.id, bp.error.replace("\n", "\\n"))
        print(f"warning: skipping broken project '{bp.id}': {bp.error}", file=sys.stderr)
    return valid


def _discover_project_paths() -> dict[str, Path]:
    """Map each on-disk project ID to its ``project.yml`` path.

    User scope wins over system scope for collisions — matches how
    :func:`load_project` resolves the effective config.  Returning the
    path alongside the ID lets :func:`discover_projects` carry the
    location forward to ``BrokenProject`` without re-walking.
    """
    paths: dict[str, Path] = {}
    for root in (user_projects_dir(), projects_dir()):
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir() or d.name in paths:
                continue
            yml = d / _PROJECT_YML
            if yml.is_file() and is_valid_project_id(d.name):
                paths[d.name] = yml
    return paths


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
"""C0/C1 control characters except TAB (``\t``) and LF (``\n``).

ANSI escape sequences start with ESC (``\x1b``) and are caught here.
Error messages from pydantic / YAMLError can include attacker-supplied
bytes from project config files; blanking these prevents log-spoofing
and terminal-escape injection when messages hit an interactive stderr.
"""


def _sanitize_for_tty(s: str) -> str:
    """Strip control/escape chars so attacker-supplied bytes can't spoof TTY output."""
    return _CONTROL_CHARS.sub("?", s)


def _validated_global_git_section() -> dict[str, Any]:
    """Return the global config ``git:`` section, validated through the schema.

    If the global config has type errors in the git section (e.g. ``human_name: 123``),
    they are caught here with a clear message rather than surfacing later as a confusing
    Pydantic error during ProjectConfig construction.
    """
    raw = get_global_section("git")
    if not raw:
        return {}
    try:
        return RawGlobalGitSection.model_validate(raw).model_dump(exclude_none=True)
    except ValidationError:
        logger.warning("Invalid git section in global config, ignoring", exc_info=True)
        return {}


def load_project(project_id: str) -> ProjectConfig:
    """Load and return a fully resolved :class:`ProjectConfig` from *project_id*."""
    root = _find_project_root(project_id)
    cfg_path = root / _PROJECT_YML
    if not cfg_path.is_file():
        raise SystemExit(f"Missing {_PROJECT_YML} in {root}")

    raw = _parse_project_yaml(cfg_path)

    # Git identity resolved via ConfigStack: git-global → terok-global → project.yml
    git_dict = raw.git.model_dump(exclude_none=True)
    identity_stack = ConfigStack()
    identity_stack.push(ConfigScope("git-global", None, _git_global_identity()))
    identity_stack.push(ConfigScope("terok-global", None, _validated_global_git_section()))
    identity_stack.push(ConfigScope("project", cfg_path, git_dict))
    identity = identity_stack.resolve()

    try:
        return _build_project_config(raw, identity, root, project_id)
    except ValidationError as exc:
        # Identity values come from merged sources (git config, global config,
        # project.yml).  Include provenance in the error so the user knows
        # where to look.
        sources = ", ".join(s.level for s in identity_stack.scopes if s.data)
        raise SystemExit(
            _format_validation_error(exc, cfg_path) + f"\n  (git identity merged from: {sources})"
        )
