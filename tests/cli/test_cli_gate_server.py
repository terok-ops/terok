# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for gate-server CLI commands."""

import unittest
import unittest.mock
from io import StringIO

from terok.cli.commands.gate_server import (
    _cmd_install,
    _cmd_start,
    _cmd_status,
    _cmd_stop,
    _cmd_uninstall,
)
from terok.lib.security.gate_server import GateServerStatus


class TestCmdInstall(unittest.TestCase):
    """Tests for gate-server install."""

    @unittest.mock.patch("terok.cli.commands.gate_server.install_systemd_units")
    @unittest.mock.patch("terok.cli.commands.gate_server.is_systemd_available", return_value=True)
    def test_install(
        self, _mock_avail: unittest.mock.Mock, mock_install: unittest.mock.Mock
    ) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_install()
        mock_install.assert_called_once()
        self.assertIn("installed", out.getvalue())

    @unittest.mock.patch("terok.cli.commands.gate_server.is_systemd_available", return_value=False)
    def test_install_no_systemd(self, _mock: unittest.mock.Mock) -> None:
        """Install exits with error when systemd is unavailable."""
        with self.assertRaises(SystemExit):
            _cmd_install()


class TestCmdUninstall(unittest.TestCase):
    """Tests for gate-server uninstall."""

    @unittest.mock.patch("terok.cli.commands.gate_server.uninstall_systemd_units")
    @unittest.mock.patch("terok.cli.commands.gate_server.is_systemd_available", return_value=True)
    def test_uninstall(
        self, _mock_avail: unittest.mock.Mock, mock_uninstall: unittest.mock.Mock
    ) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_uninstall()
        mock_uninstall.assert_called_once()
        self.assertIn("removed", out.getvalue())

    @unittest.mock.patch("terok.cli.commands.gate_server.is_systemd_available", return_value=False)
    def test_uninstall_no_systemd(self, _mock: unittest.mock.Mock) -> None:
        """Uninstall exits with error when systemd is unavailable."""
        with self.assertRaises(SystemExit):
            _cmd_uninstall()


class TestCmdStart(unittest.TestCase):
    """Tests for gate-server start."""

    @unittest.mock.patch("terok.cli.commands.gate_server.start_daemon")
    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_start(self, _mock_status: unittest.mock.Mock, mock_start: unittest.mock.Mock) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_start(port=9999)
        mock_start.assert_called_once_with(port=9999)
        self.assertIn("Gate server started", out.getvalue())

    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="systemd", running=True, port=9418),
    )
    def test_start_already_running(self, _mock: unittest.mock.Mock) -> None:
        with self.assertRaises(SystemExit):
            _cmd_start(port=None)


class TestCmdStop(unittest.TestCase):
    """Tests for gate-server stop."""

    @unittest.mock.patch("terok.cli.commands.gate_server.stop_daemon")
    @unittest.mock.patch("terok.cli.commands.gate_server.is_daemon_running", return_value=True)
    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="daemon", running=True, port=9418),
    )
    def test_stop(
        self,
        _mock_status: unittest.mock.Mock,
        _mock_running: unittest.mock.Mock,
        mock_stop: unittest.mock.Mock,
    ) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_stop()
        mock_stop.assert_called_once()
        self.assertIn("Gate server stopped", out.getvalue())

    @unittest.mock.patch("terok.cli.commands.gate_server.is_daemon_running", return_value=False)
    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_stop_not_running(
        self, _mock_status: unittest.mock.Mock, _mock_running: unittest.mock.Mock
    ) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_stop()
        self.assertIn("not running", out.getvalue())

    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="systemd", running=True, port=9418),
    )
    def test_stop_systemd_managed(self, _mock: unittest.mock.Mock) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_stop()
        self.assertIn("managed by systemd", out.getvalue())


class TestCmdStatus(unittest.TestCase):
    """Tests for gate-server status."""

    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="daemon", running=True, port=9418),
    )
    def test_status_running(self, _mock: unittest.mock.Mock) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_status()
        output = out.getvalue()
        self.assertIn("daemon", output)
        self.assertIn("running", output)
        self.assertIn("9418", output)

    @unittest.mock.patch("terok.cli.commands.gate_server.is_systemd_available", return_value=True)
    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_status_stopped_with_systemd(self, *_mocks: unittest.mock.Mock) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_status()
        output = out.getvalue()
        self.assertIn("none", output)
        self.assertIn("stopped", output)
        self.assertIn("gate-server install", output)

    @unittest.mock.patch("terok.cli.commands.gate_server.is_systemd_available", return_value=False)
    @unittest.mock.patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_status_stopped_no_systemd(self, *_mocks: unittest.mock.Mock) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_status()
        output = out.getvalue()
        self.assertIn("none", output)
        self.assertIn("stopped", output)
        self.assertNotIn("gate-server install", output)
