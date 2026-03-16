# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container environment and volume assembly for task containers.

Translates project configuration and security mode into the environment
variables and volume mounts that ``podman run`` needs when launching a
task container.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..core.config import get_envs_base_dir
from ..core.projects import ProjectConfig
from ..security.gate_server import ensure_server_reachable, get_gate_base_path, get_gate_server_port
from ..util.fs import ensure_dir_writable

# ---------- Shared config directories ----------


@dataclass(frozen=True)
class SharedMount:
    """Describes a shared config directory mounted into every task container."""

    key: str
    """Lookup key (e.g. ``"codex"``)."""

    host_dir_suffix: str
    """Directory name under ``get_envs_base_dir()`` (e.g. ``"_codex-config"``)."""

    label: str
    """Human-readable label for writable-check messages (e.g. ``"Codex config"``)."""

    container_path: str
    """Mount point inside the container (e.g. ``"/home/dev/.codex"``)."""


SHARED_MOUNTS: tuple[SharedMount, ...] = (
    SharedMount("codex", "_codex-config", "Codex config", "/home/dev/.codex"),
    SharedMount("claude", "_claude-config", "Claude config", "/home/dev/.claude"),
    SharedMount("vibe", "_vibe-config", "Vibe config", "/home/dev/.vibe"),
    SharedMount("blablador", "_blablador-config", "Blablador config", "/home/dev/.blablador"),
    SharedMount(
        "opencode_config", "_opencode-config", "OpenCode config", "/home/dev/.config/opencode"
    ),
    SharedMount(
        "opencode_data", "_opencode-data", "OpenCode data", "/home/dev/.local/share/opencode"
    ),
    SharedMount("opencode_state", "_opencode-state", "OpenCode state", "/home/dev/.local/state"),
    SharedMount("toad", "_toad-config", "Toad config", "/home/dev/.config/toad"),
    SharedMount("gh", "_gh-config", "GitHub CLI config", "/home/dev/.config/gh"),
    SharedMount("glab", "_glab-config", "GitLab CLI config", "/home/dev/.config/glab-cli"),
)


def _ensure_shared_dirs(envs_base: Path) -> dict[str, Path]:
    """Ensure shared config directories exist and return key→host_path mapping."""
    dirs = {}
    for m in SHARED_MOUNTS:
        path = envs_base / m.host_dir_suffix
        ensure_dir_writable(path, m.label)
        dirs[m.key] = path
    return dirs


def _shared_volume_mounts(host_dirs: dict[str, Path]) -> list[str]:
    """Return volume mount strings for all shared config directories."""
    return [f"{host_dirs[m.key]}:{m.container_path}:z" for m in SHARED_MOUNTS]


def _gate_url(gate_repo: Path, gate_base: Path, port: int, project_id: str, token: str) -> str:
    """Build the ``http://`` URL for a gate repo served by ``terok-gate``.

    The token is embedded as the Basic Auth username in the URL so that git
    handles authentication natively.  Derives the path relative to the gate
    base directory so that custom ``gate.path`` settings produce correct URLs.
    Raises ``SystemExit`` if the gate repo is outside the configured gate base path.
    """
    try:
        rel = gate_repo.relative_to(gate_base).as_posix()
    except ValueError as exc:
        raise SystemExit(
            f"Gate repo for project '{project_id}' is outside gate base.\n"
            f"Repo: {gate_repo}\n"
            f"Gate base: {gate_base}\n"
            "Adjust gate.path or gate server base path so the repo is servable."
        ) from exc
    return f"http://{token}@host.containers.internal:{port}/{rel}"


def _security_mode_env_and_volumes(
    project: ProjectConfig, ssh_host_dir: Path, task_id: str
) -> tuple[dict[str, str], list[str]]:
    """Return env vars and volumes for the project's security mode."""
    from ..security.gate_tokens import create_token

    env: dict[str, str] = {}
    volumes: list[str] = []

    gate_repo = project.gate_path

    if project.security_class == "gatekeeping":
        if not gate_repo.exists():
            raise SystemExit(
                f"Git gate missing for project '{project.id}'.\n"
                f"Expected at: {gate_repo}\n"
                f"Run 'terokctl gate-sync {project.id}' to create/update the local mirror."
            )
        ensure_server_reachable()
        port = get_gate_server_port()
        gate_base = get_gate_base_path()
        token = create_token(project.id, task_id)
        gate_url = _gate_url(gate_repo, gate_base, port, project.id, token)
        env["CODE_REPO"] = gate_url
        if project.default_branch:
            env["GIT_BRANCH"] = project.default_branch
        if project.expose_external_remote and project.upstream_url:
            env["EXTERNAL_REMOTE_URL"] = project.upstream_url
        if project.ssh_mount_in_gatekeeping and ssh_host_dir.is_dir():
            ensure_dir_writable(ssh_host_dir, "SSH config")
            volumes.append(f"{ssh_host_dir}:/home/dev/.ssh:z")
    else:
        if gate_repo.exists():
            try:
                ensure_server_reachable()
            except SystemExit:
                pass  # gate server down; skip CLONE_FROM, fall back to upstream
            else:
                port = get_gate_server_port()
                gate_base = get_gate_base_path()
                token = create_token(project.id, task_id)
                gate_url = _gate_url(gate_repo, gate_base, port, project.id, token)
                env["CLONE_FROM"] = gate_url
        if project.upstream_url:
            env["CODE_REPO"] = project.upstream_url
            if project.default_branch:
                env["GIT_BRANCH"] = project.default_branch
        if project.ssh_mount_in_online and ssh_host_dir.is_dir():
            ensure_dir_writable(ssh_host_dir, "SSH config")
            volumes.append(f"{ssh_host_dir}:/home/dev/.ssh:z")

    return env, volumes


# ---------- Main builder ----------


def build_task_env_and_volumes(project: ProjectConfig, task_id: str) -> tuple[dict, list[str]]:
    """Compose environment and volume mounts for a task container.

    - Mount per-task workspace subdir to /workspace (host-explorable).
    - Mount all shared config dirs from ``SHARED_MOUNTS`` (read-write).
    - Optionally mount per-project SSH config dir to /home/dev/.ssh (read-write).
    - Provide REPO_ROOT and git info for the init script.
    """
    task_dir = project.tasks_root / str(task_id)
    repo_dir = task_dir / "workspace-dangerous"
    repo_dir.mkdir(parents=True, exist_ok=True)

    envs_base = get_envs_base_dir()
    config_dirs = _ensure_shared_dirs(envs_base)
    ssh_host_dir = project.ssh_host_dir or (envs_base / f"_ssh-config-{project.id}")

    env = {
        "PROJECT_ID": project.id,
        "TASK_ID": task_id,
        "REPO_ROOT": "/workspace",
        "GIT_RESET_MODE": os.environ.get("TEROK_GIT_RESET_MODE", "none"),
        "TEROK_GIT_AUTHORSHIP": project.git_authorship,
        "CLAUDE_CONFIG_DIR": "/home/dev/.claude",
        "HUMAN_GIT_NAME": project.human_name or "Nobody",
        "HUMAN_GIT_EMAIL": project.human_email or "nobody@localhost",
    }

    volumes: list[str] = [f"{repo_dir}:/workspace:Z"]
    volumes += _shared_volume_mounts(config_dirs)

    sec_env, sec_volumes = _security_mode_env_and_volumes(project, ssh_host_dir, task_id)
    env.update(sec_env)
    volumes += sec_volumes

    return env, volumes
