# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task container runners: CLI, headless, toad, and restart."""

from __future__ import annotations

import os
import secrets
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from terok_executor import (
    AgentConfigSpec,
    AgentRunner,
    BuildError,
    prepare_agent_config_dir,
    resolve_instructions,
    resolve_provider_value,
)
from terok_sandbox import (
    LifecycleHooks,
    Sandbox,
    Sharing,
    VolumeSpec,
    down as _shield_down_impl,
)

from ..core import runtime as _rt
from ..core.config import (
    SHIELD_SECURITY_HINT,
    get_public_host,
    get_shield_bypass_firewall_no_protection,
    make_sandbox_config,
    sandbox_live_mounts_dir,
)
from ..core.images import project_cli_image, require_agent_installed
from ..core.projects import load_project
from ..core.task_display import has_gpu
from ..util.ansi import (
    blue as _blue,
    green as _green,
    red as _red,
    supports_color as _supports_color,
    yellow as _yellow,
)
from ..util.net import url_host
from ..util.yaml import dump as _yaml_dump, load as _yaml_load
from .agent_config import resolve_agent_config
from .container_exec import container_git_diff
from .environment import build_task_env_and_volumes, ensure_vault
from .hooks import run_hook
from .ports import assign_web_port, release_web_port
from .tasks import (
    container_name,
    load_task_meta,
    task_new,
    update_task_exit_code,
)

if TYPE_CHECKING:
    from ..core.project_model import ProjectConfig


_LOCALHOST = "127.0.0.1"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TOAD_PUBLIC_PORT = 8080
"""Port that Caddy binds inside the container — the one podman publishes."""
_TOAD_INTERNAL_PORT = 8081
"""Loopback port that toad binds inside the container — reached only via Caddy."""
_TOAD_TOKEN_FILE_NAME = "toad.token"  # nosec B105 — filename, not a credential
_ANTHROPIC_API_HOST = "api.anthropic.com"
_FALSE_STRINGS = frozenset({"false", "0", "no", "off"})
_CONTAINER_TEROK_CONFIG = "/home/dev/.terok"


def _str_to_bool(value: object) -> bool:
    """Strictly coerce a config value to bool, treating string ``"false"`` as ``False``."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() not in _FALSE_STRINGS
    return bool(value)


def _apply_unrestricted_env(env: dict[str, str]) -> None:
    """Set ``TEROK_UNRESTRICTED`` and all agent auto-approve env vars.

    Each agent reads its own env var (``VIBE_AUTO_APPROVE``,
    ``OPENCODE_PERMISSION``, ``COPILOT_ALLOW_ALL``) regardless of how
    it is launched (CLI wrapper or ACP).  Setting them at the container
    level provides a single, unified permission mechanism.
    """
    from terok_executor import collect_all_auto_approve_env

    env["TEROK_UNRESTRICTED"] = "1"
    env.update(collect_all_auto_approve_env())


def _ensure_toad_token(agent_config_dir: Path, existing: str | None = None) -> str:
    """Per-task auth token for Caddy, written 0600 to ``toad.token`` and returned.

    Reuses *existing* (restart path) or mints a fresh 32-byte urlsafe
    string.  The write goes through a same-directory temp file + atomic
    ``os.replace``: ``agent-config`` is a bind mount, and a stopped
    container could pre-stage a symlink *or a hardlink* at
    ``toad.token`` — ``O_NOFOLLOW`` protects against the former, but
    only a rename that never touches the destination inode protects
    against the latter (truncating a hardlink clobbers the peer's
    content).
    """
    token = existing or secrets.token_urlsafe(32)
    dir_fd = os.open(agent_config_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    tmp_name = f".{_TOAD_TOKEN_FILE_NAME}.{secrets.token_hex(8)}"
    try:
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=dir_fd,
        )
        try:
            os.write(fd, token.encode())
        finally:
            os.close(fd)
        try:
            os.replace(tmp_name, _TOAD_TOKEN_FILE_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        except BaseException:
            # Clean up the temp file if replace failed for any reason.
            try:
                os.unlink(tmp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            raise
    finally:
        os.close(dir_fd)
    return token


def _toad_browser_url(public_host: str, port: int, token: str) -> str:
    """Return the first-hit URL that seeds the Caddy-set auth cookie."""
    return f"http://{url_host(public_host)}:{port}/?token={token}"


def _agent_config_dir(project: ProjectConfig, task_id: str) -> Path:
    """Return the agent-config mount path for *task_id* under *project*."""
    return project.tasks_root / str(task_id) / "agent-config"


def _rehydrate_toad_token(project: ProjectConfig, task_id: str, meta: dict, cname: str) -> str:
    """Saved toad token from *meta*, rewritten to ``agent-config/toad.token``.

    The file may have been cleaned up between runs even when the token
    persists in metadata; rewriting on every reuse is cheap insurance.
    """
    saved_token = meta.get("web_token")
    if not isinstance(saved_token, str):
        raise SystemExit(
            f"Existing toad container {cname} has no saved web_token in metadata "
            f"(created before the Caddy auth gate landed).  Re-create the task."
        )
    _ensure_toad_token(_agent_config_dir(project, task_id), existing=saved_token)
    return saved_token


def _resume_toad_container(
    *,
    project: ProjectConfig,
    task_id: str,
    cname: str,
    container_state: str,
    meta: dict,
    meta_path: Path,
    pub_host: str,
) -> None:
    """Fast-path for a toad task whose container already exists: rehydrate the token, start it if stopped, print the URL."""
    saved_port = meta.get("web_port")
    if not isinstance(saved_port, int):
        raise SystemExit(f"Existing toad container {cname} has no saved web_port in metadata.")
    actual = assign_web_port(project.id, task_id, preferred=saved_port)
    if actual != saved_port:
        # The registry handed us a fallback port — release it so the task
        # doesn't leak a claim we'll never publish.
        release_web_port(project.id, task_id)
        raise SystemExit(
            f"Port {saved_port} for {project.id}/{task_id} is no longer available "
            f"(got {actual}).  Re-create the task to use the new port."
        )
    ensure_vault()
    saved_token = _rehydrate_toad_token(project, task_id, meta, cname)
    color_enabled = _supports_color()
    url = _toad_browser_url(pub_host, saved_port, saved_token)
    if container_state == "running":
        print(f"Container {_green(cname, color_enabled)} is already running.")
        print(f"Toad: {_blue(url, color_enabled)}")
        return
    print(f"Starting existing container {_green(cname, color_enabled)}...")
    task_dir = project.tasks_root / str(task_id)
    _podman_start(cname)
    _assert_running(cname)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=saved_port,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=True)
    print("Container started.")
    print(f"Toad: {_blue(url, color_enabled)}")


@dataclass(frozen=True)
class HeadlessRunRequest:
    """Groups all parameters for a headless (autopilot) agent run."""

    project_id: str
    prompt: str
    config_path: str | None = None
    model: str | None = None
    max_turns: int | None = None
    timeout: int | None = None
    follow: bool = True
    agents: list[str] | None = None
    preset: str | None = None
    name: str | None = None
    provider: str | None = None
    instructions: str | None = None
    unrestricted: bool | None = None


@dataclass(frozen=True)
class DetachedSummary:
    """Groups all parameters for the detached task summary block."""

    label: str
    task_id: str
    cname: str
    color: bool
    log_cmd: str
    stop_cmd: str


def _prepare_agent_config(
    project: ProjectConfig,
    project_id: str,
    task_id: str,
    agents: list[str] | None,
    preset: str | None,
    *,
    provider_name: str | None = None,
) -> Path:
    """Resolve agent config, instructions, and prepare the agent-config dir.

    Shared by task runners to avoid duplicating the resolve → instructions →
    prepare sequence.  *provider_name* overrides the auto-detected provider
    (e.g. explicit provider selection).
    """
    effective = resolve_agent_config(
        project_id,
        agent_config=project.agent_config,
        project_root=project.root,
        preset=preset,
    )
    subagents = list(effective.get("subagents") or [])
    from terok_executor import get_provider as _get_provider

    resolved = _get_provider(provider_name, default_agent=project.default_agent)
    instr_text = resolve_instructions(effective, resolved.name, project_root=project.root)
    return prepare_agent_config_dir(
        AgentConfigSpec(
            tasks_root=project.tasks_root,
            task_id=task_id,
            subagents=subagents,
            selected_agents=agents,
            provider=resolved.name,
            instructions=instr_text,
            default_agent=project.default_agent,
            mounts_base=sandbox_live_mounts_dir(),
        )
    )


def _podman_start(cname: str) -> None:
    """Start an existing container, raising SystemExit on failure."""
    try:
        _rt.get_runtime().container(cname).start()
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except RuntimeError as exc:
        raise SystemExit(f"Failed to start container:\n{exc}")


def _assert_running(cname: str) -> None:
    """Verify a container is running after start, or raise SystemExit."""
    post_state = _rt.get_runtime().container(cname).state
    if post_state != "running":
        raise SystemExit(
            f"Container {cname} failed to start (state: {post_state}). "
            f"Check logs with: podman logs {cname}"
        )


def _print_login_instructions(project_id: str, task_id: str, cname: str, color: bool) -> None:
    """Print how to log into a CLI container."""
    login_cmd = f"terok login {project_id} {task_id}"
    raw_cmd = shlex.join(_rt.get_runtime().container(cname).login_command(command=("bash",)))
    print(f"Login with: {_blue(login_cmd, color)}")
    print(f"  (or:      {_blue(raw_cmd, color)})")


def _print_detached_summary(summary: DetachedSummary) -> None:
    """Print the summary block shown after detaching from a headless/follow-up task."""
    print(
        f"\n{summary.label}"
        f"\n- Task:  {summary.task_id}"
        f"\n- Name:  {_green(summary.cname, summary.color)}"
        f"\n- Logs:  {_blue(summary.log_cmd, summary.color)}"
        f"\n- Stop:  {_red(summary.stop_cmd, summary.color)}\n"
    )


_DESIRED_SHIELD_STATE_FILENAME = "shield_desired_state"
_VALID_SHIELD_STATES = frozenset({"up", "down", "down_all"})


def _read_desired_shield_state(task_dir: Path) -> str | None:
    """Read the persisted shield state from the task directory."""
    path = task_dir / _DESIRED_SHIELD_STATE_FILENAME
    if not path.is_file():
        return None
    value = path.read_text().strip()
    return value if value in _VALID_SHIELD_STATES else None


def _write_desired_shield_state(task_dir: Path, state: str) -> None:
    """Persist the desired shield state to the task directory."""
    (task_dir / _DESIRED_SHIELD_STATE_FILENAME).write_text(f"{state}\n")


def _restore_shield_state(cname: str, task_dir: Path) -> None:
    """Restore the persisted shield state on container restart (``retain`` policy)."""
    desired = _read_desired_shield_state(task_dir)
    if not desired or not desired.startswith("down"):
        return
    try:
        _shield_down_impl(cname, task_dir, allow_all=(desired == "down_all"))
    except Exception as exc:
        import warnings

        warnings.warn(f"shield restore: {exc}", stacklevel=2)


def _drop_shield_on_creation(cname: str, task_dir: Path) -> None:
    """Drop the shield after fresh container creation and persist the state."""
    try:
        _shield_down_impl(cname, task_dir)
        _write_desired_shield_state(task_dir, "down")
        audit_path = task_dir / "shield" / "audit.jsonl"
        print(f"Shield dropped (bypass mode). Audit log: {audit_path}")
        print(SHIELD_SECURITY_HINT)
    except Exception as exc:
        import warnings

        warnings.warn(f"shield drop: {exc}", stacklevel=2)


def _maybe_deny_anthropic_api(cname: str, task_dir: Path) -> None:
    """Block ``api.anthropic.com`` when Claude OAuth is proxied.

    When the shield is down, deny sets prevent phantom tokens from leaking
    to Anthropic's hardcoded ``BASE_API_URL`` endpoint.  No-op when Claude
    OAuth is skipped or exposed.
    """
    from ..core.config import is_claude_oauth_proxied

    if not is_claude_oauth_proxied():
        return
    try:
        from terok_sandbox import make_shield

        make_shield(task_dir).deny(cname, _ANTHROPIC_API_HOST)
    except Exception as exc:  # noqa: BLE001
        import warnings

        warnings.warn(f"shield deny {_ANTHROPIC_API_HOST}: {exc}", stacklevel=2)


def _apply_shield_policy(
    project: ProjectConfig, cname: str, task_dir: Path, *, is_restart: bool
) -> None:
    """Apply shield policy after container start (creation or restart).

    On fresh creation, honours ``shield.drop_on_task_run``.  On restart,
    honours ``shield.on_task_restart`` (``retain`` restores the last known
    state, ``up`` leaves the deny-all ruleset from the OCI hook).
    """
    if get_shield_bypass_firewall_no_protection():
        return

    if is_restart:
        policy = project.shield_on_task_restart
        if policy == "retain":
            _restore_shield_state(cname, task_dir)
        elif policy == "up":
            pass  # already UP from OCI hook
        else:
            raise ValueError(
                f"Unknown shield.on_task_restart value: {policy!r} (expected 'retain' or 'up')"
            )
    elif project.shield_drop_on_task_run:
        _drop_shield_on_creation(cname, task_dir)
    else:
        _write_desired_shield_state(task_dir, "up")

    _maybe_deny_anthropic_api(cname, task_dir)


def _run_container(
    *,
    cname: str,
    image: str,
    env: dict[str, str],
    volumes: list[VolumeSpec],
    project: ProjectConfig,
    task_dir: Path,
    extra_args: list[str] | None = None,
    command: list[str] | None = None,
    hooks: LifecycleHooks | None = None,
) -> None:
    """Launch a detached task container via the executor's public API.

    Delegates all podman command assembly (userns, shield/bypass, GPU,
    env redaction, CDI detection) to :meth:`AgentRunner.launch_prepared`,
    which in turn drives the sandbox.  In sealed isolation mode
    (``project.is_sealed``), the sandbox splits into create → copy → start
    instead of a single ``podman run -d``.

    Args:
        cname: Container name (``--name``).
        image: Container image to run.
        env: Environment variables to pass via ``-e``.
        volumes: Typed volume specs (sandbox decides mount vs inject).
        project: The resolved :class:`ProjectConfig` (used for GPU flag).
        task_dir: Per-task directory (used for per-task shield state).
        extra_args: Additional ``podman run`` flags inserted after the GPU
            args (e.g. ``["-p", "127.0.0.1:8080:7860"]``).
        command: Optional command + args appended after the image name.
        hooks: Optional lifecycle callbacks fired around the launch.
    """
    merged_args = list(extra_args or ()) + _project_runtime_flags(project)
    try:
        _agent_runner().launch_prepared(
            env=env,
            volumes=volumes,
            image=image,
            command=list(command or ()),
            name=cname,
            task_dir=task_dir,
            gpu=has_gpu(project),
            memory=project.memory_limit,
            cpus=project.cpu_limit,
            unrestricted="TEROK_UNRESTRICTED" in env,
            sealed=project.is_sealed,
            hooks=hooks,
            extra_args=merged_args,
            hostname=cname,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"podman not found; please install podman ({exc})") from exc
    except BuildError as exc:
        raise SystemExit(str(exc)) from exc


def _agent_runner() -> AgentRunner:
    """Return an :class:`AgentRunner` bound to terok's bridged sandbox config."""
    return AgentRunner(sandbox=Sandbox(make_sandbox_config()))


def _project_runtime_flags(project: ProjectConfig) -> list[str]:
    """Return extra ``podman run`` flags derived from project-level capabilities.

    ``run.nested_containers`` → ``--security-opt label=nested`` plus
    ``--device /dev/fuse``.  ``label=nested`` confines the outer container
    to the SELinux type that permits nested container operations
    (devpts mount, rootless overlay setup) without disabling labelling;
    ``/dev/fuse`` is required by rootless podman's fuse-overlayfs driver.
    Available on podman v4.5.0+ (April 2023); older podmans error with
    "unknown label option: nested" and the user is expected to upgrade.
    """
    flags: list[str] = []
    if project.nested_containers:
        flags += ["--security-opt", "label=nested", "--device", "/dev/fuse"]
    return flags


def task_run_cli(
    project_id: str,
    task_id: str,
    agents: list[str] | None = None,
    preset: str | None = None,
    unrestricted: bool | None = None,
) -> None:
    """Launch a CLI-mode task container and wait for its readiness marker.

    Creates (or reattaches to) a detached Podman container for interactive
    CLI access.  After the container reports ready the task metadata is
    marked ``running`` and the user is shown login instructions.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id, "cli")

    cname = container_name(project.id, "cli", task_id)
    container_state = _rt.get_runtime().container(cname).state

    # If container already exists, handle it
    if container_state is not None:
        ensure_vault()
        color_enabled = _supports_color()
        if container_state == "running":
            print(f"Container {_green(cname, color_enabled)} is already running.")
            _print_login_instructions(project.id, task_id, cname, color_enabled)
            return
        # Container exists but is stopped/exited - start it
        print(f"Starting existing container {_green(cname, color_enabled)}...")
        _podman_start(cname)
        _assert_running(cname)
        task_dir = project.tasks_root / str(task_id)
        run_hook(
            "post_start",
            project.hook_post_start,
            project_id=project.id,
            task_id=task_id,
            mode="cli",
            cname=cname,
            task_dir=task_dir,
            meta_path=meta_path,
        )
        _apply_shield_policy(project, cname, task_dir, is_restart=True)
        meta["mode"] = "cli"
        meta["ready_at"] = datetime.now(UTC).isoformat()
        meta_path.write_text(_yaml_dump(meta))
        print("Container started.")
        _print_login_instructions(project.id, task_id, cname, color_enabled)
        return

    env, volumes = build_task_env_and_volumes(project, task_id)

    # Resolve layered agent config (global → project → preset → CLI overrides)
    agent_config_dir = _prepare_agent_config(project, project_id, task_id, agents, preset)
    volumes.append(VolumeSpec(agent_config_dir, _CONTAINER_TEROK_CONFIG, sharing=Sharing.PRIVATE))

    # Resolve unrestricted mode: CLI flag → config → default (True)
    if unrestricted is None:
        _effective = resolve_agent_config(
            project_id,
            agent_config=project.agent_config,
            project_root=project.root,
            preset=preset,
        )
        _cfg_val = resolve_provider_value(
            "unrestricted", _effective, project.default_agent or "claude"
        )
        unrestricted = _cfg_val is None or _str_to_bool(_cfg_val)
    if unrestricted:
        _apply_unrestricted_env(env)

    # Run detached and keep the container alive so users can exec into it later
    # Note: We intentionally do NOT use --rm so containers persist after stopping.
    # This allows `task restart` to quickly resume stopped containers.
    task_dir = project.tasks_root / str(task_id)
    run_hook(
        "pre_start",
        project.hook_pre_start,
        project_id=project.id,
        task_id=task_id,
        mode="cli",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        task_dir=task_dir,
        # Ensure init runs and then keep the container alive even without a TTY
        # init-ssh-and-repo.sh now prints a readiness marker we can watch for
        command=["bash", "-lc", "init-ssh-and-repo.sh && echo __CLI_READY__; tail -f /dev/null"],
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=False)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="cli",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    # Stream initial logs until ready marker is seen (or timeout), then detach
    _rt.get_runtime().container(cname).stream_initial_logs(
        ready_check=lambda line: "__CLI_READY__" in line or ">> init complete" in line,
        timeout_sec=60.0,
    )

    # Verify the container is still alive after log streaming
    _assert_running(cname)
    run_hook(
        "post_ready",
        project.hook_post_ready,
        project_id=project.id,
        task_id=task_id,
        mode="cli",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    meta["mode"] = "cli"
    meta["ready_at"] = datetime.now(UTC).isoformat()
    meta["unrestricted"] = unrestricted
    if preset:
        meta["preset"] = preset
    meta_path.write_text(_yaml_dump(meta))

    color_enabled = _supports_color()
    print(
        f"\nCLI container is running in the background.\n- Name:     {_green(cname, color_enabled)}"
    )
    _print_login_instructions(project.id, task_id, cname, color_enabled)
    print(f"- To stop:  {_red(f'podman stop {cname}', color_enabled)}\n")


def task_run_toad(
    project_id: str,
    task_id: str,
    agents: list[str] | None = None,
    preset: str | None = None,
    unrestricted: bool | None = None,
) -> None:
    """Launch the Toad multi-agent TUI behind Caddy for token-gated browser access.

    Same CLI image as interactive tasks, but the container entrypoint is
    ``terok-toad-entry``: it starts Caddy on the published port, toad on
    an internal loopback port, and emits ``TEROK_READY`` once both are
    listening.  Caddy enforces the per-task token (see
    :func:`_ensure_toad_token`) on every request.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id, "toad")

    cname = container_name(project.id, "toad", task_id)
    container_state = _rt.get_runtime().container(cname).state

    pub_host = get_public_host()

    if container_state is not None:
        _resume_toad_container(
            project=project,
            task_id=task_id,
            cname=cname,
            container_state=container_state,
            meta=meta,
            meta_path=meta_path,
            pub_host=pub_host,
        )
        return

    # New container — allocate a fresh port.
    port = assign_web_port(project.id, task_id)
    meta["web_port"] = port

    env, volumes = build_task_env_and_volumes(project, task_id)

    agent_config_dir = _prepare_agent_config(project, project_id, task_id, agents, preset)
    volumes.append(VolumeSpec(agent_config_dir, _CONTAINER_TEROK_CONFIG, sharing=Sharing.PRIVATE))

    token = _ensure_toad_token(agent_config_dir)
    meta["web_token"] = token

    env["TOAD_PUBLIC_PORT"] = str(_TOAD_PUBLIC_PORT)
    env["TOAD_INTERNAL_PORT"] = str(_TOAD_INTERNAL_PORT)

    # Resolve unrestricted mode: CLI flag → config → default (True)
    if unrestricted is None:
        _effective = resolve_agent_config(
            project_id,
            agent_config=project.agent_config,
            project_root=project.root,
            preset=preset,
        )
        _cfg_val = resolve_provider_value(
            "unrestricted", _effective, project.default_agent or "claude"
        )
        unrestricted = _cfg_val is None or _str_to_bool(_cfg_val)
    if unrestricted:
        _apply_unrestricted_env(env)

    meta["mode"] = "toad"
    meta["unrestricted"] = unrestricted
    if preset:
        meta["preset"] = preset
    meta_path.write_text(_yaml_dump(meta))

    # Preserve the address family when the public host is a loopback — binding
    # ::1 to 127.0.0.1 would make the URL we print (``http://[::1]:…``)
    # unreachable.  LAN exposure still goes to ``0.0.0.0``.
    if pub_host == "::1":
        bind_addr = "[::1]"
    elif pub_host in _LOOPBACK_HOSTS:
        bind_addr = _LOCALHOST
    else:
        bind_addr = "0.0.0.0"  # nosec B104

    task_dir = project.tasks_root / str(task_id)
    # ``terok-toad-entry`` (from the caddy/toad roster entries) owns the
    # in-container choreography: it starts Caddy on ``_TOAD_PUBLIC_PORT``,
    # launches toad on loopback ``_TOAD_INTERNAL_PORT``, waits for both to
    # bind, and emits the ``TEROK_READY`` readiness marker.
    toad_cmd = f"terok-toad-entry --public-url http://{url_host(pub_host)}:{port} /workspace"
    run_hook(
        "pre_start",
        project.hook_pre_start,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=port,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        task_dir=task_dir,
        extra_args=["-p", f"{bind_addr}:{port}:{_TOAD_PUBLIC_PORT}"],
        command=["bash", "-lc", toad_cmd],
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=False)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=port,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    def _toad_ready(line: str) -> bool:
        """Return True when the supervisor wrapper reports both listeners are up."""
        return "TEROK_READY" in line

    ready = (
        _rt.get_runtime()
        .container(cname)
        .stream_initial_logs(
            ready_check=_toad_ready,
            timeout_sec=None,
        )
    )

    if not ready or not _rt.get_runtime().container(cname).running:
        print(f"Toad failed to start. Check logs: podman logs {cname}")
        raise SystemExit(1)

    run_hook(
        "post_ready",
        project.hook_post_ready,
        project_id=project.id,
        task_id=task_id,
        mode="toad",
        cname=cname,
        web_port=port,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    meta["ready_at"] = datetime.now(UTC).isoformat()
    meta_path.write_text(_yaml_dump(meta))

    color_enabled = _supports_color()
    url = _toad_browser_url(pub_host, port, token)
    print(
        f"\n>> Toad is serving."
        f"\n- Name: {_green(cname, color_enabled)}"
        f"\n- URL:  {_blue(url, color_enabled)}"
        f"\n- Logs: {_yellow(f'podman logs -f {cname}', color_enabled)}"
        f"\n- Stop: {_red(f'podman stop {cname}', color_enabled)}"
    )


def _print_run_summary(project_id: str, task_id: str, mode: str, workspace: Path) -> None:
    """Print a summary of changes made by the headless agent.

    Runs ``git diff --stat`` **inside** the task container to avoid executing
    potentially poisoned git hooks on the host.
    """
    diff_stat = container_git_diff(project_id, task_id, mode, "--stat", "HEAD@{1}..HEAD")
    if diff_stat is not None:
        stripped = diff_stat.strip()
        if stripped:
            print("\n── Changes ──────────────────────────────")
            print(stripped)
        else:
            print("\n── No changes committed ──────────────────")
    print(f"  Workspace: {workspace}")


def task_run_headless(request: HeadlessRunRequest) -> str:
    """Run an agent headlessly (autopilot mode) in a new task container.

    Creates a new task, prepares the agent-config directory with the provider's
    wrapper function and filtered subagents, then launches a detached container
    that runs init-ssh-and-repo.sh followed by the agent command.

    Args:
        request: All per-run options bundled in a :class:`HeadlessRunRequest`.

    Returns the task_id.
    """
    from terok_executor import (
        CLIOverrides,
        apply_provider_config,
        build_headless_command,
        get_provider,
    )

    project = load_project(request.project_id)
    resolved = get_provider(request.provider, default_agent=project.default_agent)
    require_agent_installed(project, resolved.name)

    # Build CLI overrides from --config file and explicit flags
    cli_overrides: dict = {}
    if request.config_path:
        config_src = Path(request.config_path)
        if not config_src.is_file():
            raise SystemExit(f"Agent config file not found: {request.config_path}")
        cli_config = _yaml_load(config_src.read_text(encoding="utf-8")) or {}
        cli_overrides = cli_config

    # Resolve layered agent config (global → project → preset → CLI overrides)
    effective = resolve_agent_config(
        request.project_id,
        agent_config=project.agent_config,
        project_root=project.root,
        preset=request.preset,
        cli_overrides=cli_overrides if cli_overrides else None,
    )

    # Resolve instructions: CLI --instructions overrides config stack
    instr_text = (
        request.instructions
        if request.instructions is not None
        else resolve_instructions(effective, resolved.name, project_root=project.root)
    )

    # Apply provider-aware config resolution with best-effort feature mapping.
    # CLI flags override config values; unsupported features produce warnings
    # or prompt augmentation.
    pcfg = apply_provider_config(
        resolved,
        effective,
        CLIOverrides(
            model=request.model,
            max_turns=request.max_turns,
            timeout=request.timeout,
            instructions=instr_text,
        ),
    )

    # Print warnings about unsupported features
    for warning in pcfg.warnings:
        print(f"Warning: {warning}")

    # Augment prompt with best-effort feature analogues (e.g. max-turns guidance)
    effective_prompt = request.prompt
    if pcfg.prompt_extra:
        effective_prompt = f"{request.prompt}\n\n{pcfg.prompt_extra}"

    # Create a new task
    task_id = task_new(request.project_id, name=request.name)

    # Collect subagents from resolved config
    subagents = list(effective.get("subagents") or [])

    # Prepare agent-config dir with wrapper, agents.json, prompt.txt, instructions.md
    task_dir = project.tasks_root / str(task_id)
    agent_config_dir = prepare_agent_config_dir(
        AgentConfigSpec(
            tasks_root=project.tasks_root,
            task_id=task_id,
            subagents=subagents,
            selected_agents=request.agents,
            prompt=effective_prompt,
            provider=resolved.name,
            instructions=instr_text,
            default_agent=project.default_agent,
            mounts_base=sandbox_live_mounts_dir(),
        )
    )

    # Resolve unrestricted mode: CLI flag → config → default (True)
    unrestricted = request.unrestricted
    if unrestricted is None:
        cfg_val = resolve_provider_value("unrestricted", effective, resolved.name)
        unrestricted = _str_to_bool(cfg_val) if cfg_val is not None else True

    # Build env and volumes
    env, volumes = build_task_env_and_volumes(project, task_id)

    # Set TEROK_UNRESTRICTED for the wrapper functions inside the container
    if unrestricted:
        _apply_unrestricted_env(env)

    # Mount agent-config dir to /home/dev/.terok
    volumes.append(VolumeSpec(agent_config_dir, _CONTAINER_TEROK_CONFIG, sharing=Sharing.PRIVATE))

    # Build headless command via provider registry
    headless_cmd = build_headless_command(
        resolved,
        timeout=pcfg.timeout,
        model=pcfg.model,
        max_turns=pcfg.max_turns,
    )

    # Build podman command (DETACHED)
    cname = container_name(project.id, "run", task_id)

    meta, meta_path = load_task_meta(project.id, task_id)
    run_hook(
        "pre_start",
        project.hook_pre_start,
        project_id=project.id,
        task_id=task_id,
        mode="run",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        task_dir=task_dir,
        command=["bash", "-lc", headless_cmd],
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=False)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="run",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )

    # Update task metadata
    meta["mode"] = "run"
    meta["ready_at"] = datetime.now(UTC).isoformat()
    meta["provider"] = resolved.name
    meta["unrestricted"] = unrestricted
    if request.preset:
        meta["preset"] = request.preset
    meta_path.write_text(_yaml_dump(meta))

    color_enabled = _supports_color()

    if request.follow:
        exit_code = _rt.get_runtime().container(cname).wait()
        _print_run_summary(project.id, task_id, "run", task_dir / "workspace-dangerous")

        update_task_exit_code(project.id, task_id, exit_code)

        if exit_code != 0:
            print(f"\n{resolved.label} exited with code {_red(str(exit_code), color_enabled)}")
    else:
        _print_detached_summary(
            DetachedSummary(
                label=f"Headless {resolved.label} task started (detached).",
                task_id=task_id,
                cname=cname,
                color=color_enabled,
                log_cmd=f"podman logs -f {cname}",
                stop_cmd=f"podman stop {cname}",
            )
        )

    return task_id


def task_followup_headless(
    project_id: str,
    task_id: str,
    prompt: str,
    follow: bool = True,
) -> None:
    """Send a follow-up prompt to a completed/failed headless task.

    Replaces prompt.txt with the new prompt (so the agent only sees the
    current instruction) and archives the previous content to
    ``prompt-history.txt``.  Restarts the stopped container via
    ``podman start``.  Session context is
    automatically restored for providers that support it:

    - **Claude**: resumes via ``--resume <session-id>`` (captured by a
      ``SessionStart`` hook that writes ``claude-session.txt``).
    - **OpenCode / Blablador**: resumes via ``--session <id>`` (captured by
      the ``opencode-session-plugin.mjs`` plugin that writes the session
      file on ``session.created`` events).
    - **Vibe**: resumes via ``--resume <id>`` (session ID parsed post-run
      from ``~/.vibe/logs/session/`` metadata).
    - **Codex / Copilot**: no session resume support — follow-ups start a
      fresh session with the new prompt only.

    Per-run flags (model, max_turns, timeout) carry forward from the
    original ``task_run_headless`` invocation since ``podman start``
    re-executes the same container command.
    """
    from terok_executor import AGENT_PROVIDERS

    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id)

    mode = meta.get("mode")
    if mode != "run":
        raise SystemExit(
            f"Task {task_id} is not a headless task (mode={mode!r}). "
            f"Follow-up is only supported for autopilot (mode='run') tasks."
        )

    cname = container_name(project.id, "run", task_id)
    container_state = _rt.get_runtime().container(cname).state
    if container_state == "running":
        raise SystemExit(
            f"Container {cname} is still running. "
            f"Wait for it to finish or stop it before sending a follow-up."
        )
    if container_state is None:
        raise SystemExit(
            f"Container {cname} not found. Cannot follow up — the container may have been removed."
        )

    # Resolve provider from task metadata
    provider_name = meta.get("provider", "claude")
    resolved = AGENT_PROVIDERS.get(provider_name)
    if resolved is None:
        import warnings

        warnings.warn(
            f"Unknown provider {provider_name!r} in task metadata; session resume check skipped.",
            stacklevel=2,
        )
    label = resolved.label if resolved else provider_name

    if resolved and not resolved.supports_session_resume:
        print(
            f"Note: {label} does not support session resume. "
            f"Follow-up will start a fresh session with the new prompt."
        )

    # Write follow-up prompt to prompt.txt (replaces previous content so the
    # agent only sees the current instruction).  Prior prompts are archived to
    # prompt-history.txt for logging/debugging.
    task_dir = project.tasks_root / str(task_id)
    agent_config_dir = task_dir / "agent-config"

    if project.is_sealed:
        # Sealed: inject prompt via podman cp into stopped container
        from terok_executor import inject_prompt

        inject_prompt(cname, prompt)
    else:
        prompt_path = agent_config_dir / "prompt.txt"
        history_path = agent_config_dir / "prompt-history.txt"
        existing = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
        if existing:
            with history_path.open("a", encoding="utf-8") as hf:
                hf.write(f"{existing}\n\n---\n\n")
        prompt_path.write_text(prompt, encoding="utf-8")

    # Ensure the vault is reachable before restarting — after a
    # host reboot the systemd socket may be active but the service idle.
    ensure_vault()

    # Restart the existing container (re-runs the original bash command,
    # which reads prompt.txt and session files from the volume)
    _podman_start(cname)
    _assert_running(cname)
    run_hook(
        "post_start",
        project.hook_post_start,
        project_id=project.id,
        task_id=task_id,
        mode="run",
        cname=cname,
        task_dir=task_dir,
        meta_path=meta_path,
    )
    _apply_shield_policy(project, cname, task_dir, is_restart=True)

    # Clear previous exit_code so effective_status shows "running" until new exit
    meta["exit_code"] = None
    meta_path.write_text(_yaml_dump(meta))

    color_enabled = _supports_color()

    if follow:
        exit_code = _rt.get_runtime().container(cname).wait()
        _print_run_summary(project.id, task_id, "run", task_dir / "workspace-dangerous")

        update_task_exit_code(project.id, task_id, exit_code)

        if exit_code != 0:
            print(f"\n{label} exited with code {_red(str(exit_code), color_enabled)}")
    else:
        _print_detached_summary(
            DetachedSummary(
                label="Follow-up started (detached).",
                task_id=task_id,
                cname=cname,
                color=color_enabled,
                log_cmd=f"podman logs -f {cname}",
                stop_cmd=f"podman stop {cname}",
            )
        )


def task_restart(project_id: str, task_id: str) -> None:
    """Restart a task's container.

    Semantics: stop the container if running, then start it.  If the
    container doesn't exist (e.g. because it was deleted out-of-band),
    raise ``SystemExit`` with an actionable pointer to ``terok task run``
    — "restart" only means restart, not re-run.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id)

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(f"Task {task_id} has never been run (no mode set)")

    cname = container_name(project.id, mode, task_id)
    container_state = _rt.get_runtime().container(cname).state

    print(f"Restarting task {project_id}/{task_id} ({mode})...")
    ensure_vault()

    # Validate the preconditions that would fail the restart *before*
    # stopping a healthy container — taking down a working service only
    # to then error out is a worse outcome than refusing to stop.
    if container_state is not None:
        web_port = meta.get("web_port")
        if isinstance(web_port, int):
            actual = assign_web_port(project.id, task_id, preferred=web_port)
            if actual != web_port:
                release_web_port(project.id, task_id)
                raise SystemExit(
                    f"Port {web_port} for {project.id}/{task_id} is no longer available "
                    f"(got {actual}).  Re-create the task to use the new port."
                )
        if mode == "toad":
            _rehydrate_toad_token(project, task_id, meta, cname)

    if container_state == "running":
        # Container is running - stop it first, then start it again
        try:
            _rt.get_runtime().container(cname).stop(timeout=project.shutdown_timeout)
        except FileNotFoundError:
            raise SystemExit("podman not found; please install podman")
        except RuntimeError as exc:
            raise SystemExit(f"Failed to stop container: {exc}")
        run_hook(
            "post_stop",
            project.hook_post_stop,
            project_id=project_id,
            task_id=task_id,
            mode=mode,
            cname=cname,
            task_dir=project.tasks_root / str(task_id),
            meta_path=meta_path,
        )

    if container_state is not None:
        task_dir = project.tasks_root / str(task_id)
        _podman_start(cname)
        _assert_running(cname)
        run_hook(
            "post_start",
            project.hook_post_start,
            project_id=project_id,
            task_id=task_id,
            mode=mode,
            cname=cname,
            task_dir=task_dir,
            meta_path=meta_path,
        )
        _apply_shield_policy(project, cname, task_dir, is_restart=True)

        color_enabled = _supports_color()
        print(f"Restarted task {task_id}: {_green(cname, color_enabled)}")
        if mode == "cli":
            _print_login_instructions(project_id, task_id, cname, color_enabled)
        elif mode == "toad":
            port = meta.get("web_port")
            token = meta.get("web_token")
            if isinstance(port, int) and isinstance(token, str):
                print(f"Toad: {_toad_browser_url(get_public_host(), port, token)}")
    else:
        # Container is gone — restart can't recreate it.  User must start
        # a fresh task with ``task run``.
        raise SystemExit(
            f"Container {cname} no longer exists.  Restart requires a running "
            f"or stopped container.  Create a new task with:\n"
            f"  terok task run {project_id}"
            + (' "<prompt>" --mode headless' if mode == "run" else "")
        )
