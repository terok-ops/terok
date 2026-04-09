# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""Container environment and volume assembly for task containers.

Translates project configuration and security mode into the environment
variables and volume mounts that ``podman run`` needs when launching a
task container.  Shared config mounts and base env vars are delegated to
:func:`terok_agent.assemble_container_env`; this module adds terok-specific
concerns (gate server, credential proxy with OAuth/socket/SSH support).
"""

from __future__ import annotations

import os
from pathlib import Path

from terok_sandbox import (
    create_token,
    ensure_server_reachable,
    get_gate_base_path,
    get_gate_server_port,
)

from ..core.config import make_sandbox_config, sandbox_live_mounts_dir
from ..core.projects import ProjectConfig
from ..util.host_cmd import WORKSPACE_DANGEROUS_DIRNAME


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


# ---------- SSH keys JSON ----------


def _load_ssh_keys_json(path: Path) -> dict[str, list[dict[str, str]]]:
    """Load the SSH key mapping JSON.  Returns empty dict if missing or malformed."""
    import json

    from ..util.logging_utils import warn_user

    if not path.is_file():
        return {}
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError as exc:
        warn_user("ssh", f"Malformed SSH keys file {path}: {exc}. SSH key injection disabled.")
        return {}
    except (OSError, UnicodeDecodeError) as exc:
        warn_user("ssh", f"Cannot read SSH keys file {path}: {exc}. SSH key injection disabled.")
        return {}


# ---------- Credential proxy ----------


def _credential_type(cred: dict) -> str:
    """Return the credential type from the stored ``type`` field."""
    return cred.get("type") or "api_key"


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

    from terok_sandbox import ensure_proxy_reachable

    ensure_proxy_reachable(make_sandbox_config())


def _credential_proxy_env_and_volumes(
    project: ProjectConfig, task_id: str
) -> tuple[dict[str, str], list[str]]:
    """Return env vars and volumes for the credential proxy.

    Injects phantom token env vars and transport overrides (HTTP base URL or
    Unix socket path) pointing to the proxy.  The choice of env vars depends
    on two orthogonal dimensions:

    - **Auth**: stored credential type (``api_key`` → :attr:`phantom_env`,
      ``oauth`` → :attr:`oauth_phantom_env`).
    - **Transport**: global config ``credential_proxy.transport``
      (``direct`` → :attr:`base_url_env`, ``socket`` → :attr:`socket_env`).

    Raises ``SystemExit`` if the proxy is not running — no silent fallback.
    The only way to skip the proxy is the explicit bypass flag
    ``credential_proxy.bypass_no_secret_protection`` in global config.
    """
    from ..core.config import get_credential_proxy_bypass, get_credential_proxy_transport

    if get_credential_proxy_bypass():
        return {}, []

    from terok_agent import get_roster
    from terok_sandbox import (
        CredentialDB,
        ensure_proxy_reachable,
        get_proxy_port,
        get_ssh_agent_port,
    )

    cfg = make_sandbox_config()
    ensure_proxy_reachable(cfg)

    roster = get_roster()
    proxy_routes = roster.proxy_routes
    use_socket = get_credential_proxy_transport() == "socket"

    db = CredentialDB(cfg.proxy_db_path)
    try:
        credential_set = "default"
        stored_providers = set(db.list_credentials(credential_set))
        routed = stored_providers & proxy_routes.keys()
        tokens: dict[str, str] = {}
        credential_types: dict[str, str] = {}
        for name in routed:
            cred = db.load_credential(credential_set, name)
            ctype = _credential_type(cred) if cred else "api_key"
            credential_types[name] = ctype
            # Claude OAuth: the static marker in .credentials.json serves as
            # the access token — no per-task phantom token needed.  The proxy
            # accepts that marker directly (see credential_proxy.constants).
            if name == "claude" and ctype == "oauth":
                continue
            tokens[name] = db.create_proxy_token(project.id, task_id, credential_set, name)

        # SSH agent: create phantom token if project has at least one valid key registered
        ssh_keys = _load_ssh_keys_json(cfg.ssh_keys_json_path)
        ssh_entry = ssh_keys.get(project.id)
        if isinstance(ssh_entry, list) and any(
            e.get("private_key") and e.get("public_key") for e in ssh_entry
        ):
            ssh_token = db.create_proxy_token(project.id, task_id, project.id, "ssh")
        else:
            ssh_token = None
    finally:
        db.close()

    port = get_proxy_port(cfg)
    proxy_base = f"http://host.containers.internal:{port}"
    env: dict[str, str] = {}

    for name, route in proxy_routes.items():
        if name not in routed:
            continue

        is_oauth = credential_types[name] == "oauth"

        # Claude OAuth: don't inject CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_UNIX_SOCKET.
        # Claude Code shows "Claude API" when the token source is the env var;
        # subscription mode requires the token to come from .credentials.json.
        # The static marker in that file is accepted by the proxy directly.
        if name == "claude" and is_oauth:
            if route.base_url_env:
                env[route.base_url_env] = proxy_base
            continue

        # Auth dimension: select phantom env vars by credential type.
        # Providers with oauth_phantom_env get OAuth-specific env vars;
        # others fall back to phantom_env (same env var for both auth types).
        token_vars = (
            route.oauth_phantom_env if (is_oauth and route.oauth_phantom_env) else route.phantom_env
        )
        for env_var in token_vars:
            env[env_var] = tokens[name]

        # Transport dimension: socket flag + HTTP base URL.
        if use_socket and route.socket_path and route.socket_env:
            env[route.socket_env] = route.socket_path
        if route.base_url_env:
            env[route.base_url_env] = proxy_base

        # Override OpenCode base URL for proxied providers (the original
        # value from collect_opencode_provider_env points to the real upstream;
        # this override redirects through the proxy instead)
        oc_provider = roster.providers.get(name)
        if oc_provider and oc_provider.opencode_config:
            env[f"TEROK_OC_{name.upper()}_BASE_URL"] = f"{proxy_base}/v1"
        if name == "glab":
            env["GITLAB_API_HOST"] = f"host.containers.internal:{port}"
            env["API_PROTOCOL"] = "http"

    if routed:
        env["TEROK_PROXY_PORT"] = str(port)

    if ssh_token:
        env["TEROK_SSH_AGENT_TOKEN"] = ssh_token
        env["TEROK_SSH_AGENT_PORT"] = str(get_ssh_agent_port(cfg))

    # Warn about real credential files in shared mounts that will be visible
    # to the container alongside proxy phantom tokens.
    from terok_agent import scan_leaked_credentials

    leaked = scan_leaked_credentials(sandbox_live_mounts_dir())
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

    Delegates shared config mounts, base env vars, workspace volume, git
    identity, and OpenCode provider env to
    :func:`terok_agent.assemble_container_env`, then layers terok-specific
    concerns: ``PROJECT_ID``, gate server URLs, and the full credential
    proxy (OAuth, socket transport, SSH agent).
    """
    task_dir = project.tasks_root / str(task_id)
    repo_dir = task_dir / WORKSPACE_DANGEROUS_DIRNAME
    repo_dir.mkdir(parents=True, exist_ok=True)

    # Pre-resolve gate server URLs → CODE_REPO / CLONE_FROM / GIT_BRANCH
    sec_env, _sec_volumes = _security_mode_env_and_volumes(project, task_id)

    # Pre-resolve git identity using terok's authorship logic so the
    # container has correct GIT_AUTHOR_*/GIT_COMMITTER_* from launch.
    identity = resolve_git_identity(
        agent_name="AI Agent",
        agent_email="ai-agent@localhost",
        human_name=project.human_name or "Nobody",
        human_email=project.human_email or "nobody@localhost",
        authorship=project.git_authorship,
    )

    from terok_agent import ContainerEnvSpec, assemble_container_env, get_roster

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
            unrestricted=False,  # task_runners resolves per-provider config
            shared_dir=project.shared_dir,
            envs_dir=sandbox_live_mounts_dir(),
        ),
        get_roster(),
        proxy_bypass=True,  # terok uses richer proxy handling below
    )

    env = dict(result.env)
    volumes = list(result.volumes)

    # terok-specific env vars not covered by the shared assembly
    env["PROJECT_ID"] = project.id
    env["GIT_RESET_MODE"] = os.environ.get("TEROK_GIT_RESET_MODE", "none")
    if "EXTERNAL_REMOTE_URL" in sec_env:
        env["EXTERNAL_REMOTE_URL"] = sec_env["EXTERNAL_REMOTE_URL"]

    # Credential proxy: full OAuth / socket / SSH support (terok-specific)
    proxy_env, proxy_volumes = _credential_proxy_env_and_volumes(project, task_id)
    env.update(proxy_env)
    volumes += proxy_volumes

    return env, volumes
