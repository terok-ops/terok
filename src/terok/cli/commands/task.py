# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Task management commands: new, list, run-cli, start, etc."""

from __future__ import annotations

import argparse
import sys

from terok_executor import PROVIDER_NAMES as _PROVIDER_NAMES

from ...lib.core.config import get_logs_partial_streaming as _get_logs_partial_streaming
from ...lib.domain.facade import (
    HeadlessRunRequest,
    LogViewOptions,
    get_tasks as _get_tasks,
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
from ._completers import complete_project_ids as _complete_project_ids, set_completer


def _complete_task_ids(
    prefix: str, parsed_args: argparse.Namespace, **kwargs: object
) -> list[str]:  # pragma: no cover
    """Return task IDs matching *prefix* for argcomplete."""
    project_id = getattr(parsed_args, "project_id", None)
    if not project_id:
        return []
    try:
        tids = [t.task_id for t in _get_tasks(project_id) if t.task_id]
    except Exception:
        return []
    if prefix:
        tids = [t for t in tids if t.startswith(prefix)]
    return tids


def _add_project_arg(parser: argparse.ArgumentParser, **kwargs: object) -> None:
    """Add a ``project_id`` positional with project-ID completion."""
    set_completer(parser.add_argument("project_id", **kwargs), _complete_project_ids)


def _add_project_task_args(parser: argparse.ArgumentParser) -> None:
    """Add ``project_id`` and ``task_id`` positionals with completers."""
    _add_project_arg(parser)
    set_completer(parser.add_argument("task_id"), _complete_task_ids)


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


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register task-related subcommands.

    Note on ordering: the ``task`` group is added *before* the flat
    ``login`` shortcut so ``--help`` reads ``task`` → ``login``, matching
    the mental model of "task management, with login as a quick way to
    attach."
    """
    # task subcommand group
    p_task = subparsers.add_parser("task", help="Manage tasks")
    tsub = p_task.add_subparsers(dest="task_cmd", required=True)

    # task run (headless autopilot — replaces former top-level `run`)
    t_run = tsub.add_parser("run", help="Run an agent headlessly in a new task (autopilot mode)")
    _add_project_arg(t_run, help="Project ID")
    t_run.add_argument("prompt", help="Task prompt for the agent")
    t_run.add_argument(
        "--provider",
        choices=list(_PROVIDER_NAMES),
        default=None,
        help="Agent provider (default: from project/global config, or claude)",
    )
    t_run.add_argument("--config", dest="agent_config", help="Path to agent config YAML file")
    t_run.add_argument("--preset", help="Name of a preset to apply (global or project-level)")
    t_run.add_argument("--model", help="Model override (provider-specific)")
    t_run.add_argument("--max-turns", type=int, help="Maximum agent turns")
    t_run.add_argument("--timeout", type=int, help="Maximum runtime in seconds")
    t_run.add_argument(
        "--no-follow",
        action="store_true",
        help="Detach after starting (don't stream output)",
    )
    t_run.add_argument(
        "--agent",
        dest="selected_agents",
        action="append",
        default=None,
        help="Include a non-default agent by name (repeatable, Claude only)",
    )
    t_run.add_argument("--name", help="Human-readable task name (slug-style, e.g. fix-auth-bug)")
    t_run.add_argument(
        "--instructions",
        metavar="FILE",
        help="Path to instructions file (overrides config stack)",
    )
    _add_restriction_flags(t_run)

    t_new = tsub.add_parser("new", help="Create a new task")
    _add_project_arg(t_new)
    t_new.add_argument("--name", help="Human-readable task name (slug-style, e.g. fix-auth-bug)")

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

    t_run_cli = tsub.add_parser("run-cli", help="Run task in CLI (codex agent) mode")
    _add_project_task_args(t_run_cli)
    t_run_cli.add_argument(
        "--agent",
        dest="selected_agents",
        action="append",
        default=None,
        help="Include a non-default agent by name (repeatable)",
    )
    t_run_cli.add_argument("--preset", help="Name of a preset to apply (global or project-level)")
    _add_restriction_flags(t_run_cli)

    t_run_toad = tsub.add_parser("run-toad", help="Run Toad multi-agent TUI (browser access)")
    _add_project_task_args(t_run_toad)
    t_run_toad.add_argument(
        "--agent",
        dest="selected_agents",
        action="append",
        default=None,
        help="Include a non-default agent by name (repeatable)",
    )
    t_run_toad.add_argument("--preset", help="Name of a preset to apply (global or project-level)")
    _add_restriction_flags(t_run_toad)

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

    t_restart = tsub.add_parser("restart", help="Restart a stopped task or re-run if gone")
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

    t_start = tsub.add_parser(
        "start",
        help="Create a new task and immediately run it (default: CLI mode)",
    )
    _add_project_arg(t_start)
    t_start.add_argument(
        "--toad",
        action="store_true",
        help="Start Toad multi-agent TUI (browser access)",
    )
    t_start.add_argument(
        "--agent",
        dest="selected_agents",
        action="append",
        default=None,
        help="Include a non-default agent by name (repeatable)",
    )
    t_start.add_argument("--preset", help="Name of a preset to apply (global or project-level)")
    t_start.add_argument("--name", help="Human-readable task name (slug-style, e.g. fix-auth-bug)")
    _add_restriction_flags(t_start)

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
    """Handle ``terok task run`` (headless autopilot)."""
    instructions_text = None
    instructions_path = getattr(args, "instructions", None)
    if instructions_path:
        from pathlib import Path

        ipath = Path(instructions_path)
        if not ipath.is_file():
            raise SystemExit(f"Instructions file not found: {instructions_path}")
        try:
            instructions_text = ipath.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SystemExit(f"Instructions file must be UTF-8 text: {instructions_path}") from exc
        except OSError as exc:
            raise SystemExit(
                f"Failed to read instructions file {instructions_path}: {exc}"
            ) from exc

    task_run_headless(
        HeadlessRunRequest(
            project_id=args.project_id,
            prompt=args.prompt,
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


def _dispatch_task_sub(args: argparse.Namespace) -> bool:
    """Dispatch ``task <subcommand>`` to the right handler."""
    # `task run` — autopilot — must resolve before we try ``resolve_task_id``
    # (it creates a new task and takes no ``task_id``).
    if args.task_cmd == "run":
        _cmd_task_run(args)
        return True

    pid = args.project_id
    tid = resolve_task_id(pid, args.task_id) if hasattr(args, "task_id") else ""
    if args.task_cmd == "new":
        task_new(pid, name=getattr(args, "name", None))
    elif args.task_cmd == "list":
        task_list(
            pid,
            status=getattr(args, "filter_status", None),
            mode=getattr(args, "filter_mode", None),
            agent=getattr(args, "filter_agent", None),
        )
    elif args.task_cmd == "run-cli":
        task_run_cli(
            pid,
            tid,
            agents=getattr(args, "selected_agents", None),
            preset=getattr(args, "preset", None),
            unrestricted=_resolve_unrestricted(args),
        )
    elif args.task_cmd == "run-toad":
        task_run_toad(
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
        task_restart(pid, tid)
    elif args.task_cmd == "followup":
        task_followup_headless(
            pid,
            tid,
            args.prompt,
            follow=not getattr(args, "no_follow", False),
        )
    elif args.task_cmd == "start":
        task_id = task_new(pid, name=getattr(args, "name", None))
        selected = getattr(args, "selected_agents", None)
        preset = getattr(args, "preset", None)
        restriction = _resolve_unrestricted(args)
        if getattr(args, "toad", False):
            task_run_toad(pid, task_id, agents=selected, preset=preset, unrestricted=restriction)
        else:
            task_run_cli(pid, task_id, agents=selected, preset=preset, unrestricted=restriction)
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
