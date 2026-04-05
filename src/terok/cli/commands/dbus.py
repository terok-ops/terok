# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""D-Bus desktop notification subcommands.

Wires terok-dbus's :data:`COMMANDS` registry into terok's CLI as
``terok dbus notify`` and ``terok dbus subscribe``.  Handlers are
async coroutines dispatched via :func:`asyncio.run`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from terok_dbus._registry import COMMANDS, ArgDef


def _add_arg(parser: argparse.ArgumentParser, arg: ArgDef) -> None:
    """Register an :class:`ArgDef` with an argparse parser."""
    kwargs: dict = {}
    if arg.help:
        kwargs["help"] = arg.help
    for field in ("type", "default", "action", "dest", "nargs"):
        val = getattr(arg, field)
        if val is not None:
            kwargs[field] = val
    # Support slash-separated flag names (e.g. "-t/--timeout")
    names = arg.name.split("/")
    parser.add_argument(*names, **kwargs)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``dbus`` subcommand group from the terok-dbus registry."""
    p = subparsers.add_parser("dbus", help="D-Bus desktop notification tools (terok-dbus)")
    sub = p.add_subparsers(dest="dbus_cmd", required=True)

    for cmd in COMMANDS:
        sp = sub.add_parser(cmd.name, help=cmd.help)
        for arg in cmd.args:
            _add_arg(sp, arg)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle dbus commands.  Returns True if handled."""
    if getattr(args, "cmd", None) != "dbus":
        return False

    cmd_name = getattr(args, "dbus_cmd", None)
    cmd_lookup = {cmd.name: cmd for cmd in COMMANDS}
    cmd_def = cmd_lookup.get(cmd_name)
    if cmd_def is None or cmd_def.handler is None:
        return False

    # Build kwargs from ArgDef definitions
    kwargs: dict = {}
    for arg in cmd_def.args:
        key = arg.dest or arg.name.split("/")[-1].lstrip("-").replace("-", "_")
        if hasattr(args, key):
            kwargs[key] = getattr(args, key)

    try:
        asyncio.run(cmd_def.handler(**kwargs))
    except KeyboardInterrupt:
        sys.exit(130)

    return True
