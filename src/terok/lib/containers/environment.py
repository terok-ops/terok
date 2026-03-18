# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
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

from ..containers.headless_providers import collect_opencode_provider_env
from ..core.config import get_envs_base_dir
from ..core.projects import ProjectConfig
from ..security.gate_server import ensure_server_reachable, get_gate_base_path, get_gate_server_port
from ..util.fs import ensure_dir_writable
from ..util.host_cmd import WORKSPACE_DANGEROUS_DIRNAME

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


_STATIC_SHARED_MOUNTS: tuple[SharedMount, ...] = (
    SharedMount("codex", "_codex-config", "Codex config", "/home/dev/.codex"),
    SharedMount("claude", "_claude-config", "Claude config", "/home/dev/.claude"),
    SharedMount("vibe", "_vibe-config", "Vibe config", "/home/dev/.vibe"),
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


def _build_shared_mounts() -> tuple[SharedMount, ...]:
    """Build complete shared mounts including dynamically generated OpenCode provider mounts."""
    from ..containers.headless_providers import HEADLESS_PROVIDERS

    dynamic = tuple(
        SharedMount(
            p.name,
            f"_{p.name}-config",
            f"{p.label} config",
            f"/home/dev/{p.opencode_config.config_dir}",
        )
        for p in HEADLESS_PROVIDERS.values()
        if p.opencode_config is not None
    )
    return _STATIC_SHARED_MOUNTS + dynamic


SHARED_MOUNTS: tuple[SharedMount, ...] = _build_shared_mounts()


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


# ---------- Git identity ----------


def resolve_git_identity(
    agent_name: str,
    agent_email: str,
    human_name: str,
    human_email: str,
    authorship: str = "agent-human",
) -> dict[str, str]:
    """Resolve ``GIT_AUTHOR_*`` and ``GIT_COMMITTER_*`` env vars.

    Mirrors the logic in ``terok-env-git-identity.sh`` so that the identity
    is baked into the container environment at launch time.  This makes
    git commits work for any code path — interactive CLI wrappers, ACP
    adapters launched by toad, and headless runs — without relying on
    shell functions that only run in login shells.

    The CLI wrapper functions still call ``_terok_apply_git_identity()``
    in subshells, which overrides these env vars per invocation.  That
    gives per-agent identity when multiple agents share a container.
    """
    match authorship:
        case "human-agent":
            author_name, author_email = human_name, human_email
            committer_name, committer_email = agent_name, agent_email
        case "agent":
            author_name, author_email = agent_name, agent_email
            committer_name, committer_email = agent_name, agent_email
        case "human":
            author_name, author_email = human_name, human_email
            committer_name, committer_email = human_name, human_email
        case _:  # agent-human (default)
            author_name, author_email = agent_name, agent_email
            committer_name, committer_email = human_name, human_email

    return {
        "GIT_AUTHOR_NAME": author_name,
        "GIT_AUTHOR_EMAIL": author_email,
        "GIT_COMMITTER_NAME": committer_name,
        "GIT_COMMITTER_EMAIL": committer_email,
    }


def apply_git_identity_env(
    env: dict[str, str],
    project: ProjectConfig,
    agent_name: str = "AI Agent",
    agent_email: str = "ai-agent@localhost",
) -> None:
    """Add ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` to a container env dict.

    Uses the project's authorship policy and human identity together with
    the given agent identity to resolve the four git env vars.
    """
    env.update(
        resolve_git_identity(
            agent_name=agent_name,
            agent_email=agent_email,
            human_name=project.human_name or "Nobody",
            human_email=project.human_email or "nobody@localhost",
            authorship=project.git_authorship,
        )
    )


# ---------- Main builder ----------


def build_task_env_and_volumes(project: ProjectConfig, task_id: str) -> tuple[dict, list[str]]:
    """Compose environment and volume mounts for a task container.

    - Mount per-task workspace subdir to /workspace (host-explorable).
    - Mount all shared config dirs from ``SHARED_MOUNTS`` (read-write).
    - Optionally mount per-project SSH config dir to /home/dev/.ssh (read-write).
    - Provide REPO_ROOT and git info for the init script.
    """
    task_dir = project.tasks_root / str(task_id)
    repo_dir = task_dir / WORKSPACE_DANGEROUS_DIRNAME
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

    # Add OpenCode provider environment variables
    env.update(collect_opencode_provider_env())

    volumes: list[str] = [f"{repo_dir}:/workspace:Z"]
    volumes += _shared_volume_mounts(config_dirs)

    sec_env, sec_volumes = _security_mode_env_and_volumes(project, ssh_host_dir, task_id)
    env.update(sec_env)
    volumes += sec_volumes

    return env, volumes
