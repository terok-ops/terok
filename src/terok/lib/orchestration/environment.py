# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""Container environment and volume assembly for task containers.

Translates project configuration and security mode into the environment
variables and volume mounts that ``podman run`` needs when launching a
task container.  Shared config mounts and base env vars are delegated to
:func:`terok_executor.assemble_container_env`; this module adds terok-specific
concerns (gate server, credential proxy with OAuth/socket/SSH support).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from terok_sandbox import (
    VolumeSpec,
    create_token,
    ensure_server_reachable,
    get_gate_base_path,
    get_gate_server_port,
)

from ..core.config import make_sandbox_config, sandbox_live_mounts_dir
from ..core.projects import ProjectConfig
from ..util.host_cmd import WORKSPACE_DANGEROUS_DIRNAME

_logger = logging.getLogger(__name__)


def _gate_url(gate_repo: Path, gate_base: Path, port: int, token: str) -> str:
    """Build the ``http://`` URL for a gate repo served by ``terok-gate``.

    The token is embedded as the Basic Auth username in the URL so that git
    handles authentication natively.  Uses the repo directory name as the URL
    path — the gate server serves repos as direct children of its base path.

    Raises ``SystemExit`` if the repo is not a direct child of the gate base,
    since the gate server cannot serve repos from arbitrary locations.
    """
    if gate_repo.resolve().parent != gate_base.resolve():
        raise SystemExit(
            "Configured gate.path is not servable by terok-gate.\n"
            f"  Gate repo: {gate_repo}\n"
            f"  Gate base: {gate_base}\n"
            "Move the repo under the gate base directory, or adjust\n"
            "gate_server.repos_dir / paths.state_dir in global config."
        )
    return f"http://{token}@host.containers.internal:{port}/{gate_repo.name}"


def _security_mode_env_and_volumes(
    project: ProjectConfig, task_id: str
) -> tuple[dict[str, str], list[str]]:
    """Return env vars and volumes for the project's security mode."""
    cfg = make_sandbox_config()
    env: dict[str, str] = {}
    volumes: list[str] = []

    gate_repo = project.gate_path
    gate_base = get_gate_base_path(cfg)

    if project.security_class == "gatekeeping":
        if not gate_repo.exists():
            raise SystemExit(
                f"Git gate missing for project '{project.id}'.\n"
                f"Expected at: {gate_repo}\n"
                f"Run 'terok gate-sync {project.id}' to create/update the local mirror."
            )
        ensure_server_reachable(cfg)
        port = get_gate_server_port(cfg)
        token = create_token(project.id, task_id, cfg)
        gate_url = _gate_url(gate_repo, gate_base, port, token)
        env["CODE_REPO"] = gate_url
        if project.default_branch:
            env["GIT_BRANCH"] = project.default_branch
        if project.expose_external_remote and project.upstream_url:
            env["EXTERNAL_REMOTE_URL"] = project.upstream_url
    else:
        if gate_repo.exists():
            try:
                ensure_server_reachable(cfg)
            except SystemExit:
                from ..util.logging_utils import warn_user

                warn_user(
                    "gate",
                    "Gate server unreachable; cloning directly from upstream. "
                    "This is safe — online mode does not require the gate.",
                )
            else:
                port = get_gate_server_port(cfg)
                token = create_token(project.id, task_id, cfg)
                gate_url = _gate_url(gate_repo, gate_base, port, token)
                env["CLONE_FROM"] = gate_url
        if project.upstream_url:
            env["CODE_REPO"] = project.upstream_url
            if project.default_branch:
                env["GIT_BRANCH"] = project.default_branch

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


def ensure_credential_proxy() -> None:
    """Ensure the credential proxy is reachable (respecting the bypass flag).

    Call this before (re)starting a container that was created with proxy
    phantom tokens.  After a host reboot the systemd socket may be active
    but the service idle — this function brings the TCP ports up so
    containers can connect.

    No-op when the ``bypass_no_secret_protection`` flag is set.
    """
    from ..core.config import get_credential_proxy_bypass

    if get_credential_proxy_bypass():
        return

    from terok_sandbox import ProxyUnreachableError, ensure_proxy_reachable

    try:
        ensure_proxy_reachable(make_sandbox_config())
    except ProxyUnreachableError as exc:
        raise SystemExit(
            f"{exc}\n\n"
            "Start it with:\n"
            "  terok credential-proxy install   (systemd socket activation)\n"
            "  terok credential-proxy start     (manual daemon)"
        ) from exc


def _apply_claude_oauth_overrides(env: dict[str, str]) -> None:
    """Adjust Claude OAuth env vars based on the experimental proxy config.

    Executor handles all generic proxy plumbing (phantom tokens, transport,
    SSH agent).  This function only adjusts Claude-specific env vars:

    - **Proxied** (``is_claude_oauth_proxied``): remove phantom token, keep
      ``ANTHROPIC_BASE_URL`` — the container uses the mounted
      ``.credentials.json`` marker directly with the proxy.
    - **Skipped** (default): remove all Claude proxy env vars — Claude Code's
      hardcoded ``BASE_API_URL`` bypasses the proxy anyway.
    - **Exposed** (``expose_oauth_token``): also removes vars — the real
      OAuth token is mounted directly for Claude Code subscription features.
    """
    from ..core.config import is_claude_oauth_proxied

    # Only act when executor injected Claude OAuth vars
    if "CLAUDE_CODE_OAUTH_TOKEN" not in env:
        return

    if is_claude_oauth_proxied():
        # Proxied: remove phantom token (the mounted .credentials.json
        # marker is used for auth), keep ANTHROPIC_BASE_URL for routing
        env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    else:
        # Skipped or exposed: remove all Claude proxy env vars
        for key in ("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_UNIX_SOCKET"):
            env.pop(key, None)


def _warn_leaked_credentials() -> None:
    """Warn about real credential files in shared mounts.

    When the Claude OAuth token is intentionally exposed (for Claude Code
    subscription features), the Claude-specific warning is suppressed.
    """
    from terok_executor import scan_leaked_credentials

    from ..core.config import is_claude_oauth_exposed
    from ..util.ansi import bold, supports_color, yellow

    leaked = scan_leaked_credentials(sandbox_live_mounts_dir())

    if is_claude_oauth_exposed():
        import sys

        color = supports_color()
        print(
            "\n"
            + bold(
                yellow(
                    "  WARNING: Claude OAuth token is EXPOSED to all task containers.\n"
                    "  The credential proxy does NOT protect this token — it is mounted\n"
                    "  directly via .credentials.json in the shared config directory.\n"
                    "  Every task container managed by terok can read the real token.\n",
                    color,
                ),
                color,
            ),
            file=sys.stderr,
        )
        leaked = [(p, path) for p, path in leaked if p != "claude"]

    for provider, path in leaked:
        _logger.warning("Real credential in shared mount for provider %s", provider)
        _logger.debug("  path: %s", path)


# ---------- Clone-cache workspace seeding ----------


def _seed_workspace_cache(repo_dir: Path, project_id: str, code_repo: str | None) -> None:
    """Pre-populate *repo_dir* from the clone cache (best-effort).

    Only acts when the workspace has a ``.new-task-marker`` (new task)
    and no existing ``.git``.  Failures are logged and swallowed — the
    container falls back to a full ``git clone``.
    """
    if (repo_dir / ".git").is_dir() or not (repo_dir / ".new-task-marker").is_file():
        return

    try:
        from terok_executor import seed_workspace_from_clone_cache
    except ImportError:
        return

    try:
        seed_workspace_from_clone_cache(
            repo_dir, project_id, origin_url=code_repo, cfg=make_sandbox_config()
        )
    except Exception:
        _logger.warning(
            "seed_workspace_from_clone_cache failed for project %s at %s",
            project_id,
            repo_dir,
            exc_info=True,
        )


# ---------- Main builder ----------


def build_task_env_and_volumes(
    project: ProjectConfig, task_id: str
) -> tuple[dict, list[VolumeSpec]]:
    """Compose environment and volume mounts for a task container.

    Delegates shared config mounts, base env vars, workspace volume, git
    identity, and OpenCode provider env to
    :func:`terok_executor.assemble_container_env`, then layers terok-specific
    concerns: ``PROJECT_ID``, gate server URLs, and the full credential
    proxy (OAuth, socket transport, SSH agent).

    In **sealed** isolation mode (``project.is_sealed``), volumes are
    injected via ``podman cp`` instead of bind mounts — the sandbox
    handles this transparently when ``RunSpec.sealed`` is set.  The
    workspace is still created and cache-seeded on the host so the
    container benefits from fast startup in both modes.
    """
    sealed = project.is_sealed

    task_dir = project.tasks_root / str(task_id)
    task_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = task_dir / WORKSPACE_DANGEROUS_DIRNAME
    repo_dir.mkdir(exist_ok=True)

    # Pre-resolve gate server URLs → CODE_REPO / CLONE_FROM / GIT_BRANCH
    sec_env, _sec_volumes = _security_mode_env_and_volumes(project, task_id)

    # Seed workspace from clone cache (fast-start optimisation).
    # Only for new tasks (marker present, no .git yet).  The in-container
    # init script then does fetch+reset instead of a full git clone.
    # In sealed mode the seeded dir is podman-cp'd into the container.
    _seed_workspace_cache(repo_dir, project.id, sec_env.get("CODE_REPO"))

    # Pre-resolve git identity using terok's authorship logic so the
    # container has correct GIT_AUTHOR_*/GIT_COMMITTER_* from launch.
    identity = resolve_git_identity(
        agent_name="AI Agent",
        agent_email="ai-agent@localhost",
        human_name=project.human_name or "Nobody",
        human_email=project.human_email or "nobody@localhost",
        authorship=project.git_authorship,
    )

    from terok_executor import ContainerEnvSpec, assemble_container_env, get_roster

    from ..core.config import get_credential_proxy_bypass, get_credential_proxy_transport

    # Proxy: bypass → no proxy at all; otherwise ensure it's up before assembly
    proxy_bypass = get_credential_proxy_bypass()
    if not proxy_bypass:
        ensure_credential_proxy()

    result = assemble_container_env(
        ContainerEnvSpec(
            task_id=task_id,
            provider_name=project.default_agent or "claude",
            workspace_host_path=repo_dir,
            code_repo=sec_env.get("CODE_REPO"),
            clone_from=sec_env.get("CLONE_FROM"),
            branch=sec_env.get("GIT_BRANCH"),
            git_author_name=identity["GIT_AUTHOR_NAME"],
            git_author_email=identity["GIT_AUTHOR_EMAIL"],
            git_committer_name=identity["GIT_COMMITTER_NAME"],
            git_committer_email=identity["GIT_COMMITTER_EMAIL"],
            authorship=project.git_authorship,
            human_name=project.human_name or "Nobody",
            human_email=project.human_email or "nobody@localhost",
            credential_scope=project.id,
            proxy_transport=get_credential_proxy_transport(),
            proxy_required=not proxy_bypass,
            unrestricted=False,  # task_runners resolves per-provider config
            shared_dir=None if sealed else project.shared_dir,
            envs_dir=sandbox_live_mounts_dir(),
        ),
        get_roster(),
        # bypass → skip proxy entirely (no tokens, no check)
        caller_manages_proxy=proxy_bypass,
    )

    env = dict(result.env)
    volumes: list[VolumeSpec] = list(result.volumes)

    # terok-specific env vars not covered by the shared assembly
    env["PROJECT_ID"] = project.id
    env["GIT_RESET_MODE"] = os.environ.get("TEROK_GIT_RESET_MODE", "none")
    if "EXTERNAL_REMOTE_URL" in sec_env:
        env["EXTERNAL_REMOTE_URL"] = sec_env["EXTERNAL_REMOTE_URL"]

    # Claude OAuth overrides + leaked-cred scan with exposed-token filtering
    if not proxy_bypass:
        _apply_claude_oauth_overrides(env)
        _warn_leaked_credentials()

    return env, volumes
