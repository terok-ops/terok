# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for D-Bus CLI commands (registry-driven dispatch)."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest
from terok_dbus._registry import CommandDef

from terok.cli.commands.dbus import dispatch, register


@pytest.fixture()
def dbus_parser() -> argparse.ArgumentParser:
    """Return an argument parser with the dbus subcommands registered."""
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    return parser


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(
            ["dbus", "notify", "Hello"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "", "timeout": -1},
            id="notify-summary-only",
        ),
        pytest.param(
            ["dbus", "notify", "Hello", "World"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "World", "timeout": -1},
            id="notify-with-body",
        ),
        pytest.param(
            ["dbus", "notify", "Hello", "-t", "5000"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "", "timeout": 5000},
            id="notify-short-timeout",
        ),
        pytest.param(
            ["dbus", "notify", "Hello", "--timeout", "3000"],
            {"dbus_cmd": "notify", "summary": "Hello", "body": "", "timeout": 3000},
            id="notify-long-timeout",
        ),
        pytest.param(
            ["dbus", "subscribe"],
            {"dbus_cmd": "subscribe"},
            id="subscribe",
        ),
    ],
)
def test_register_parses_dbus_subcommands(
    dbus_parser: argparse.ArgumentParser,
    argv: list[str],
    expected: dict[str, object],
) -> None:
    """Registered dbus subcommands parse the expected argument shapes."""
    args = dbus_parser.parse_args(argv)
    for key, value in expected.items():
        assert getattr(args, key) == value


def test_dispatch_returns_false_for_non_dbus_commands() -> None:
    """Dispatch ignores non-dbus CLI namespaces."""
    assert not dispatch(argparse.Namespace(cmd="project"))


def _real_args(name: str) -> tuple:
    """Return the ArgDef tuple for a real COMMANDS entry by name."""
    from terok_dbus._registry import COMMANDS as REAL_COMMANDS

    return next(c.args for c in REAL_COMMANDS if c.name == name)


def test_dispatch_notify() -> None:
    """``dbus notify`` dispatches to the notify handler with correct kwargs."""
    calls: list[dict] = []

    async def stub_notify(**kwargs: object) -> None:
        calls.append(kwargs)

    stub_commands = (
        CommandDef(name="notify", handler=stub_notify, args=_real_args("notify")),
        CommandDef(name="subscribe", handler=None),
    )
    args = argparse.Namespace(
        cmd="dbus", dbus_cmd="notify", summary="Test", body="Body", timeout=5000
    )
    with patch("terok.cli.commands.dbus.COMMANDS", stub_commands):
        assert dispatch(args)
    assert calls == [{"summary": "Test", "body": "Body", "timeout": 5000}]


def test_dispatch_subscribe() -> None:
    """``dbus subscribe`` dispatches to the subscribe handler."""
    calls: list[dict] = []

    async def stub_subscribe(**kwargs: object) -> None:
        calls.append(kwargs)

    stub_commands = (
        CommandDef(name="notify", handler=None),
        CommandDef(name="subscribe", handler=stub_subscribe, args=_real_args("subscribe")),
    )
    args = argparse.Namespace(cmd="dbus", dbus_cmd="subscribe")
    with patch("terok.cli.commands.dbus.COMMANDS", stub_commands):
        assert dispatch(args)
    assert calls == [{}]


def test_dispatch_unknown_subcommand_returns_false() -> None:
    """Unknown dbus subcommand returns False (not handled)."""
    assert not dispatch(argparse.Namespace(cmd="dbus", dbus_cmd="nonexistent"))
