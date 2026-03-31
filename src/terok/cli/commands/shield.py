# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield egress firewall management commands.

Uses the ``terok_shield`` command registry to build subcommands.
Commands that need a container take positional ``project_id task_id``
(same convention as ``terokctl task …``), which are resolved to a
container name + task directory for the registry handler.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from terok_sandbox import make_shield
from terok_shield import COMMANDS, ArgDef, CommandDef, ExecError


def _add_arg(parser: argparse.ArgumentParser, arg: ArgDef) -> None:
    """Register an :class:`ArgDef` with an argparse parser."""
    kwargs: dict = {}
    if arg.help:
        kwargs["help"] = arg.help
    for field in ("type", "default", "action", "dest", "nargs"):
        val = getattr(arg, field)
        if val is not None:
            kwargs[field] = val
    parser.add_argument(arg.name, **kwargs)


def _resolve_task(project_id: str, task_id: str) -> tuple[str, Path]:
    """Resolve project+task to (container_name, task_dir).

    Returns:
        Tuple of (container_name, task_dir) for constructing a Shield.

    Raises:
        ValueError: If the task has never been run (no container exists).
    """
    from ...lib.core.projects import load_project
    from ...lib.orchestration.tasks import container_name, load_task_meta

    project = load_project(project_id)
    meta, _ = load_task_meta(project.id, task_id)
    mode = meta.get("mode")
    if mode is None:
        raise ValueError(
            f"Task {task_id} in project {project_id!r} has never been run — no container exists"
        )
    cname = container_name(project.id, mode, task_id)
    task_dir = project.tasks_root / str(task_id)
    return cname, task_dir


def _extract_handler_kwargs(args: argparse.Namespace, cmd_def: CommandDef) -> dict:
    """Extract keyword arguments for a registry handler from parsed args."""
    kwargs: dict = {}
    for arg in cmd_def.args:
        if arg.name == "container":
            continue
        key = arg.dest or arg.name.lstrip("-").replace("-", "_")
        if hasattr(args, key):
            kwargs[key] = getattr(args, key)
    return kwargs


_DESIRED_STATE_FILENAME = "shield_desired_state"


def _persist_desired_state(cmd_name: str, task_dir: Path, kwargs: dict) -> None:
    """Write desired shield state after a successful ``up`` or ``down`` command.

    Persists the operator's intent so ``on_task_restart: retain`` can
    restore the correct state after a container stop/start cycle.
    Best-effort: OSError is logged but swallowed so the shield command
    itself stays successful.
    """
    if cmd_name == "up":
        value = "up"
    elif cmd_name == "down":
        value = "down_all" if kwargs.get("allow_all") else "down"
    else:
        return
    try:
        (task_dir / _DESIRED_STATE_FILENAME).write_text(f"{value}\n")
    except OSError as exc:
        print(
            f"Warning: could not persist {_DESIRED_STATE_FILENAME} to {task_dir}: {exc}",
            file=sys.stderr,
        )


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``shield`` subcommand group from the registry."""
    p = subparsers.add_parser("shield", help="Manage egress firewall (terok-shield)")
    sub = p.add_subparsers(dest="shield_cmd", required=True)

    for cmd in COMMANDS:
        if cmd.standalone_only:
            continue

        sp = sub.add_parser(cmd.name, help=cmd.help)

        # Commands that need a container get positional project_id + task_id,
        # matching the ``terokctl task …`` convention.  Commands with an
        # *optional* container arg (like ``status``) get nargs="?" so they
        # work both with and without a task target.
        if cmd.needs_container:
            sp.add_argument("project_id", help="Project ID")
            sp.add_argument("task_id", help="Task ID")
        elif any(a.name == "container" for a in cmd.args):
            sp.add_argument("project_id", nargs="?", help="Project ID")
            sp.add_argument("task_id", nargs="?", help="Task ID")

        for arg in cmd.args:
            if arg.name == "container":
                continue
            _add_arg(sp, arg)

    # Manually register setup (standalone_only in registry, needs subprocess passthrough)
    p_setup = sub.add_parser("setup", help="Install global OCI hooks for shield")
    p_setup.add_argument("--root", action="store_true", help="System-wide (sudo)")
    p_setup.add_argument("--user", action="store_true", help="User-local")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle shield commands.  Returns True if handled."""
    if args.cmd != "shield":
        return False

    cmd_name = args.shield_cmd

    # setup is standalone_only and needs subprocess passthrough (no registry handler)
    if cmd_name == "setup":
        from terok_sandbox import run_setup as shield_run_setup

        shield_run_setup(root=args.root, user=args.user)
        return True

    cmd_lookup = {cmd.name: cmd for cmd in COMMANDS if not cmd.standalone_only}
    cmd_def = cmd_lookup.get(cmd_name)
    if cmd_def is None or cmd_def.handler is None:
        return False

    project_id = getattr(args, "project_id", None)
    task_id = getattr(args, "task_id", None)
    if (project_id is None) != (task_id is None):
        print("Error: provide both <project_id> and <task_id>, or neither", file=sys.stderr)
        sys.exit(1)
    has_task = project_id is not None and task_id is not None

    try:
        if has_task:
            cname, task_dir = _resolve_task(args.project_id, args.task_id)
            shield = make_shield(task_dir)
            kwargs = _extract_handler_kwargs(args, cmd_def)
            if cmd_def.needs_container:
                cmd_def.handler(shield, cname, **kwargs)
                _persist_desired_state(cmd_name, task_dir, kwargs)
            else:
                # Optional container arg (e.g. ``status <project> <task>``)
                kwargs["container"] = cname
                cmd_def.handler(shield, **kwargs)
        else:
            import tempfile

            with tempfile.TemporaryDirectory() as tmp:
                shield = make_shield(Path(tmp))
                kwargs = _extract_handler_kwargs(args, cmd_def)
                cmd_def.handler(shield, **kwargs)
    except ExecError as exc:
        print(
            f"Error: shield operation failed for task {args.task_id}: {exc}"
            if has_task
            else f"Error: shield operation failed: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    return True
