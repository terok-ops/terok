# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the wire_group-mounted gate commands."""

from __future__ import annotations

from unittest.mock import patch

from terok.cli.wiring import wire_dispatch, wire_group


def test_gate_group_registered() -> None:
    """GATE_COMMANDS are mountable under the 'gate' prefix."""
    import argparse

    from terok_sandbox import GATE_COMMANDS

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    wire_group(sub, "gate", GATE_COMMANDS, help="Gate server commands")

    args = parser.parse_args(["gate", "status"])
    assert args.cmd == "gate"
    assert hasattr(args, "_wired_cmd")
    assert args._wired_cmd.name == "status"


def test_gate_status_dispatches() -> None:
    """'gate status' dispatches to the sandbox gate status handler."""
    import argparse

    from terok_sandbox import GATE_COMMANDS

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    wire_group(sub, "gate", GATE_COMMANDS)

    args = parser.parse_args(["gate", "status"])
    # Verify the correct handler is wired — we can't call it without a real
    # gate server, but we can check it resolves to the right function.
    from terok_sandbox.commands import _handle_gate_status

    assert args._wired_cmd.handler is _handle_gate_status


def test_gate_group_help_shown_without_subcommand() -> None:
    """Invoking 'gate' without a subcommand shows group help."""
    import argparse

    from terok_sandbox import GATE_COMMANDS

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    wire_group(sub, "gate", GATE_COMMANDS, help="Gate server commands")

    args = parser.parse_args(["gate"])
    with patch.object(args._group_help, "print_help") as mock_help:
        handled = wire_dispatch(args)

    assert handled is True
    mock_help.assert_called_once()
