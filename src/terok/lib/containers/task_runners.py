# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task container runners: CLI, web, headless, and restart."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..core.images import project_cli_image, project_web_image
from ..core.projects import load_project
from ..security.shield import pre_start as _shield_pre_start
from ..util.ansi import (
    blue as _blue,
    green as _green,
    red as _red,
    supports_color as _supports_color,
    yellow as _yellow,
)
from ..util.podman import _podman_userns_args
from .agent_config import resolve_agent_config, resolve_provider_value
from .agents import AgentConfigSpec, prepare_agent_config_dir
from .environment import (
    apply_web_env_overrides,
    build_task_env_and_volumes,
)
from .instructions import resolve_instructions
from .ports import assign_web_port
from .runtime import (
    container_name,
    get_container_state,
    gpu_run_args,
    is_container_running,
    stream_initial_logs,
    wait_for_exit,
)
from .tasks import (
    load_task_meta,
    task_new,
    update_task_exit_code,
)

if TYPE_CHECKING:
    from ..core.project_model import ProjectConfig

_LOCALHOST = "127.0.0.1"
_SENSITIVE_KEY_RE = re.compile(r"(?i)(KEY|TOKEN|SECRET|API|PASSWORD|PRIVATE)")


def _redact_env_args(cmd: list[str]) -> list[str]:
    """Return a copy of *cmd* with sensitive ``-e KEY=VALUE`` args redacted."""
    out: list[str] = []
    redact_next = False
    for arg in cmd:
        if redact_next:
            k, _, _v = arg.partition("=")
            out.append(f"{k}=REDACTED" if _SENSITIVE_KEY_RE.search(k) else arg)
            redact_next = False
        elif arg == "-e":
            out.append(arg)
            redact_next = True
        else:
            out.append(arg)
    return out


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

    Shared by CLI and web task runners to avoid duplicating the
    resolve → instructions → prepare sequence.  *provider_name* overrides
    the auto-detected provider (e.g. web backend selection).
    """
    effective = resolve_agent_config(project_id, preset=preset)
    subagents = list(effective.get("subagents") or [])
    from .headless_providers import get_provider as _get_provider

    resolved = _get_provider(provider_name, project)
    instr_text = resolve_instructions(effective, resolved.name, project_root=project.root)
    return prepare_agent_config_dir(
        AgentConfigSpec(
            project=project,
            task_id=task_id,
            subagents=subagents,
            selected_agents=agents,
            provider=resolved.name,
            instructions=instr_text,
        )
    )


_CDI_HINT = (
    "Hint: NVIDIA CDI configuration appears to be missing or broken.\n"
    "Ensure the NVIDIA Container Toolkit is installed and CDI is configured.\n"
    "See: https://podman-desktop.io/docs/podman/gpu"
)

_CDI_ERROR_PATTERNS = ("cdi.k8s.io", "nvidia.com/gpu", "CDI")


def _enrich_run_error(prefix: str, exc: subprocess.CalledProcessError) -> str:
    """Return an enriched error message, adding a CDI hint when applicable."""
    stderr = (exc.stderr or b"").decode(errors="replace")
    msg = f"{prefix}: {exc}" if not stderr else f"{prefix}:\n{stderr.strip()}"
    if any(pat in stderr for pat in _CDI_ERROR_PATTERNS):
        msg += f"\n\n{_CDI_HINT}"
    return msg


def _podman_start(cname: str) -> None:
    """Start an existing container, raising SystemExit on failure."""
    try:
        subprocess.run(
            ["podman", "start", cname],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except subprocess.CalledProcessError as e:
        raise SystemExit(_enrich_run_error("Failed to start container", e))


def _assert_running(cname: str) -> None:
    """Verify a container is running after start, or raise SystemExit."""
    post_state = get_container_state(cname)
    if post_state != "running":
        raise SystemExit(
            f"Container {cname} failed to start (state: {post_state}). "
            f"Check logs with: podman logs {cname}"
        )


def _print_login_instructions(project_id: str, task_id: str, cname: str, color: bool) -> None:
    """Print how to log into a CLI container."""
    login_cmd = f"terokctl login {project_id} {task_id}"
    raw_cmd = f"podman exec -it {cname} bash"
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


def _run_container(
    *,
    cname: str,
    image: str,
    env: dict[str, str],
    volumes: list[str],
    project: ProjectConfig,
    extra_args: list[str] | None = None,
    command: list[str] | None = None,
) -> None:
    """Build, print, and execute a detached ``podman run`` command.

    Centralises the shared container-launch boilerplate used by the CLI, web,
    and headless runners: user-namespace mapping, GPU passthrough, volume and
    environment injection, and uniform error handling.

    Args:
        cname: Container name (``--name``).
        image: Container image to run.
        env: Environment variables to pass via ``-e``.
        volumes: Volume mounts to pass via ``-v``.
        project: The resolved :class:`ProjectConfig` (used for GPU args).
        extra_args: Additional ``podman run`` flags inserted after the GPU
            args (e.g. ``["-p", "127.0.0.1:8080:7860"]``).
        command: Optional command + args appended after the image name.
    """
    cmd: list[str] = ["podman", "run", "-d"]
    cmd += _podman_userns_args()
    cmd += _shield_pre_start(cname)
    cmd += gpu_run_args(project)
    if extra_args:
        cmd += extra_args
    for v in volumes:
        cmd += ["-v", v]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += ["--name", cname, "-w", "/workspace", image]
    if command:
        cmd += command
    print("$", " ".join(_redact_env_args(cmd)))
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except FileNotFoundError:
        raise SystemExit("podman not found; please install podman")
    except subprocess.CalledProcessError as e:
        raise SystemExit(_enrich_run_error("Run failed", e))


def task_run_cli(
    project_id: str, task_id: str, agents: list[str] | None = None, preset: str | None = None
) -> None:
    """Launch a CLI-mode task container and wait for its readiness marker.

    Creates (or reattaches to) a detached Podman container for interactive
    CLI access.  After the container reports ready the task metadata is
    marked ``running`` and the user is shown login instructions.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id, "cli")

    cname = container_name(project.id, "cli", task_id)
    container_state = get_container_state(cname)

    # If container already exists, handle it
    if container_state is not None:
        color_enabled = _supports_color()
        if container_state == "running":
            print(f"Container {_green(cname, color_enabled)} is already running.")
            _print_login_instructions(project.id, task_id, cname, color_enabled)
            return
        # Container exists but is stopped/exited - start it
        print(f"Starting existing container {_green(cname, color_enabled)}...")
        _podman_start(cname)
        _assert_running(cname)
        meta["mode"] = "cli"
        meta_path.write_text(yaml.safe_dump(meta))
        print("Container started.")
        _print_login_instructions(project.id, task_id, cname, color_enabled)
        return

    env, volumes = build_task_env_and_volumes(project, task_id)

    # Resolve layered agent config (global → project → preset → CLI overrides)
    agent_config_dir = _prepare_agent_config(project, project_id, task_id, agents, preset)
    volumes.append(f"{agent_config_dir}:/home/dev/.terok:Z")

    # Resolve unrestricted mode from config (CLI/web tasks default to True)
    _effective = resolve_agent_config(project_id, preset=preset)
    _unrestricted = resolve_provider_value(
        "unrestricted", _effective, project.default_agent or "claude"
    )
    if _unrestricted is None or _unrestricted:
        env["TEROK_UNRESTRICTED"] = "1"

    # Run detached and keep the container alive so users can exec into it later
    # Note: We intentionally do NOT use --rm so containers persist after stopping.
    # This allows `task restart` to quickly resume stopped containers.
    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        # Ensure init runs and then keep the container alive even without a TTY
        # init-ssh-and-repo.sh now prints a readiness marker we can watch for
        command=["bash", "-lc", "init-ssh-and-repo.sh && echo __CLI_READY__; tail -f /dev/null"],
    )

    # Stream initial logs until ready marker is seen (or timeout), then detach
    stream_initial_logs(
        container_name=cname,
        timeout_sec=60.0,
        ready_check=lambda line: "__CLI_READY__" in line or ">> init complete" in line,
    )

    # Verify the container is still alive after log streaming
    _assert_running(cname)

    meta["mode"] = "cli"
    meta["unrestricted"] = _unrestricted is None or bool(_unrestricted)
    if preset:
        meta["preset"] = preset
    meta_path.write_text(yaml.safe_dump(meta))

    color_enabled = _supports_color()
    print(
        f"\nCLI container is running in the background.\n- Name:     {_green(cname, color_enabled)}"
    )
    _print_login_instructions(project.id, task_id, cname, color_enabled)
    print(f"- To stop:  {_red(f'podman stop {cname}', color_enabled)}\n")


def task_run_web(
    project_id: str,
    task_id: str,
    backend: str | None = None,
    agents: list[str] | None = None,
    preset: str | None = None,
) -> None:
    """Launch a web-mode task container with a browser-accessible IDE backend.

    Sets up port forwarding, starts a detached Podman container running
    the chosen *backend* (OpenHands or Open WebUI), and prints the URL
    the user can open in a browser.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id, "web")

    mode_updated = meta.get("mode") != "web"
    if mode_updated:
        meta["mode"] = "web"

    if preset and meta.get("preset") != preset:
        meta["preset"] = preset

    port = meta.get("web_port")
    if not isinstance(port, int):
        port = assign_web_port()
        meta["web_port"] = port

    env, volumes = build_task_env_and_volumes(project, task_id)

    # Resolve layered agent config (global → project → preset → CLI overrides)
    # Note: backend is a web UI name (codex/claude/copilot), not a headless provider
    agent_config_dir = _prepare_agent_config(project, project_id, task_id, agents, preset)
    volumes.append(f"{agent_config_dir}:/home/dev/.terok:Z")

    env = apply_web_env_overrides(env, backend, project.default_agent)

    # Save the effective backend to task metadata for UI display
    effective_backend = env.get("TEROK_UI_BACKEND", "codex")
    backend_updated = meta.get("backend") != effective_backend
    if backend_updated:
        meta["backend"] = effective_backend

    # Resolve unrestricted mode from config using the effective backend
    _effective = resolve_agent_config(project_id, preset=preset)
    _unrestricted = resolve_provider_value("unrestricted", _effective, effective_backend)
    resolved_unrestricted = _unrestricted is None or bool(_unrestricted)
    if resolved_unrestricted:
        env["TEROK_UNRESTRICTED"] = "1"

    cname = container_name(project.id, "web", task_id)
    container_state = get_container_state(cname)

    # If container already exists, handle it — don't overwrite metadata with
    # a potentially different unrestricted value while the container keeps its
    # original environment.
    if container_state is not None:
        color_enabled = _supports_color()
        url = f"http://{_LOCALHOST}:{port}/"
        if container_state == "running":
            print(f"Container {_green(cname, color_enabled)} is already running.")
            print(f"Web UI: {_blue(url, color_enabled)}")
            return
        # Container exists but is stopped/exited - start it
        print(f"Starting existing container {_green(cname, color_enabled)}...")
        _podman_start(cname)
        _assert_running(cname)
        print("Container started.")
        print(f"Web UI: {_blue(url, color_enabled)}")
        return

    # Persist metadata only when a new container is actually being created
    meta["unrestricted"] = resolved_unrestricted
    meta_path.write_text(yaml.safe_dump(meta))

    # Start UI in background and return terminal when it's reachable
    # Note: We intentionally do NOT use --rm so containers persist after stopping.
    # This allows `task restart` to quickly resume stopped containers.
    _run_container(
        cname=cname,
        image=project_web_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        extra_args=["-p", f"{_LOCALHOST}:{port}:7860"],
    )

    # Stream initial logs and detach once the Terok Web UI server reports that it
    # is actually running. We intentionally rely on a *log marker* here
    # instead of just probing the TCP port, because podman exposes the host port
    # regardless of the state of the routed guest port.
    # Terok Web UI currently prints a stable line when the server is ready, e.g.:
    #   "Terok Web UI started"
    #
    # We treat the appearance of this as the readiness signal.
    def _web_ready(line: str) -> bool:
        """Return True if *line* contains the Terok Web UI readiness marker."""
        line = line.strip()
        if not line:
            return False

        # Primary marker: the main startup banner emitted by Terok Web UI when
        # the HTTP server is ready to accept connections.
        return "Terok Web UI started" in line

    # Follow logs until either the Terok Web UI readiness marker is seen or the
    # container exits. We deliberately do *not* time out here: as long as the
    # init script keeps making progress, the user sees the live logs and can
    # decide to Ctrl+C if it hangs.
    ready = stream_initial_logs(
        container_name=cname,
        timeout_sec=None,
        ready_check=_web_ready,
    )

    # After log streaming stops, check whether the container is actually
    # still running. This prevents false "Web UI is up" messages in cases where
    # the web process failed to start (e.g. Node error) and the container
    # exited before emitting the readiness marker.
    running = is_container_running(cname)

    if ready and running:
        color_enabled = _supports_color()
        print("\n\n>> terok: ")
        print("Web UI container is up")
    elif not running:
        print(
            "Web UI container exited before the web UI became reachable. "
            "Check the container logs for errors."
        )
        print(
            f"- Last known name: {cname}\n"
            f"- Check logs (if still available): podman logs {cname}\n"
            f"- You may need to re-run: terokctl task run-web {project.id} {task_id}"
        )
        # Exit with non-zero status to signal that the web UI did not start.
        raise SystemExit(1)

    url = f"http://{_LOCALHOST}:{port}/"
    log_command = f"podman logs -f {cname}"
    stop_command = f"podman stop {cname}"

    print(
        f"- Name: {_green(cname, color_enabled)}"
        f"\n- Routed URL: {_blue(url, color_enabled)}"
        f"\n- Check logs: {_yellow(log_command, color_enabled)}"
        f"\n- Stop:       {_red(stop_command, color_enabled)}"
    )


def _print_run_summary(workspace: Path) -> None:
    """Print a summary of changes made by the headless agent."""
    try:
        diff_stat = subprocess.check_output(
            ["git", "-C", str(workspace), "diff", "--stat", "HEAD@{1}..HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if diff_stat:
            print("\n── Changes ──────────────────────────────")
            print(diff_stat)
        else:
            print("\n── No changes committed ──────────────────")
        print(f"  Workspace: {workspace}")
    except subprocess.CalledProcessError:
        print(f"\n  Workspace: {workspace}")
    except FileNotFoundError:
        print(f"\n  Workspace: {workspace}")


def task_run_headless(request: HeadlessRunRequest) -> str:
    """Run an agent headlessly (autopilot mode) in a new task container.

    Creates a new task, prepares the agent-config directory with the provider's
    wrapper function and filtered subagents, then launches a detached container
    that runs init-ssh-and-repo.sh followed by the agent command.

    Args:
        request: All per-run options bundled in a :class:`HeadlessRunRequest`.

    Returns the task_id.
    """
    from .headless_providers import (
        CLIOverrides,
        apply_provider_config,
        build_headless_command,
        get_provider,
    )

    project = load_project(request.project_id)
    resolved = get_provider(request.provider, project)

    # Build CLI overrides from --config file and explicit flags
    cli_overrides: dict = {}
    if request.config_path:
        config_src = Path(request.config_path)
        if not config_src.is_file():
            raise SystemExit(f"Agent config file not found: {request.config_path}")
        cli_config = yaml.safe_load(config_src.read_text(encoding="utf-8")) or {}
        cli_overrides = cli_config

    # Resolve layered agent config (global → project → preset → CLI overrides)
    effective = resolve_agent_config(
        request.project_id,
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
            project=project,
            task_id=task_id,
            subagents=subagents,
            selected_agents=request.agents,
            prompt=effective_prompt,
            provider=resolved.name,
            instructions=instr_text,
        )
    )

    # Resolve unrestricted mode: CLI flag → config → default (True)
    unrestricted = request.unrestricted
    if unrestricted is None:
        cfg_val = resolve_provider_value("unrestricted", effective, resolved.name)
        unrestricted = cfg_val if cfg_val is not None else True

    # Build env and volumes
    env, volumes = build_task_env_and_volumes(project, task_id)

    # Set TEROK_UNRESTRICTED for the wrapper functions inside the container
    if unrestricted:
        env["TEROK_UNRESTRICTED"] = "1"

    # Mount agent-config dir to /home/dev/.terok
    volumes.append(f"{agent_config_dir}:/home/dev/.terok:Z")

    # Build headless command via provider registry
    headless_cmd = build_headless_command(
        resolved,
        timeout=pcfg.timeout,
        model=pcfg.model,
        max_turns=pcfg.max_turns,
    )

    # Build podman command (DETACHED)
    cname = container_name(project.id, "run", task_id)

    _run_container(
        cname=cname,
        image=project_cli_image(project.id),
        env=env,
        volumes=volumes,
        project=project,
        command=["bash", "-lc", headless_cmd],
    )

    # Update task metadata
    meta, meta_path = load_task_meta(project.id, task_id)
    meta["mode"] = "run"
    meta["provider"] = resolved.name
    meta["unrestricted"] = unrestricted
    if request.preset:
        meta["preset"] = request.preset
    meta_path.write_text(yaml.safe_dump(meta))

    color_enabled = _supports_color()

    if request.follow:
        exit_code = wait_for_exit(cname)
        _print_run_summary(task_dir / "workspace-dangerous")

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
    from .headless_providers import HEADLESS_PROVIDERS

    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id)

    mode = meta.get("mode")
    if mode != "run":
        raise SystemExit(
            f"Task {task_id} is not a headless task (mode={mode!r}). "
            f"Follow-up is only supported for autopilot (mode='run') tasks."
        )

    cname = container_name(project.id, "run", task_id)
    container_state = get_container_state(cname)
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
    resolved = HEADLESS_PROVIDERS.get(provider_name)
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
    prompt_path = agent_config_dir / "prompt.txt"
    history_path = agent_config_dir / "prompt-history.txt"
    existing = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    if existing:
        with history_path.open("a", encoding="utf-8") as hf:
            hf.write(f"{existing}\n\n---\n\n")
    prompt_path.write_text(prompt, encoding="utf-8")

    # Restart the existing container (re-runs the original bash command,
    # which reads prompt.txt and session files from the volume)
    _podman_start(cname)
    _assert_running(cname)

    # Clear previous exit_code so effective_status shows "running" until new exit
    meta["exit_code"] = None
    meta_path.write_text(yaml.safe_dump(meta))

    color_enabled = _supports_color()

    if follow:
        exit_code = wait_for_exit(cname)
        _print_run_summary(task_dir / "workspace-dangerous")

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


def task_restart(project_id: str, task_id: str, backend: str | None = None) -> None:
    """Restart a task container.

    If the container is running, stops it first and then starts it again.
    If the container exists in stopped/exited state, uses ``podman start``.
    If the container doesn't exist, delegates to task_run_cli or task_run_web.

    Note:
        Headless (mode ``"run"``) tasks cannot be auto-restarted because they
        require the original prompt and context.  Attempting to restart a
        headless task whose container no longer exists will raise ``SystemExit``.
        Re-run headless tasks manually via ``terokctl run`` with the original
        prompt instead.
    """
    project = load_project(project_id)
    meta, meta_path = load_task_meta(project.id, task_id)

    mode = meta.get("mode")
    if not mode:
        raise SystemExit(f"Task {task_id} has never been run (no mode set)")

    cname = container_name(project.id, mode, task_id)
    container_state = get_container_state(cname)

    print(f"Restarting task {project_id}/{task_id} ({mode})...")

    if container_state == "running":
        # Container is running - stop it first, then start it again
        try:
            subprocess.run(
                ["podman", "stop", "--time", str(project.shutdown_timeout), cname],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            raise SystemExit("podman not found; please install podman")
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"Failed to stop container: {e}")

    if container_state is not None:
        # Container exists (stopped/exited, or just stopped above) - start it
        _podman_start(cname)
        _assert_running(cname)

        color_enabled = _supports_color()
        print(f"Restarted task {task_id}: {_green(cname, color_enabled)}")
        if mode == "cli":
            _print_login_instructions(project_id, task_id, cname, color_enabled)
        elif mode == "web":
            port = meta.get("web_port")
            if port:
                print(f"Web UI: http://{_LOCALHOST}:{port}/")
    else:
        # Container doesn't exist - re-run the task
        print(f"Container {cname} not found, re-running task...")
        saved_preset = meta.get("preset")
        if mode == "cli":
            task_run_cli(project_id, task_id, preset=saved_preset)
        elif mode == "web":
            task_run_web(
                project_id, task_id, backend=backend or meta.get("backend"), preset=saved_preset
            )
        else:
            raise SystemExit(f"Unknown mode '{mode}' for task {task_id}")
