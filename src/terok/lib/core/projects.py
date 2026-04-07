# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project discovery, loading, and preset management."""

import logging
import subprocess
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from terok_agent import ConfigScope, ConfigStack

from ..util.yaml import YAMLError, dump as _yaml_dump, load as _yaml_load
from .config import (
    build_dir,
    bundled_presets_dir,
    gate_repos_dir,
    get_global_default_agent,
    get_global_default_login,
    get_global_hooks,
    get_global_section,
    get_shield_drop_on_task_run,
    get_shield_on_task_restart,
    projects_dir,
    state_dir,
    user_presets_dir,
    user_projects_dir,
)
from .git_authorship import normalize_git_authorship
from .project_model import (  # noqa: F401 — re-exported public API
    PresetInfo,
    ProjectConfig,
    effective_ssh_key_name,
    validate_project_id,
)
from .yaml_schema import RawGlobalGitSection, RawProjectYaml

logger = logging.getLogger(__name__)

_PROJECT_YML = "project.yml"


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
    """Parse and validate a project.yml file, returning a typed model."""
    try:
        raw = _yaml_load(cfg_path.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeDecodeError, YAMLError) as exc:
        raise SystemExit(f"Failed to read {cfg_path}: {exc}")
    try:
        return RawProjectYaml.model_validate(raw)
    except ValidationError as exc:
        raise SystemExit(_format_validation_error(exc, cfg_path))


def _resolve_ssh_template(raw_template: str | None, root: Path) -> Path | None:
    """Resolve an SSH config_template path relative to the project root."""
    if not raw_template:
        return None
    p = Path(raw_template).expanduser()
    if not p.is_absolute():
        p = root / p
    return p.resolve()


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
    sec = raw.project.security_class
    sr = state_dir()

    tasks_root = Path(raw.tasks.root or (sr / "tasks" / pid)).resolve()
    gate_path = Path(raw.gate.path or (gate_repos_dir() / f"{pid}.git")).resolve()

    staging_root: Path | None = None
    if sec == "gatekeeping":
        staging_root = Path(raw.gatekeeping.staging_root or (build_dir() / pid)).resolve()

    ssh_host_dir = Path(raw.ssh.host_dir).expanduser().resolve() if raw.ssh.host_dir else None

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
        upstream_url=raw.git.upstream_url,
        default_branch=raw.git.default_branch or None,
        root=root.resolve(),
        tasks_root=tasks_root,
        gate_path=gate_path,
        staging_root=staging_root,
        ssh_key_name=raw.ssh.key_name,
        ssh_host_dir=ssh_host_dir,
        ssh_config_template=_resolve_ssh_template(raw.ssh.config_template, root),
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
        task_name_categories=raw.tasks.name_categories,
        shield_drop_on_task_run=shield_drop,
        shield_on_task_restart=shield_restart,
        hook_pre_start=hook_pre,
        hook_post_start=hook_post,
        hook_post_ready=hook_ready,
        hook_post_stop=hook_stop,
        docker_base_image=raw.docker.base_image,
        docker_snippet_inline=raw.docker.user_snippet_inline,
        docker_snippet_file=raw.docker.user_snippet_file,
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
    """Create a new project config derived from an existing one.

    Copies the source ``project.yml``, preserving ``git``, ``ssh``, and ``gate``
    sections while resetting ``project.id`` and clearing the ``agent:`` section
    for customization.  Returns the new project root directory.

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

    # Update project ID
    if "project" not in source_cfg:
        source_cfg["project"] = {}
    source_cfg["project"]["id"] = new_id

    # Clear agent section for customization
    source_cfg.pop("agent", None)

    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / _PROJECT_YML).write_text(
        _yaml_dump(source_cfg),
        encoding="utf-8",
    )

    return target_root


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


def list_projects() -> list[ProjectConfig]:
    """Discover all projects (user + system) and return them as ProjectConfig objects.

    User projects override system ones with the same id.
    """
    ids: set[str] = set()

    # Collect IDs from user and system project dirs
    for root in (user_projects_dir(), projects_dir()):
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir():
                continue
            if (d / _PROJECT_YML).is_file():
                ids.add(d.name)

    projects: list[ProjectConfig] = []
    for pid in sorted(ids):
        # load_project will automatically prefer user over system config
        try:
            projects.append(load_project(pid))
        except (SystemExit, Exception):
            # if a project is broken (malformed YAML, missing fields, etc.),
            # skip it rather than crashing the listing or the TUI
            logger.debug("Skipping broken project '%s'", pid, exc_info=True)
            continue
    return projects


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
