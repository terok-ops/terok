# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CLI wiring helpers (wire, wire_group, wire_dispatch)."""

from __future__ import annotations

import argparse
from unittest.mock import patch

from terok_sandbox.commands import ArgDef, CommandDef

from terok.cli.wiring import wire, wire_dispatch, wire_group


def _noop(**kwargs) -> None:
    """No-op handler for test commands."""


_TEST_COMMANDS = (
    CommandDef(
        name="alpha",
        help="Alpha command",
        handler=_noop,
        args=(ArgDef(name="--count", type=int, default=1, help="count"),),
    ),
    CommandDef(name="beta", help="Beta command", handler=_noop),
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


class TestWireDispatch:
    """Verify dispatch integration."""

    def test_dispatches_wired_command(self) -> None:
        """wire_dispatch() calls the handler with correct kwargs."""
        calls: list[dict] = []

        def tracking_handler(**kwargs) -> None:
            calls.append(kwargs)

        cmds = (
            CommandDef(
                name="alpha",
                help="Alpha command",
                handler=tracking_handler,
                args=(ArgDef(name="--count", type=int, default=1, help="count"),),
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
