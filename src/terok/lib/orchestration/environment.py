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

from terok_agent import collect_opencode_provider_env
from terok_sandbox import (
    ensure_server_reachable,
    get_gate_base_path,
    get_gate_server_port,
)

from ..core.config import get_envs_base_dir
from ..core.projects import ProjectConfig
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


def _build_shared_mounts() -> tuple[SharedMount, ...]:
    """Derive shared mounts from the agent registry.

    The YAML agent registry is the single source of truth for all shared
    mounts — auth dirs, OpenCode state dirs, and Toad config.  Each entry's
    ``auth:`` section and ``mounts:`` section contribute mount definitions,
    deduplicated by ``host_dir`` in the registry.
    """
    from terok_agent import get_registry

    return tuple(
        SharedMount(m.host_dir, m.host_dir, m.label, m.container_path)
        for m in get_registry().mounts
    )


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


def _gate_url(gate_repo: Path, port: int, token: str) -> str:
    """Build the ``http://`` URL for a gate repo served by ``terok-gate``.

    The token is embedded as the Basic Auth username in the URL so that git
    handles authentication natively.  Uses the repo directory name as the URL
    path — the gate server serves repos as direct children of its base path.

    Raises ``SystemExit`` if the repo is not a direct child of the gate base,
    since the gate server cannot serve repos from arbitrary locations.
    """
    gate_base = get_gate_base_path().resolve()
    if gate_repo.resolve().parent != gate_base:
        raise SystemExit(
            "Configured gate.path is not servable by terok-gate.\n"
            f"  Gate repo: {gate_repo}\n"
            f"  Gate base: {gate_base}\n"
            "Move the repo under the gate base directory, or adjust\n"
            "gate_server.base_path / paths.state_root in global config."
        )
    return f"http://{token}@host.containers.internal:{port}/{gate_repo.name}"


def _security_mode_env_and_volumes(
    project: ProjectConfig, ssh_host_dir: Path, task_id: str
) -> tuple[dict[str, str], list[str]]:
    """Return env vars and volumes for the project's security mode."""
    from terok_sandbox import create_token

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
        token = create_token(project.id, task_id)
        gate_url = _gate_url(gate_repo, port, token)
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
                token = create_token(project.id, task_id)
                gate_url = _gate_url(gate_repo, port, token)
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


# ---------- Credential proxy ----------


def _credential_proxy_env_and_volumes(
    project: ProjectConfig, task_id: str
) -> tuple[dict[str, str], list[str]]:
    """Return env vars and volumes for the credential proxy.

    Injects phantom API key env vars and base URL overrides pointing to
    the proxy socket, and mounts the socket into the container.

    Raises ``SystemExit`` if the proxy is not running — no silent fallback.
    The only way to skip the proxy is the explicit bypass flag
    ``credential_proxy.bypass_no_secret_protection`` in global config.
    """
    from ..core.config import get_credential_proxy_bypass

    if get_credential_proxy_bypass():
        return {}, []

    from terok_agent import get_registry
    from terok_sandbox import (
        CredentialDB,
        SandboxConfig,
        ensure_proxy_reachable,
        get_proxy_port,
    )

    cfg = SandboxConfig()
    ensure_proxy_reachable()

    registry = get_registry()
    proxy_routes = registry.proxy_routes

    db = CredentialDB(cfg.proxy_db_path)
    try:
        credential_set = "default"
        stored_providers = set(db.list_credentials(credential_set))
        routed = stored_providers & proxy_routes.keys()
        tokens = {
            name: db.create_proxy_token(project.id, task_id, credential_set, name)
            for name in routed
        }
    finally:
        db.close()

    port = get_proxy_port(cfg)
    proxy_base = f"http://host.containers.internal:{port}"
    env: dict[str, str] = {}

    for name, route in proxy_routes.items():
        if name not in routed:
            continue
        for env_var in route.phantom_env:
            env[env_var] = tokens[name]
        if route.base_url_env:
            env[route.base_url_env] = proxy_base
        # Override OpenCode base URL for proxied providers (the original
        # value from collect_opencode_provider_env points to the real upstream;
        # this override redirects through the proxy instead)
        oc_provider = registry.providers.get(name)
        if oc_provider and oc_provider.opencode_config:
            env[f"TEROK_OC_{name.upper()}_BASE_URL"] = f"{proxy_base}/v1"
        if name == "glab":
            env["GITLAB_API_HOST"] = f"host.containers.internal:{port}"
            env["API_PROTOCOL"] = "http"

    if routed:
        env["TEROK_PROXY_PORT"] = str(port)

    # Warn about real credential files in shared mounts that will be visible
    # to the container alongside proxy phantom tokens.
    from terok_agent import scan_leaked_credentials

    leaked = scan_leaked_credentials(cfg.effective_envs_dir)
    if leaked:
        import sys

        print("WARNING: Real credentials in shared mounts:", file=sys.stderr)
        for provider, path in leaked:
            print(f"  {provider}: {path}", file=sys.stderr)
        print(
            "Remove these files — containers should only see proxy tokens.",
            file=sys.stderr,
        )

    return env, []


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

    # Credential proxy: inject phantom tokens and base URL overrides
    proxy_env, proxy_volumes = _credential_proxy_env_and_volumes(project, task_id)
    env.update(proxy_env)
    volumes += proxy_volumes

    return env, volumes
