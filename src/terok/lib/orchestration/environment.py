# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""Container environment and volume assembly for task containers.

Translates project configuration and security mode into the environment
variables and volume mounts that ``podman run`` needs when launching a
task container.  Shared config mounts and base env vars are delegated to
[`terok_executor.assemble_container_env`][terok_executor.assemble_container_env]; this module adds terok-specific
concerns (gate server, vault with OAuth/socket/SSH support).
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

_CONTAINER_RUNTIME_DIR = "/run/terok"
"""Container-side mount point — must match [`terok_sandbox.CONTAINER_RUNTIME_DIR`][terok_sandbox.CONTAINER_RUNTIME_DIR]."""

_CONTAINER_GATE_PORT = 9418
"""Loopback TCP port the container-side socat bridge listens on in socket mode.

Must match the hardcoded ``TCP-LISTEN:9418`` in ``ensure-bridges.sh`` — that
bridge forwards to the mounted host ``gate-server.sock``, so the CODE_REPO /
CLONE_FROM URL the container sees is ``http://localhost:9418/<repo>``.
"""


def _gate_url(
    gate_repo: Path, gate_base: Path, port: int, token: str, *, use_socket: bool = False
) -> str:
    """Build the ``http://`` URL for a gate repo served by ``terok-gate``.

    The token is embedded as the Basic Auth username in the URL so that git
    handles authentication natively.  Uses the repo directory name as the URL
    path — the gate server serves repos as direct children of its base path.

    In socket mode the container reaches the gate via a localhost socat bridge
    (started by ``ensure-bridges.sh``), so the URL points to ``localhost``
    instead of ``host.containers.internal``.

    Raises ``SystemExit`` if the repo is not a direct child of the gate base,
    since the gate server cannot serve repos from arbitrary locations.
    """
    if gate_repo.resolve().parent != gate_base.resolve():
        raise SystemExit(
            "Configured gate.path is not servable by terok-gate.\n"
            f"  Gate repo: {gate_repo}\n"
            f"  Gate base: {gate_base}\n"
            "Move the repo under the gate base directory, or adjust\n"
            "gate_server.repos_dir / paths.root in global config."
        )
    host = f"localhost:{port}" if use_socket else f"host.containers.internal:{port}"
    return f"http://{token}@{host}/{gate_repo.name}"


def _security_mode_env_and_volumes(
    project: ProjectConfig,
    task_id: str,
    cfg: object,
    *,
    use_socket: bool = False,
) -> tuple[dict[str, str], list[str]]:
    """Return env vars and volumes for the project's security mode."""
    env: dict[str, str] = {}
    volumes: list[str] = []

    gate_repo = project.gate_path
    gate_base = get_gate_base_path(cfg)
    # In socket mode the container reaches the gate via an in-container
    # socat bridge that listens on a fixed port (see ensure-bridges.sh);
    # in TCP mode the container reaches the host's gate server directly.
    gate_port = _CONTAINER_GATE_PORT if use_socket else get_gate_server_port(cfg)

    if project.security_class == "gatekeeping":
        if not gate_repo.exists():
            raise SystemExit(
                f"Git gate missing for project '{project.id}'.\n"
                f"Expected at: {gate_repo}\n"
                f"Run 'terok project gate-sync {project.id}' to create/update the local mirror."
            )
        ensure_server_reachable(cfg)
        token = create_token(project.id, task_id, cfg)
        gate_url = _gate_url(gate_repo, gate_base, gate_port, token, use_socket=use_socket)
        env["CODE_REPO"] = gate_url
        if project.default_branch:
            env["GIT_BRANCH"] = project.default_branch
        if project.expose_external_remote and project.upstream_url:
            env["EXTERNAL_REMOTE_URL"] = project.upstream_url
    else:
        # Online mode: use the gate as a clone accelerator when it exists
        # *and* the project opted in.  ``gate.enabled: false`` is the
        # explicit escape hatch for hosts that cannot reach the remote
        # (container clones directly from upstream instead).
        if project.gate_enabled and gate_repo.exists():
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
                token = create_token(project.id, task_id, cfg)
                gate_url = _gate_url(gate_repo, gate_base, gate_port, token, use_socket=use_socket)
                env["CLONE_FROM"] = gate_url
        if project.upstream_url:
            env["CODE_REPO"] = project.upstream_url
            if project.default_branch:
                env["GIT_BRANCH"] = project.default_branch

    # Gate socket path for the container-side socat bridge (set once for both modes).
    if use_socket and ("CODE_REPO" in env or "CLONE_FROM" in env) and gate_repo.exists():
        env["TEROK_GATE_SOCKET"] = f"{_CONTAINER_RUNTIME_DIR}/{cfg.gate_socket_path.name}"

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


# ---------- Vault ----------


def ensure_vault() -> None:
    """Ensure the vault is reachable (respecting the bypass flag).

    Call this before (re)starting a container that was created with vault
    phantom tokens.  After a host reboot the systemd socket may be active
    but the service idle — this function brings the TCP ports up so
    containers can connect.

    No-op when the ``bypass_no_secret_protection`` flag is set.
    """
    from ..core.config import get_vault_bypass

    if get_vault_bypass():
        return

    from terok_sandbox import VaultUnreachableError, ensure_vault_reachable

    try:
        ensure_vault_reachable(make_sandbox_config())
    except VaultUnreachableError as exc:
        raise SystemExit(
            f"{exc}\n\n"
            "Start it with:\n"
            "  terok vault install   (systemd socket activation)\n"
            "  terok vault start     (manual daemon)"
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


def _shared_config_patch_providers(roster: object) -> frozenset[str]:
    """Return providers that declare shared config patches in the roster."""
    return frozenset(
        name for name, route in roster.vault_routes.items() if route.shared_config_patch
    )


def _vault_patch_provider_sets(
    roster: object, *, vault_bypass: bool = False
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(enabled, disabled)`` shared-config patch provider sets.

    Enabled providers have their roster-declared patches applied.  Disabled
    providers have previously managed values reconciled away if terok still
    owns them.  Codex is special only in its feature gate: the secure
    vaulted OAuth mode enables the shared ``~/.codex/config.toml`` rewrite;
    disabled/exposed/bypassed modes remove stale managed Codex URLs.
    """
    from ..core.config import is_codex_oauth_proxied

    providers = _shared_config_patch_providers(roster)
    if vault_bypass:
        return frozenset(), providers

    enabled = providers
    disabled = frozenset()
    if not is_codex_oauth_proxied():
        enabled -= {"codex"}
        disabled |= providers & {"codex"}
    return enabled, disabled


def _warn_leaked_credentials() -> None:
    """Warn about real credential files in shared mounts.

    When an OAuth token is intentionally exposed (for Claude subscription
    features or direct Codex control), the
    provider-specific leak warning is suppressed and replaced by a loud,
    explicit banner so the user can't miss that a real token is mounted.
    """
    import sys

    from terok_executor import scan_leaked_credentials

    from ..core.config import is_claude_oauth_exposed, is_codex_oauth_exposed
    from ..util.ansi import bold, supports_color, yellow

    leaked = scan_leaked_credentials(sandbox_live_mounts_dir())
    color = supports_color()

    def _banner(provider_label: str, file_desc: str) -> None:
        print(
            "\n"
            + bold(
                yellow(
                    f"  WARNING: {provider_label} OAuth token is EXPOSED to all task containers.\n"
                    f"  The vault does NOT protect this token — it is mounted\n"
                    f"  directly via {file_desc} in the shared config directory.\n"
                    f"  Every task container managed by terok can read the real token.\n",
                    color,
                ),
                color,
            ),
            file=sys.stderr,
        )

    if is_claude_oauth_exposed():
        _banner("Claude", ".credentials.json")
        leaked = [(p, path) for p, path in leaked if p != "claude"]

    if is_codex_oauth_exposed():
        _banner("Codex", "auth.json")
        leaked = [(p, path) for p, path in leaked if p != "codex"]

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
    [`terok_executor.assemble_container_env`][terok_executor.assemble_container_env], then layers terok-specific
    concerns: ``PROJECT_ID``, gate server URLs, and the full vault
    (OAuth, socket transport, SSH agent).

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

    from ..core.config import get_services_mode, get_vault_bypass, get_vault_transport

    cfg = make_sandbox_config()
    use_socket = get_services_mode() == "socket"

    # Pre-resolve gate server URLs → CODE_REPO / CLONE_FROM / GIT_BRANCH
    sec_env, _sec_volumes = _security_mode_env_and_volumes(
        project, task_id, cfg, use_socket=use_socket
    )

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

    # Vault: bypass → no vault at all; otherwise ensure it's up before assembly
    vault_bypass = get_vault_bypass()
    if not vault_bypass:
        ensure_vault()
    vault_transport = get_vault_transport()

    roster = get_roster()
    enabled_patch_providers, disabled_patch_providers = _vault_patch_provider_sets(
        roster, vault_bypass=vault_bypass
    )

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
            vault_transport=vault_transport,
            vault_required=not vault_bypass,
            unrestricted=False,  # task_runners resolves per-provider config
            shared_dir=None if sealed else project.shared_dir,
            envs_dir=sandbox_live_mounts_dir(),
            timezone=project.timezone,
            enabled_vault_patch_providers=enabled_patch_providers,
            disabled_vault_patch_providers=disabled_patch_providers,
        ),
        roster,
        # bypass → skip proxy entirely (no tokens, no check)
        caller_manages_vault=vault_bypass,
    )

    env = dict(result.env)
    volumes: list[VolumeSpec] = list(result.volumes)

    # terok-specific env vars not covered by the shared assembly
    env["PROJECT_ID"] = project.id
    env["GIT_RESET_MODE"] = os.environ.get("TEROK_GIT_RESET_MODE", "none")
    # Merge gate/security env vars not consumed by ContainerEnvSpec
    for key in ("EXTERNAL_REMOTE_URL", "TEROK_GATE_SOCKET"):
        if key in sec_env:
            env[key] = sec_env[key]

    # Socket mode: mount host runtime dir so socat bridges can reach sockets
    if use_socket:
        from terok_sandbox import Sharing

        volumes.append(VolumeSpec(cfg.runtime_dir, _CONTAINER_RUNTIME_DIR, sharing=Sharing.SHARED))

    # Claude OAuth env override + leaked-cred scan with exposed-token filtering
    if not vault_bypass:
        _apply_claude_oauth_overrides(env)
        _warn_leaked_credentials()

    return env, volumes
