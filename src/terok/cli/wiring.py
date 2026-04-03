# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Registry-driven CLI wiring for sub-package command registries.

Mounts ``CommandDef`` tuples (from terok-sandbox and terok-agent) under
argparse subparser groups.  Each package exports its commands as frozen
tuples; this module wires them into terok's namespace without
duplicating argument definitions or handler logic.

The wiring layer uses structural typing (protocols) so it works with
any ``CommandDef`` / ``ArgDef`` that exposes the expected attributes —
no coupling to a specific package's internal module.

When *config_factory* is set on a group, the factory is called at
dispatch time and the result is injected as ``cfg`` into every handler.
A startup validation pass rejects any handler that lacks a ``cfg``
parameter — fail-fast, no split-brain.

Usage::

    from terok_agent import AGENT_COMMANDS
    from terok_sandbox import GATE_COMMANDS

    wire_group(sub, "agent", AGENT_COMMANDS, help="Agent container commands")
    wire_group(
        sub, "gate", GATE_COMMANDS, help="Gate server commands", config_factory=make_sandbox_config
    )
"""

from __future__ import annotations

import argparse
import inspect
from typing import Any, Protocol, runtime_checkable

_CFG_PARAM = "cfg"


@runtime_checkable
class ArgProto(Protocol):
    """Structural contract for a CLI argument definition."""

    name: str
    help: str
    type: Any
    default: Any
    action: str | None
    dest: str | None
    nargs: int | str | None


@runtime_checkable
class CmdProto(Protocol):
    """Structural contract for a CLI command definition."""

    name: str
    help: str
    handler: Any  # Callable[..., None] | None
    args: tuple[ArgProto, ...]


def _arg_key(arg: ArgProto) -> str:
    """Derive the Python kwarg name from an argument definition."""
    return arg.dest or arg.name.lstrip("-").replace("-", "_")


def _validate_cfg_signatures(group_name: str, commands: tuple[CmdProto, ...]) -> None:
    """Fail fast if any handler in a config-injected group lacks a ``cfg`` param."""
    for cmd in commands:
        if cmd.handler is None:
            continue
        sig = inspect.signature(cmd.handler)
        if _CFG_PARAM not in sig.parameters:
            raise TypeError(
                f"Handler {cmd.handler.__name__} in group {group_name!r} "
                f"lacks required {_CFG_PARAM!r} parameter (config_factory is set)"
            )


def wire(sub: argparse._SubParsersAction, cmd: CmdProto) -> None:  # type: ignore[type-arg]
    """Add a single command definition to an argparse subparser group."""
    p = sub.add_parser(cmd.name, help=cmd.help)
    for arg in cmd.args:
        kwargs: dict = {}
        if arg.help:
            kwargs["help"] = arg.help
        if arg.type is not None:
            kwargs["type"] = arg.type
        if arg.default is not None:
            kwargs["default"] = arg.default
        if arg.action is not None:
            kwargs["action"] = arg.action
        if arg.dest is not None:
            kwargs["dest"] = arg.dest
        if arg.nargs is not None:
            kwargs["nargs"] = arg.nargs
        if getattr(arg, "required", False) and arg.name.startswith("-"):
            kwargs["required"] = True
        p.add_argument(arg.name, **kwargs)
    p.set_defaults(_wired_cmd=cmd)


def wire_group(
    sub: argparse._SubParsersAction,  # type: ignore[type-arg]
    name: str,
    commands: tuple[CmdProto, ...],
    *,
    help: str = "",
    config_factory: Any = None,
) -> None:
    """Mount a tuple of command definitions under a named subparser group.

    Creates ``<prog> <name> <subcommand>`` paths for each command in *commands*.
    When the group name is given without a subcommand, prints help.

    When *config_factory* is set, every handler in *commands* must accept a
    ``cfg`` keyword argument.  At dispatch time, ``config_factory()`` is called
    and the result injected as ``cfg``.
    """
    if config_factory is not None:
        _validate_cfg_signatures(name, commands)
    group = sub.add_parser(name, help=help)
    group_sub = group.add_subparsers(dest=f"{name}_cmd")
    for cmd in commands:
        wire(group_sub, cmd)
    group.set_defaults(_group_help=group, _config_factory=config_factory)


def wire_dispatch(args: argparse.Namespace) -> bool:
    """Dispatch a wired command.  Returns ``True`` if handled.

    Integrates with terok's existing dispatch chain: each dispatcher
    returns ``True`` if it handled the command, ``False`` to pass.
    """
    # Show group help when a group is invoked without a subcommand
    group_parser = getattr(args, "_group_help", None)
    if group_parser is not None and not hasattr(args, "_wired_cmd"):
        group_parser.print_help()
        return True

    cmd: CmdProto | None = getattr(args, "_wired_cmd", None)
    if cmd is None or cmd.handler is None:
        return False

    kwargs = {_arg_key(arg): getattr(args, _arg_key(arg), arg.default) for arg in cmd.args}

    factory = getattr(args, "_config_factory", None)
    if factory is not None:
        kwargs[_CFG_PARAM] = factory()

    cmd.handler(**kwargs)
    return True
