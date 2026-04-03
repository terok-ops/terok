# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI wiring helpers (wire, wire_group, wire_dispatch)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from terok.cli.wiring import wire, wire_dispatch, wire_group

# ── Lightweight test doubles matching the ArgProto/CmdProto contracts ────


@dataclass(frozen=True)
class _Arg:
    """Minimal ArgDef-compatible test double."""

    name: str
    help: str = ""
    type: Any = None
    default: Any = None
    action: str | None = None
    dest: str | None = None
    nargs: int | str | None = None


@dataclass(frozen=True)
class _Cmd:
    """Minimal CommandDef-compatible test double."""

    name: str
    help: str = ""
    handler: Any = None
    args: tuple[_Arg, ...] = ()


def _noop(**kwargs) -> None:
    """No-op handler for test commands."""


def _noop_with_cfg(*, cfg=None, **kwargs) -> None:
    """No-op handler that accepts cfg for config-injected groups."""


_TEST_COMMANDS = (
    _Cmd(
        name="alpha",
        help="Alpha command",
        handler=_noop,
        args=(_Arg(name="--count", type=int, default=1, help="count"),),
    ),
    _Cmd(name="beta", help="Beta command", handler=_noop),
)

_CFG_COMMANDS = (
    _Cmd(
        name="alpha",
        help="Alpha command",
        handler=_noop_with_cfg,
        args=(_Arg(name="--count", type=int, default=1, help="count"),),
    ),
    _Cmd(name="beta", help="Beta command", handler=_noop_with_cfg),
)


class TestWire:
    """Verify single-command wiring."""

    def test_wire_registers_subparser(self) -> None:
        """wire() creates a subparser with the command's name."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire(sub, _TEST_COMMANDS[0])

        args = parser.parse_args(["alpha", "--count", "5"])
        assert args.cmd == "alpha"
        assert args.count == 5
        assert args._wired_cmd is _TEST_COMMANDS[0]

    def test_wire_stores_command_ref(self) -> None:
        """wire() stores the CommandDef on the namespace via _wired_cmd."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire(sub, _TEST_COMMANDS[1])

        args = parser.parse_args(["beta"])
        assert args._wired_cmd.name == "beta"


class TestWireGroup:
    """Verify grouped command mounting."""

    def test_creates_group_subparser(self) -> None:
        """wire_group() creates a group parser with nested subparsers."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "test", _TEST_COMMANDS, help="Test group")

        args = parser.parse_args(["test", "alpha", "--count", "3"])
        assert args.cmd == "test"
        assert args._wired_cmd.name == "alpha"
        assert args.count == 3

    def test_group_without_subcommand_gets_help_default(self) -> None:
        """wire_group() stores the group parser for help display."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "test", _TEST_COMMANDS)

        args = parser.parse_args(["test"])
        assert hasattr(args, "_group_help")

    def test_config_factory_injects_at_dispatch(self) -> None:
        """wire_group(config_factory=...) causes dispatch to inject cfg."""
        received: list[dict] = []

        def handler(*, cfg=None, count=1) -> None:
            received.append({"cfg": cfg, "count": count})

        cmds = (
            _Cmd(
                name="alpha",
                handler=handler,
                args=(_Arg(name="--count", type=int, default=1, help="count"),),
            ),
        )
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", cmds, config_factory=lambda: "injected")

        args = parser.parse_args(["grp", "alpha", "--count", "3"])
        wire_dispatch(args)

        assert received == [{"cfg": "injected", "count": 3}]

    def test_no_factory_does_not_inject_cfg(self) -> None:
        """Without config_factory, handler receives only CLI args."""
        received: list[dict] = []

        def handler(**kwargs) -> None:
            received.append(kwargs)

        cmds = (
            _Cmd(
                name="alpha",
                handler=handler,
                args=(_Arg(name="--count", type=int, default=1, help="count"),),
            ),
        )
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", cmds)

        args = parser.parse_args(["grp", "alpha", "--count", "2"])
        wire_dispatch(args)

        assert received == [{"count": 2}]
        assert "cfg" not in received[0]

    def test_accepts_handlers_without_cfg_at_registration(self) -> None:
        """wire_group() does not reject handlers at registration time.

        Validation is deferred to dispatch so PRs can be merged independently.
        """
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", _TEST_COMMANDS, config_factory=lambda: None)
        # No error at registration — validated at dispatch


class TestWireDispatch:
    """Verify dispatch integration."""

    def test_dispatches_wired_command(self) -> None:
        """wire_dispatch() calls the handler with correct kwargs."""
        calls: list[dict] = []

        def tracking_handler(**kwargs) -> None:
            calls.append(kwargs)

        cmds = (
            _Cmd(
                name="alpha",
                help="Alpha command",
                handler=tracking_handler,
                args=(_Arg(name="--count", type=int, default=1, help="count"),),
            ),
        )
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", cmds)

        args = parser.parse_args(["grp", "alpha", "--count", "7"])
        handled = wire_dispatch(args)

        assert handled is True
        assert calls == [{"count": 7}]

    def test_returns_false_for_unwired_command(self) -> None:
        """wire_dispatch() returns False when no _wired_cmd is set."""
        args = argparse.Namespace(cmd="unknown")
        assert wire_dispatch(args) is False

    def test_shows_group_help_without_subcommand(self) -> None:
        """wire_dispatch() prints group help when subcommand is missing."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", _TEST_COMMANDS)

        args = parser.parse_args(["grp"])
        with patch.object(args._group_help, "print_help") as mock_help:
            handled = wire_dispatch(args)

        assert handled is True
        mock_help.assert_called_once()

    def test_injects_cfg_when_factory_set(self) -> None:
        """wire_dispatch() injects cfg from config_factory into handler kwargs."""
        received: list[dict] = []

        def tracking_handler(*, cfg=None, count=1) -> None:
            received.append({"cfg": cfg, "count": count})

        cmds = (
            _Cmd(
                name="alpha",
                handler=tracking_handler,
                args=(_Arg(name="--count", type=int, default=1, help="count"),),
            ),
        )
        factory = lambda: "injected-config"  # noqa: E731

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", cmds, config_factory=factory)

        args = parser.parse_args(["grp", "alpha", "--count", "5"])
        wire_dispatch(args)

        assert received == [{"cfg": "injected-config", "count": 5}]

    def test_no_cfg_without_factory(self) -> None:
        """wire_dispatch() does not inject cfg when no config_factory is set."""
        received: list[dict] = []

        def tracking_handler(**kwargs) -> None:
            received.append(kwargs)

        cmds = (_Cmd(name="alpha", handler=tracking_handler),)

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", cmds)

        args = parser.parse_args(["grp", "alpha"])
        wire_dispatch(args)

        assert received == [{}]

    def test_missing_cfg_param_raises_at_dispatch(self) -> None:
        """wire_dispatch() raises TypeError when handler lacks cfg but factory is set."""

        def no_cfg_handler(*, count: int = 1) -> None:
            pass

        cmds = (
            _Cmd(
                name="alpha",
                handler=no_cfg_handler,
                args=(_Arg(name="--count", type=int, default=1, help="count"),),
            ),
        )
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", cmds, config_factory=lambda: None)

        args = parser.parse_args(["grp", "alpha", "--count", "1"])
        with pytest.raises(TypeError, match="lacks required.*cfg"):
            wire_dispatch(args)

    def test_var_keyword_handler_accepted(self) -> None:
        """Handlers with **kwargs accept cfg injection without explicit param."""
        received: list[dict] = []

        def kwargs_handler(**kwargs) -> None:
            received.append(kwargs)

        cmds = (_Cmd(name="alpha", handler=kwargs_handler),)

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "grp", cmds, config_factory=lambda: "injected")

        args = parser.parse_args(["grp", "alpha"])
        wire_dispatch(args)

        assert received == [{"cfg": "injected"}]


class TestAgentCommandsRegistered:
    """Verify AGENT_COMMANDS can be mounted and dispatched."""

    def test_agent_run_parseable(self) -> None:
        """'agent run claude .' parses correctly."""
        from terok_agent import AGENT_COMMANDS

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "agent", AGENT_COMMANDS)

        args = parser.parse_args(["agent", "run", "claude", "."])
        assert args._wired_cmd.name == "run"
        assert args.agent == "claude"
        assert args.repo == "."

    def test_agent_agents_parseable(self) -> None:
        """'agent agents --all' parses correctly."""
        from terok_agent import AGENT_COMMANDS

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        wire_group(sub, "agent", AGENT_COMMANDS)

        args = parser.parse_args(["agent", "agents", "--all"])
        assert args._wired_cmd.name == "agents"
        assert args.show_all is True
