# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terokctl credentials`` CLI subcommand."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.credentials import dispatch, register
from tests.testfs import MOCK_BASE

MOCK_PROXY_SOCKET = MOCK_BASE / "run" / "credential-proxy.sock"
MOCK_PROXY_DB = MOCK_BASE / "proxy" / "credentials.db"
MOCK_PROXY_ROUTES = MOCK_BASE / "proxy" / "routes.json"

_MOD = "terok.cli.commands.credentials"


def _make_parser() -> argparse.ArgumentParser:
    """Build a minimal parser with the credentials subcommand registered."""
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register(sub)
    return parser


def _make_status(*, running: bool = False, mode: str = "none") -> MagicMock:
    """Build a proxy status mock."""
    status = MagicMock()
    status.mode = mode
    status.running = running
    status.socket_path = MOCK_PROXY_SOCKET
    status.db_path = MOCK_PROXY_DB
    status.routes_path = MOCK_PROXY_ROUTES
    status.routes_configured = 3
    status.credentials_stored = ("claude", "gh")
    return status


class TestCredentialsRegister:
    """Verify subcommand registration."""

    def test_credentials_subcommands_registered(self) -> None:
        """All credentials subcommands are parseable."""
        parser = _make_parser()
        for sub in ("install", "uninstall", "start", "stop", "status"):
            args = parser.parse_args(["credentials", sub])
            assert args.cmd == "credentials"
            assert args.credentials_cmd == sub


class TestCredentialsDispatch:
    """Verify dispatch routing."""

    def test_dispatch_ignores_other_commands(self) -> None:
        """dispatch returns False for non-credentials commands."""
        args = argparse.Namespace(cmd="task")
        assert dispatch(args) is False

    @patch(f"{_MOD}.is_proxy_systemd_available", return_value=False)
    @patch(f"{_MOD}.get_proxy_status")
    def test_dispatch_status(self, mock_status, mock_sd, capsys) -> None:
        """'credentials status' prints status info."""
        mock_status.return_value = _make_status()
        parser = _make_parser()
        args = parser.parse_args(["credentials", "status"])
        assert dispatch(args) is True
        out = capsys.readouterr().out
        assert "stopped" in out
        assert "claude" in out

    @patch(f"{_MOD}.start_proxy")
    @patch("terok_agent.ensure_proxy_routes", create=True)
    @patch(f"{_MOD}.is_proxy_running", return_value=False)
    def test_dispatch_start(self, mock_running, mock_routes, mock_start, capsys) -> None:
        """'credentials start' generates routes and starts the daemon."""
        parser = _make_parser()
        args = parser.parse_args(["credentials", "start"])
        assert dispatch(args) is True
        mock_routes.assert_called_once()
        mock_start.assert_called_once()
        assert "started" in capsys.readouterr().out

    @patch(f"{_MOD}.is_proxy_running", return_value=True)
    def test_dispatch_start_already_running(self, mock_running) -> None:
        """'credentials start' exits if already running."""
        parser = _make_parser()
        args = parser.parse_args(["credentials", "start"])
        with pytest.raises(SystemExit):
            dispatch(args)

    @patch(f"{_MOD}.stop_proxy")
    @patch(f"{_MOD}.is_proxy_running", return_value=True)
    def test_dispatch_stop(self, mock_running, mock_stop, capsys) -> None:
        """'credentials stop' calls stop_proxy."""
        parser = _make_parser()
        args = parser.parse_args(["credentials", "stop"])
        assert dispatch(args) is True
        mock_stop.assert_called_once()

    @patch(f"{_MOD}.is_proxy_running", return_value=False)
    def test_dispatch_stop_not_running(self, mock_running, capsys) -> None:
        """'credentials stop' prints info when not running."""
        parser = _make_parser()
        args = parser.parse_args(["credentials", "stop"])
        assert dispatch(args) is True
        assert "not running" in capsys.readouterr().out

    @patch(f"{_MOD}.install_proxy_systemd")
    @patch("terok_agent.ensure_proxy_routes", create=True)
    @patch(f"{_MOD}.is_proxy_systemd_available", return_value=True)
    def test_dispatch_install(self, mock_sd, mock_routes, mock_install, capsys) -> None:
        """'credentials install' installs systemd units."""
        parser = _make_parser()
        args = parser.parse_args(["credentials", "install"])
        assert dispatch(args) is True
        mock_install.assert_called_once()
        assert "installed" in capsys.readouterr().out

    @patch(f"{_MOD}.is_proxy_systemd_available", return_value=False)
    def test_dispatch_install_no_systemd(self, mock_sd) -> None:
        """'credentials install' fails without systemd."""
        parser = _make_parser()
        args = parser.parse_args(["credentials", "install"])
        with pytest.raises(SystemExit):
            dispatch(args)

    @patch(f"{_MOD}.uninstall_proxy_systemd")
    @patch(f"{_MOD}.is_proxy_systemd_available", return_value=True)
    def test_dispatch_uninstall(self, mock_sd, mock_uninstall, capsys) -> None:
        """'credentials uninstall' removes systemd units."""
        parser = _make_parser()
        args = parser.parse_args(["credentials", "uninstall"])
        assert dispatch(args) is True
        mock_uninstall.assert_called_once()

    def test_dispatch_unknown_subcommand(self) -> None:
        """Unknown credentials subcommand returns False."""
        args = argparse.Namespace(cmd="credentials", credentials_cmd="bogus")
        assert dispatch(args) is False
