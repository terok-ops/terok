# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the top-level ``terok auth`` command."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

from terok.cli.commands.auth import dispatch, register


def test_register_parses_provider_and_project() -> None:
    """``auth <provider> <project>`` parses as two positional arguments."""
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["auth", "claude", "myproj"])
    assert args.cmd == "auth"
    assert args.provider == "claude"
    assert args.project_id == "myproj"


def test_register_rejects_unknown_provider() -> None:
    """``auth <unknown>`` exits with argparse's choices error."""
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    try:
        parser.parse_args(["auth", "not-a-provider", "p"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover — defensive: argparse must exit on invalid choice
        raise AssertionError("expected SystemExit")


def test_dispatch_ignores_other_commands() -> None:
    """Dispatch returns False for unrelated namespaces."""
    assert dispatch(argparse.Namespace(cmd="task")) is False


def test_dispatch_runs_install_check_then_authenticate() -> None:
    """``auth`` loads the project, verifies the agent, then authenticates."""
    fake_project = SimpleNamespace(id="p1")
    args = argparse.Namespace(cmd="auth", provider="claude", project_id="p1")
    with (
        patch("terok.cli.commands.auth.load_project", return_value=fake_project),
        patch("terok.cli.commands.auth.require_agent_installed") as mock_check,
        patch("terok.cli.commands.auth.authenticate") as mock_auth,
    ):
        assert dispatch(args) is True

    # The handler passes the *loaded* project object (not the raw id) into
    # the installation check, and the noun label drives error messages.
    mock_check.assert_called_once_with(fake_project, "claude", noun="Provider")
    mock_auth.assert_called_once_with("p1", "claude")
