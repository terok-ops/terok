# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for sickbay CLI command."""

import unittest
import unittest.mock
from io import StringIO

from terok.cli.commands.sickbay import _cmd_sickbay
from terok.lib.security.gate_server import GateServerStatus


class TestSickbay(unittest.TestCase):
    """Tests for the sickbay health check command."""

    @unittest.mock.patch(
        "terok.cli.commands.sickbay.get_server_status",
        return_value=GateServerStatus(mode="systemd", running=True, port=9418),
    )
    def test_all_ok(self, _mock: unittest.mock.Mock) -> None:
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_sickbay()
        output = out.getvalue()
        self.assertIn("Gate server", output)
        self.assertIn("ok", output)
        self.assertIn("systemd", output)

    @unittest.mock.patch("terok.cli.commands.sickbay.is_systemd_available", return_value=True)
    @unittest.mock.patch(
        "terok.cli.commands.sickbay.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_warn_not_running_systemd(self, *_mocks: unittest.mock.Mock) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
                _cmd_sickbay()
        self.assertEqual(ctx.exception.code, 1)
        output = out.getvalue()
        self.assertIn("WARN", output)
        self.assertIn("gate-server install", output)

    @unittest.mock.patch("terok.cli.commands.sickbay.is_systemd_available", return_value=False)
    @unittest.mock.patch(
        "terok.cli.commands.sickbay.get_server_status",
        return_value=GateServerStatus(mode="none", running=False, port=9418),
    )
    def test_warn_not_running_no_systemd(self, *_mocks: unittest.mock.Mock) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
                _cmd_sickbay()
        self.assertEqual(ctx.exception.code, 1)
        output = out.getvalue()
        self.assertIn("WARN", output)
        self.assertIn("gate-server start", output)

    @unittest.mock.patch(
        "terok.cli.commands.sickbay.get_server_status",
        return_value=GateServerStatus(mode="systemd", running=False, port=9418),
    )
    def test_error_socket_inactive(self, _mock: unittest.mock.Mock) -> None:
        with self.assertRaises(SystemExit) as ctx:
            with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
                _cmd_sickbay()
        self.assertEqual(ctx.exception.code, 2)
        output = out.getvalue()
        self.assertIn("ERROR", output)
        self.assertIn("not active", output)
