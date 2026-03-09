# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for shield CLI commands."""

import unittest
import unittest.mock
from io import StringIO

from terok.cli.commands.shield import (
    _cmd_allow,
    _cmd_deny,
    _cmd_logs,
    _cmd_profiles,
    _cmd_rules,
    _cmd_setup,
    _cmd_status,
)

TEST_IP = "1.2.3.4"


class TestCmdSetup(unittest.TestCase):
    """Tests for shield setup."""

    @unittest.mock.patch("terok.cli.commands.shield.setup")
    def test_setup(self, mock_setup: unittest.mock.Mock) -> None:
        """setup() calls shield setup and prints confirmation."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_setup()
        mock_setup.assert_called_once()
        self.assertIn("installed", out.getvalue())


class TestCmdStatus(unittest.TestCase):
    """Tests for shield status."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.status",
        return_value={"hook_installed": True, "mode": "hook"},
    )
    def test_status(self, _mock: unittest.mock.Mock) -> None:
        """status() prints key-value pairs."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_status()
        output = out.getvalue()
        self.assertIn("hook_installed", output)
        self.assertIn("True", output)
        self.assertIn("hook", output)


class TestCmdProfiles(unittest.TestCase):
    """Tests for shield profiles."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.get_profiles",
        return_value=["dev-standard", "dev-strict"],
    )
    def test_profiles(self, _mock: unittest.mock.Mock) -> None:
        """profiles() lists each profile on a line."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_profiles()
        output = out.getvalue()
        self.assertIn("dev-standard", output)
        self.assertIn("dev-strict", output)


class TestCmdRules(unittest.TestCase):
    """Tests for shield rules."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.rules",
        return_value="table inet shield { chain output { } }",
    )
    def test_rules(self, mock_rules: unittest.mock.Mock) -> None:
        """rules() prints nft output."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_rules("my-container")
        mock_rules.assert_called_once_with("my-container")
        self.assertIn("table inet shield", out.getvalue())


class TestCmdAllow(unittest.TestCase):
    """Tests for shield allow."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.allow",
        return_value=["allowed TEST_IP/32"],
    )
    def test_allow(self, mock_allow: unittest.mock.Mock) -> None:
        """allow() prints result lines."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_allow("ctr", TEST_IP)
        mock_allow.assert_called_once_with("ctr", TEST_IP)
        self.assertIn("allowed", out.getvalue())


class TestCmdDeny(unittest.TestCase):
    """Tests for shield deny."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.deny",
        return_value=["denied TEST_IP/32"],
    )
    def test_deny(self, mock_deny: unittest.mock.Mock) -> None:
        """deny() prints result lines."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_deny("ctr", TEST_IP)
        mock_deny.assert_called_once_with("ctr", TEST_IP)
        self.assertIn("denied", out.getvalue())


class TestCmdLogs(unittest.TestCase):
    """Tests for shield logs."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.get_log_containers",
        return_value=["ctr-a", "ctr-b"],
    )
    def test_logs_list_containers(self, _mock: unittest.mock.Mock) -> None:
        """logs without container lists available containers."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_logs(None, 50)
        output = out.getvalue()
        self.assertIn("ctr-a", output)
        self.assertIn("ctr-b", output)

    @unittest.mock.patch(
        "terok.cli.commands.shield.get_log_containers",
        return_value=[],
    )
    def test_logs_no_containers(self, _mock: unittest.mock.Mock) -> None:
        """logs without container and no logs prints message."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_logs(None, 50)
        self.assertIn("No audit logs", out.getvalue())

    @unittest.mock.patch(
        "terok.cli.commands.shield.logs",
        return_value=iter([{"action": "allow", "dest": TEST_IP}]),
    )
    def test_logs_for_container(self, mock_logs: unittest.mock.Mock) -> None:
        """logs with container prints JSON entries."""
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_logs("my-ctr", 25)
        mock_logs.assert_called_once_with("my-ctr", n=25)
        output = out.getvalue()
        self.assertIn("allow", output)
        self.assertIn(TEST_IP, output)
