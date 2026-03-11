# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project discovery, loading, and preset management."""

import logging
import subprocess
from pathlib import Path
from typing import Any

import yaml  # pip install pyyaml

from ..util.config_stack import ConfigScope, ConfigStack
from .config import (
    build_root,
    bundled_presets_dir,
    config_root,
    get_global_default_agent,
    get_global_section,
    global_presets_dir,
    state_root,
    user_projects_root,
)
from .git_authorship import normalize_git_authorship
from .project_model import (  # noqa: F401 — re-exported public API
    PresetInfo,
    ProjectConfig,
    effective_ssh_key_name,
    validate_project_id,
)

logger = logging.getLogger(__name__)


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


def find_preset_path(project: ProjectConfig, preset_name: str) -> Path | None:
    """Return the path of a preset file, or ``None`` if not found.

    Search order: project presets → global presets → bundled presets.
    """
    for search_dir in (project.presets_dir, global_presets_dir(), bundled_presets_dir()):
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
        ("global", global_presets_dir()),
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
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
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
    projects_root = user_projects_root().resolve()
    target_root = (projects_root / new_id).resolve()

    # Guard against directory traversal (belt-and-suspenders with the regex above)
    if not target_root.is_relative_to(projects_root):
        raise SystemExit(f"Invalid project ID '{new_id}': path escapes projects directory")

    if target_root.exists():
        raise SystemExit(f"Project '{new_id}' already exists at {target_root}")

    # Read and re-serialise via safe_load/safe_dump (comments are not preserved)
    source_cfg = yaml.safe_load((source.root / "project.yml").read_text(encoding="utf-8")) or {}

    # Update project ID
    if "project" not in source_cfg:
        source_cfg["project"] = {}
    source_cfg["project"]["id"] = new_id

    # Clear agent section for customization
    source_cfg.pop("agent", None)

    target_root.mkdir(parents=True, exist_ok=True)
    (target_root / "project.yml").write_text(
        yaml.safe_dump(source_cfg, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )

    return target_root


def _find_project_root(project_id: str) -> Path:
    """Return the root directory for *project_id*, preferring user over system."""
    user_root = user_projects_root() / project_id
    sys_root = config_root() / project_id
    if (user_root / "project.yml").is_file():
        return user_root
    if (sys_root / "project.yml").is_file():
        return sys_root
    raise SystemExit(f"Project '{project_id}' not found in {user_root} or {sys_root}")


# ---------- Project listing ----------


def list_projects() -> list[ProjectConfig]:
    """Discover all projects (user + system) and return them as ProjectConfig objects.

    User projects override system ones with the same id.
    """
    ids: set[str] = set()

    # Collect IDs from user and system project dirs
    for root in (user_projects_root(), config_root()):
        if not root.is_dir():
            continue
        for d in root.iterdir():
            if not d.is_dir():
                continue
            if (d / "project.yml").is_file():
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


def load_project(project_id: str) -> ProjectConfig:
    """Load and return a fully resolved :class:`ProjectConfig` from *project_id*."""
    root = _find_project_root(project_id)
    cfg_path = root / "project.yml"
    if not cfg_path.is_file():
        raise SystemExit(f"Missing project.yml in {root}")
    try:
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SystemExit(f"Failed to parse {cfg_path}: {exc}")

    proj_cfg = cfg.get("project", {}) or {}
    git_cfg = cfg.get("git", {}) or {}
    ssh_cfg = cfg.get("ssh", {}) or {}
    tasks_cfg = cfg.get("tasks", {}) or {}
    gate_path_cfg = cfg.get("gate", {}) or {}
    gate_cfg = cfg.get("gatekeeping", {}) or {}

    pid = proj_cfg.get("id", project_id)
    sec = proj_cfg.get("security_class", "online")

    sr = state_root()
    tasks_root = Path(tasks_cfg.get("root", sr / "tasks" / pid)).resolve()
    gate_path = Path(gate_path_cfg.get("path", sr / "gate" / f"{pid}.git")).resolve()

    staging_root: Path | None = None
    if sec == "gatekeeping":
        # Default to build_root unless explicitly configured in project.yml
        staging_root = Path(gate_cfg.get("staging_root", build_root() / pid)).resolve()

    upstream_url = git_cfg.get("upstream_url")
    default_branch = git_cfg.get("default_branch") or None

    ssh_key_name = ssh_cfg.get("key_name")
    ssh_host_dir = (
        Path(ssh_cfg.get("host_dir")).expanduser().resolve() if ssh_cfg.get("host_dir") else None
    )

    # Optional: ssh.config_template (path to a template file). If relative, it's relative to the project root.
    ssh_cfg_template_path: Path | None = None
    if ssh_cfg.get("config_template"):
        cfg_t = Path(str(ssh_cfg.get("config_template")))
        if not cfg_t.is_absolute():
            cfg_t = root / cfg_t
        ssh_cfg_template_path = cfg_t.expanduser().resolve()

    # Optional flag: ssh.mount_in_online (default true)
    ssh_mount_in_online = bool(ssh_cfg.get("mount_in_online", True))
    # Optional flag: ssh.mount_in_gatekeeping (default false)
    ssh_mount_in_gatekeeping = bool(ssh_cfg.get("mount_in_gatekeeping", False))
    # Optional flag: gatekeeping.expose_external_remote (default false)
    # When true, passes the upstream URL to the container as "external" remote
    expose_external_remote = bool(gate_cfg.get("expose_external_remote", False))

    # Human credentials for git committer (while AI is the author)
    # Resolved via ConfigStack: git-global → terok-global → project.yml
    identity_stack = ConfigStack()
    identity_stack.push(ConfigScope("git-global", None, _git_global_identity()))
    identity_stack.push(ConfigScope("terok-global", None, get_global_section("git")))
    identity_stack.push(ConfigScope("project", cfg_path, git_cfg))
    identity = identity_stack.resolve()

    human_name = identity.get("human_name") or "Nobody"
    human_email = identity.get("human_email") or "nobody@localhost"
    git_authorship = normalize_git_authorship(identity.get("authorship"))

    # Upstream polling configuration
    polling_cfg = gate_cfg.get("upstream_polling", {}) or {}
    upstream_polling_enabled = bool(polling_cfg.get("enabled", True))
    upstream_polling_interval_minutes = int(polling_cfg.get("interval_minutes", 5))

    # Auto-sync configuration
    sync_cfg = gate_cfg.get("auto_sync", {}) or {}
    auto_sync_enabled = bool(sync_cfg.get("enabled", False))
    auto_sync_branches = list(sync_cfg.get("branches", []))

    # Default agent preference (for Web UI and potentially CLI)
    # Precedence: 1) project.yml default_agent, 2) global terokctl config, 3) None (use default)
    default_agent = cfg.get("default_agent")
    if not default_agent:
        default_agent = get_global_default_agent()

    # Run section (GPU, shutdown timeout, etc.)
    run_cfg = cfg.get("run", {}) or {}
    shutdown_timeout = int(run_cfg.get("shutdown_timeout", 10))

    # Task name categories (from tasks.name_categories in project.yml)
    raw_cats = tasks_cfg.get("name_categories")
    task_name_categories: list[str] | None = None
    if isinstance(raw_cats, list) and raw_cats:
        task_name_categories = [str(c) for c in raw_cats]
    elif isinstance(raw_cats, str) and raw_cats.strip():
        task_name_categories = [raw_cats.strip()]

    # Agent config section (model, subagents, mcp_servers, etc.)
    agent_cfg = cfg.get("agent", {}) or {}
    # Resolve subagent file: paths relative to project root
    _resolve_subagent_files(agent_cfg.get("subagents", []), root)

    p = ProjectConfig(
        id=pid,
        security_class=sec,
        upstream_url=upstream_url,
        default_branch=default_branch,
        root=root.resolve(),
        tasks_root=tasks_root,
        gate_path=gate_path,
        staging_root=staging_root,
        ssh_key_name=ssh_key_name,
        ssh_host_dir=ssh_host_dir,
        ssh_config_template=ssh_cfg_template_path,
        ssh_mount_in_online=ssh_mount_in_online,
        ssh_mount_in_gatekeeping=ssh_mount_in_gatekeeping,
        expose_external_remote=expose_external_remote,
        human_name=human_name,
        human_email=human_email,
        git_authorship=git_authorship,
        upstream_polling_enabled=upstream_polling_enabled,
        upstream_polling_interval_minutes=upstream_polling_interval_minutes,
        auto_sync_enabled=auto_sync_enabled,
        auto_sync_branches=auto_sync_branches,
        default_agent=default_agent,
        agent_config=agent_cfg,
        shutdown_timeout=shutdown_timeout,
        task_name_categories=task_name_categories,
    )
    return p
