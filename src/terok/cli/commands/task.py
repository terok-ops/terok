# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task management commands: list, run, stop, restart, etc."""

from __future__ import annotations

import argparse
import sys
from typing import Any

from terok_executor import PROVIDER_NAMES as _PROVIDER_NAMES

from ...lib.core.config import get_logs_partial_streaming as _get_logs_partial_streaming
from ...lib.domain.facade import (
    HeadlessRunRequest,
    LogViewOptions,
    build_images,
    project_image_exists,
    task_archive_list,
    task_archive_logs,
    task_delete,
    task_followup_headless,
    task_list,
    task_login,
    task_logs,
    task_new,
    task_rename,
    task_restart,
    task_run_cli,
    task_run_headless,
    task_run_toad,
    task_status,
    task_stop,
)
from ...lib.orchestration.tasks import resolve_task_id
from ._completers import add_project_id, add_task_id, complete_preset_names, set_completer


def _add_project_arg(parser: argparse.ArgumentParser, **kwargs: object) -> None:
    """Add a ``project_id`` positional with project-ID completion."""
    add_project_id(parser, **kwargs)


def _add_project_task_args(parser: argparse.ArgumentParser) -> None:
    """Add ``project_id`` and ``task_id`` positionals with completers."""
    add_project_id(parser)
    add_task_id(parser)


def _add_restriction_flags(parser: argparse.ArgumentParser) -> None:
    """Add mutually exclusive ``--unrestricted`` / ``--restricted`` flags."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--unrestricted",
        action="store_true",
        default=None,
        help="Run agent fully autonomous (skip all approval prompts)",
    )
    group.add_argument(
        "--restricted",
        action="store_true",
        default=None,
        help="Run agent with vendor-default permissions (ask before acting)",
    )


def _resolve_unrestricted(args: argparse.Namespace) -> bool | None:
    """Resolve ``--unrestricted`` / ``--restricted`` to a tri-state bool."""
    if getattr(args, "unrestricted", None):
        return True
    if getattr(args, "restricted", None):
        return False
    return None


def register(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    *,
    prog: str = "terok",
) -> None:
    """Register task-related subcommands.

    *prog* gates the scripting-only verbs (``task new``, ``task attach``) to
    the ``terokctl`` surface.  The human-facing ``terok`` binary exposes
    only the unified ``task run`` that creates+runs in one step.

    Note on ordering: the ``task`` group is added *before* the flat
    ``login`` shortcut so ``--help`` reads ``task`` → ``login``, matching
    the mental model of "task management, with login as a quick way to
    attach."
    """
    is_ctl = prog == "terokctl"

    # task subcommand group
    p_task = subparsers.add_parser("task", help="Manage tasks")
    tsub = p_task.add_subparsers(dest="task_cmd", required=True)

    # Unified ``task run <project>`` — creates a new task and runs it in the
    # chosen mode.  CLI (interactive) is the default; ``--mode headless``
    # runs autopilot (requires ``--prompt``); ``--mode toad`` starts the
    # Toad multi-agent TUI (browser access).
    t_run = tsub.add_parser(
        "run",
        help="Create a new task and run it (mode selects the runtime)",
    )
    _add_project_arg(t_run, help="Project ID")
    t_run.add_argument(
        "--mode",
        choices=("cli", "toad", "headless"),
        default="cli",
        help="Runtime mode: cli (interactive, default), toad (browser TUI), headless (autopilot)",
    )
    t_run.add_argument(
        "--prompt",
        default=None,
        help="Agent prompt — required when --mode=headless, ignored otherwise",
    )
    # Common flags (meaningful in all modes)
    t_run.add_argument(
        "--agent",
        dest="selected_agents",
        action="append",
        default=None,
        help="Include a non-default agent by name (repeatable)",
    )
    set_completer(
        t_run.add_argument("--preset", help="Name of a preset to apply (global or project-level)"),
        complete_preset_names,
    )
    t_run.add_argument("--name", help="Human-readable task name (slug-style, e.g. fix-auth-bug)")
    _add_restriction_flags(t_run)
    # CLI-mode attach (default: attach when stdout is a TTY).  Headless
    # already streams/follows on its own; toad returns a URL + token.
    attach_group = t_run.add_mutually_exclusive_group()
    attach_group.add_argument(
        "--attach",
        action="store_true",
        default=None,
        help="After container is ready, log into it (default on TTY, --mode=cli only)",
    )
    attach_group.add_argument(
        "--no-attach",
        dest="attach",
        action="store_false",
        help="Print login instructions instead of attaching",
    )
    # Headless-only flags (silently ignored in cli/toad modes)
    t_run.add_argument(
        "--provider",
        choices=list(_PROVIDER_NAMES),
        default=None,
        help="Agent provider (headless only; default: from project/global config, or claude)",
    )
    t_run.add_argument(
        "--config",
        dest="agent_config",
        help="Path to agent config YAML file (headless only)",
    )
    t_run.add_argument("--model", help="Model override (headless only; provider-specific)")
    t_run.add_argument("--max-turns", type=int, help="Maximum agent turns (headless only)")
    t_run.add_argument("--timeout", type=int, help="Maximum runtime in seconds (headless only)")
    t_run.add_argument(
        "--no-follow",
        action="store_true",
        help="Detach after starting, don't stream output (headless only)",
    )
    t_run.add_argument(
        "--instructions",
        metavar="FILE",
        help="Path to instructions file (headless only; overrides config stack)",
    )

    # ---- Scripting-only (terokctl) ----------------------------------------
    # ``task new`` creates metadata + workspace but does not start a
    # container.  Only useful as a building block for automation — humans
    # always want ``task run``.
    if is_ctl:
        t_new = tsub.add_parser(
            "new",
            help="Create task metadata + workspace without running it (scripting)",
        )
        _add_project_arg(t_new)
        t_new.add_argument(
            "--name", help="Human-readable task name (slug-style, e.g. fix-auth-bug)"
        )

        # ``task attach`` runs an existing task in CLI or Toad mode.
        t_attach = tsub.add_parser(
            "attach",
            help="Run an existing task in the chosen interactive mode (scripting)",
        )
        _add_project_task_args(t_attach)
        t_attach.add_argument(
            "--mode",
            choices=("cli", "toad"),
            default="cli",
            help="Runtime mode: cli (default) or toad",
        )
        t_attach.add_argument(
            "--agent",
            dest="selected_agents",
            action="append",
            default=None,
            help="Include a non-default agent by name (repeatable)",
        )
        set_completer(
            t_attach.add_argument(
                "--preset", help="Name of a preset to apply (global or project-level)"
            ),
            complete_preset_names,
        )
        _add_restriction_flags(t_attach)

    t_list = tsub.add_parser("list", help="List tasks")
    _add_project_arg(t_list)
    t_list.add_argument(
        "--status",
        dest="filter_status",
        help="Filter by task status (e.g. running, stopped, created)",
    )
    t_list.add_argument(
        "--mode",
        dest="filter_mode",
        help="Filter by task mode (e.g. cli, web, run)",
    )
    t_list.add_argument(
        "--agent",
        dest="filter_agent",
        help="Filter by agent preset name",
    )

    t_delete = tsub.add_parser("delete", help="Delete a task and its containers")
    _add_project_task_args(t_delete)

    t_stop = tsub.add_parser("stop", help="Gracefully stop a running task container")
    _add_project_task_args(t_stop)
    t_stop.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Seconds before SIGKILL (overrides project run.shutdown_timeout, default 10)",
    )

    t_restart = tsub.add_parser(
        "restart",
        help="Restart a task's container (stop if running, then start)",
    )
    _add_project_task_args(t_restart)

    t_followup = tsub.add_parser(
        "followup", help="Follow up on a completed/failed headless task with a new prompt"
    )
    _add_project_task_args(t_followup)
    t_followup.add_argument("-p", "--prompt", required=True, help="Follow-up prompt for the agent")
    t_followup.add_argument(
        "--no-follow",
        action="store_true",
        help="Detach after starting (don't stream output)",
    )

    t_rename = tsub.add_parser("rename", help="Rename a task")
    _add_project_task_args(t_rename)
    t_rename.add_argument("name", help="New task name (slug-style, e.g. fix-auth-bug)")

    t_status = tsub.add_parser("status", help="Show actual container state vs metadata")
    _add_project_task_args(t_status)

    t_logs = tsub.add_parser("logs", help="View formatted container logs for a task")
    _add_project_task_args(t_logs)
    t_logs.add_argument("-f", "--follow", action="store_true", help="Follow live output")
    t_logs.add_argument(
        "--raw", action="store_true", help="Show raw podman output (bypass formatting)"
    )
    t_logs.add_argument("--tail", type=int, default=None, help="Show only the last N lines")
    stream_group = t_logs.add_mutually_exclusive_group()
    stream_group.add_argument(
        "--stream",
        action="store_true",
        default=None,
        help="Enable partial streaming (typewriter effect, default)",
    )
    stream_group.add_argument(
        "--no-stream",
        action="store_true",
        default=None,
        help="Disable partial streaming (show coalesced messages only)",
    )

    t_archive = tsub.add_parser("archive", help="View archived (deleted) tasks")
    archive_sub = t_archive.add_subparsers(dest="archive_cmd", required=True)

    t_archive_list = archive_sub.add_parser("list", help="List archived tasks")
    _add_project_arg(t_archive_list)

    t_archive_logs = archive_sub.add_parser("logs", help="View logs from an archived task")
    _add_project_arg(t_archive_logs)
    t_archive_logs.add_argument(
        "archive_id",
        help="Archive ID prefix (timestamp, e.g. 20260305T143000Z)",
    )

    # login (top-level shortcut — registered after the task group so --help
    # lists ``task`` → ``login`` in intuitive order)
    p_login = subparsers.add_parser("login", help="Open interactive shell in a running container")
    _add_project_task_args(p_login)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle task-related commands.  Returns True if handled."""
    if args.cmd == "login":
        tid = resolve_task_id(args.project_id, args.task_id)
        task_login(args.project_id, tid)
        return True
    if args.cmd == "task":
        return _dispatch_task_sub(args)
    return False


def _cmd_task_run(args: argparse.Namespace) -> None:
    """Dispatch ``terok task run`` to the runner for the chosen mode."""
    _setup_verdict_or_exit()
    mode = getattr(args, "mode", "cli")
    if mode == "headless":
        _cmd_task_run_headless(args)
    elif mode == "toad":
        _cmd_task_run_interactive(args, runner=task_run_toad, attach=False)
    else:  # mode == "cli"
        _cmd_task_run_interactive(args, runner=task_run_cli, attach=_resolve_attach(args))


def _setup_verdict_or_exit() -> None:
    """Bounce the user to ``terok setup`` before spawning task containers.

    Mirrors ``terok-executor run``'s phase-4 exit-code contract so
    scripts see the same signal whether they drive terok or executor
    directly:

    - ``OK`` → silent return, task runner proceeds.
    - ``FIRST_RUN`` / ``STALE_AFTER_UPDATE`` / ``STAMP_CORRUPT`` →
      exit 3 with ``"Fix: terok setup"`` on stderr.
    - ``STALE_AFTER_DOWNGRADE`` → exit 4 after a multi-line refusal
      naming the downgraded packages.  Downgrades aren't tested;
      refusing is deliberate per epic terok-ai/terok#685.

    Cheap enough to call on every ``task run`` / ``task restart``
    invocation — one ``Path.is_file``, one JSON decode, a handful
    of ``importlib.metadata.version`` lookups.
    """
    from terok_sandbox import SetupVerdict, needs_setup
    from terok_sandbox.setup_stamp import _installed_versions, _read_stamp, stamp_path

    verdict = needs_setup()
    if verdict is SetupVerdict.OK:
        return

    if verdict is SetupVerdict.STALE_AFTER_DOWNGRADE:
        downgraded = _name_downgraded_packages(stamp_path(), _read_stamp, _installed_versions)
        names = ", ".join(downgraded) or "one or more packages"
        print(
            f"terok: refusing to run — downgrade detected ({names}).\n"
            "  Downgrades aren't supported; older code may not read newer state correctly.\n"
            "  Either upgrade back to the stamped version, or "
            "remove the stamp at your own risk:\n"
            f"    rm {stamp_path()}",
            file=sys.stderr,
        )
        raise SystemExit(4)

    nudge = {
        SetupVerdict.FIRST_RUN: "no setup stamp found — terok hasn't been initialised",
        SetupVerdict.STALE_AFTER_UPDATE: (
            "package versions changed since the last setup — re-run to apply"
        ),
        SetupVerdict.STAMP_CORRUPT: "setup stamp is unreadable — re-run setup to refresh it",
    }[verdict]
    print(
        f"terok: {nudge}.\n  Fix:    terok setup",
        file=sys.stderr,
    )
    raise SystemExit(3)


def _name_downgraded_packages(
    path: Any,
    read_stamp_fn: Any,
    installed_fn: Any,
) -> list[str]:
    """Return ``[pkg]`` whose installed version is < stamped, or missing entirely.

    Best-effort: if the stamp can't be re-read (race with a parallel
    setup overwrite) we return an empty list so the caller falls back
    to a generic "downgrade detected" message instead of crashing.
    """
    from packaging.version import InvalidVersion, Version

    try:
        stamped = read_stamp_fn(path)
    except Exception:  # noqa: BLE001 — diagnostic helper, never the source of truth
        return []
    installed = installed_fn()

    out: list[str] = []
    for pkg, stamp_ver in stamped.items():
        if pkg not in installed:
            out.append(f"{pkg} (uninstalled)")
            continue
        cur = installed[pkg]
        try:
            if Version(cur) < Version(stamp_ver):
                out.append(f"{pkg} {stamp_ver} → {cur}")
        except InvalidVersion:
            if cur < stamp_ver:
                out.append(f"{pkg} {stamp_ver} → {cur}")
    return out


def _cmd_task_run_interactive(args: argparse.Namespace, *, runner: Any, attach: bool) -> None:
    """Create a task, launch its container, and optionally attach to it.

    *runner* is the mode-specific launcher (CLI or Toad).  Toad prints a
    URL + token and returns; CLI under *attach* execs into ``task_login``
    once the container reports ready.
    """
    pid = args.project_id
    _ensure_project_image(pid)
    tid = task_new(pid, name=getattr(args, "name", None))
    runner(
        pid,
        tid,
        agents=getattr(args, "selected_agents", None),
        preset=getattr(args, "preset", None),
        unrestricted=_resolve_unrestricted(args),
    )
    if attach:
        # ``task_login`` calls ``os.execvp`` and never returns on success.
        task_login(pid, tid)


def _cmd_task_run_headless(args: argparse.Namespace) -> None:
    """Autopilot path: create a task and run it headlessly against the prompt.

    Cheap validation (``--prompt`` present, ``--instructions`` file readable)
    runs first so the user sees those errors before the image preflight
    potentially asks to build for several minutes.
    """
    prompt = getattr(args, "prompt", None)
    if not prompt:
        raise SystemExit("--prompt is required when --mode=headless")

    instructions_text = _read_instructions(getattr(args, "instructions", None))

    _ensure_project_image(args.project_id)

    task_run_headless(
        HeadlessRunRequest(
            project_id=args.project_id,
            prompt=prompt,
            config_path=getattr(args, "agent_config", None),
            model=getattr(args, "model", None),
            max_turns=getattr(args, "max_turns", None),
            timeout=getattr(args, "timeout", None),
            follow=not getattr(args, "no_follow", False),
            agents=getattr(args, "selected_agents", None),
            preset=getattr(args, "preset", None),
            name=getattr(args, "name", None),
            provider=getattr(args, "provider", None),
            instructions=instructions_text,
            unrestricted=_resolve_unrestricted(args),
        )
    )


def _resolve_attach(args: argparse.Namespace) -> bool:
    """Decide whether a CLI-mode ``task run`` execs into ``terok login`` on ready.

    Default is True on an interactive TTY (``docker run -it`` mental model),
    False otherwise — scripts piping the output want "start it and return"
    rather than ``execvp`` into an interactive shell.
    """
    if args.attach is not None:
        return bool(args.attach)
    return sys.stdin.isatty() and sys.stdout.isatty()


def _ensure_project_image(project_id: str) -> None:
    """Ensure the project's L2 image exists, offering to build it when missing.

    TTY → interactive ``Build now? [Y/n]`` prompt, then ``build_images()``
    inline.  Non-TTY → exit with a hint so scripts stay deterministic.
    """
    if project_image_exists(project_id):
        return

    hint = (
        f"Image for project {project_id!r} is not present. "
        f"Build it first: terok project build {project_id}"
    )
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit(hint)

    try:
        answer = (
            input(f"Image for project {project_id!r} is missing. Build now? [Y/n]: ")
            .strip()
            .lower()
        )
    except EOFError:
        # stdin closed — treat as implicit decline, print the hint.
        print()
        raise SystemExit(hint) from None
    except KeyboardInterrupt:
        # Ctrl-C stays Ctrl-C: exit 130 (conventional SIGINT), no hint.
        print()
        raise SystemExit(130) from None

    if answer in ("n", "no"):
        raise SystemExit(hint)

    build_images(project_id)


def _read_instructions(instructions_path: str | None) -> str | None:
    """Load an instructions file or return None; raises SystemExit on IO errors."""
    if not instructions_path:
        return None
    from pathlib import Path

    ipath = Path(instructions_path)
    if not ipath.is_file():
        raise SystemExit(f"Instructions file not found: {instructions_path}")
    try:
        return ipath.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SystemExit(f"Instructions file must be UTF-8 text: {instructions_path}") from exc
    except OSError as exc:
        raise SystemExit(f"Failed to read instructions file {instructions_path}: {exc}") from exc


def _dispatch_task_sub(args: argparse.Namespace) -> bool:
    """Dispatch ``task <subcommand>`` to the right handler."""
    # ``task run`` creates a new task (no task_id to resolve yet).
    if args.task_cmd == "run":
        _cmd_task_run(args)
        return True

    # ``task new`` (terokctl scripting surface) — same: no task_id yet.
    if args.task_cmd == "new":
        task_new(args.project_id, name=getattr(args, "name", None))
        return True

    pid = args.project_id
    tid = resolve_task_id(pid, args.task_id) if hasattr(args, "task_id") else ""
    if args.task_cmd == "list":
        task_list(
            pid,
            status=getattr(args, "filter_status", None),
            mode=getattr(args, "filter_mode", None),
            agent=getattr(args, "filter_agent", None),
        )
    elif args.task_cmd == "attach":
        # terokctl-only: run an existing task in the chosen interactive mode.
        runner = task_run_toad if getattr(args, "mode", "cli") == "toad" else task_run_cli
        runner(
            pid,
            tid,
            agents=getattr(args, "selected_agents", None),
            preset=getattr(args, "preset", None),
            unrestricted=_resolve_unrestricted(args),
        )
    elif args.task_cmd == "delete":
        result = task_delete(pid, tid)
        if result.warnings:
            for w in result.warnings:
                print(f"  Warning: {w}", file=sys.stderr)
            print(f"Deleted task {tid} (with warnings). Archive: terok task archive list {pid}")
        else:
            print(f"Deleted task {tid}. Archive: terok task archive list {pid}")
    elif args.task_cmd == "stop":
        task_stop(pid, tid, timeout=getattr(args, "timeout", None))
    elif args.task_cmd == "restart":
        _setup_verdict_or_exit()
        task_restart(pid, tid)
    elif args.task_cmd == "followup":
        _setup_verdict_or_exit()
        task_followup_headless(
            pid,
            tid,
            args.prompt,
            follow=not getattr(args, "no_follow", False),
        )
    elif args.task_cmd == "rename":
        task_rename(pid, tid, args.name)
    elif args.task_cmd == "status":
        task_status(pid, tid)
    elif args.task_cmd == "logs":
        # Resolve streaming: CLI flag → config → default (True)
        if getattr(args, "no_stream", None):
            stream = False
        elif getattr(args, "stream", None):
            stream = True
        else:
            stream = _get_logs_partial_streaming()
        task_logs(
            pid,
            tid,
            LogViewOptions(
                follow=getattr(args, "follow", False),
                raw=getattr(args, "raw", False),
                tail=getattr(args, "tail", None),
                streaming=stream,
            ),
        )
    elif args.task_cmd == "archive":
        return _dispatch_archive_sub(args)
    else:
        return False
    return True


def _dispatch_archive_sub(args: argparse.Namespace) -> bool:
    """Dispatch ``task archive <subcommand>``."""
    if args.archive_cmd == "list":
        task_archive_list(args.project_id)
    elif args.archive_cmd == "logs":
        log_file = task_archive_logs(args.project_id, args.archive_id)
        if log_file is None:
            raise SystemExit(
                f"No archived logs found for prefix {args.archive_id!r}. "
                f"Use 'terok task archive list {args.project_id}' to see available archives."
            )
        with log_file.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                print(line, end="")
    else:
        return False
    return True
