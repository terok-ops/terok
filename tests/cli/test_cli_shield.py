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


class TestCmdSetup(unittest.TestCase):
    """Tests for shield setup command."""

    @unittest.mock.patch("terok.cli.commands.shield.setup")
    def test_setup_prints_complete(self, mock_setup: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_setup()
        mock_setup.assert_called_once()
        self.assertIn("complete", out.getvalue())


class TestCmdStatus(unittest.TestCase):
    """Tests for shield status command."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.status",
        return_value={
            "mode": "hook",
            "audit_enabled": True,
            "profiles": ["base", "dev-standard"],
            "log_files": ["ctr-a"],
        },
    )
    def test_status_output(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_status()
        output = out.getvalue()
        self.assertIn("hook", output)
        self.assertIn("enabled", output)
        self.assertIn("base", output)
        self.assertIn("1 container", output)


class TestCmdProfiles(unittest.TestCase):
    """Tests for shield profiles command."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.get_profiles",
        return_value=["base", "dev-standard"],
    )
    def test_profiles_listed(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_profiles()
        self.assertIn("base", out.getvalue())
        self.assertIn("dev-standard", out.getvalue())


class TestCmdRules(unittest.TestCase):
    """Tests for shield rules command."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.rules",
        return_value="table inet shield { chain output { ... } }",
    )
    def test_rules_output(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_rules("test-ctr")
        self.assertIn("shield", out.getvalue())


class TestCmdAllow(unittest.TestCase):
    """Tests for shield allow command."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.allow",
        return_value=["1.2.3.4"],
    )
    def test_allow_output(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_allow("test-ctr", "example.com")
        self.assertIn("Allowed 1 IP(s)", out.getvalue())

    @unittest.mock.patch("terok.cli.commands.shield.allow", return_value=[])
    def test_allow_no_ips(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_allow("test-ctr", "nonexistent.invalid")
        self.assertIn("No IPs", out.getvalue())


class TestCmdDeny(unittest.TestCase):
    """Tests for shield deny command."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.deny",
        return_value=["1.2.3.4"],
    )
    def test_deny_output(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_deny("test-ctr", "1.2.3.4")
        self.assertIn("Denied 1 IP(s)", out.getvalue())


class TestCmdLogs(unittest.TestCase):
    """Tests for shield logs command."""

    @unittest.mock.patch(
        "terok.cli.commands.shield.get_log_containers",
        return_value=["ctr-a", "ctr-b"],
    )
    def test_logs_no_container_lists_containers(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_logs(None, 50)
        output = out.getvalue()
        self.assertIn("ctr-a", output)
        self.assertIn("ctr-b", output)

    @unittest.mock.patch("terok.cli.commands.shield.get_log_containers", return_value=[])
    def test_logs_no_logs(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_logs(None, 50)
        self.assertIn("No audit logs", out.getvalue())

    @unittest.mock.patch(
        "terok.cli.commands.shield.logs",
        return_value=iter([{"ts": "2026-01-01T00:00:00Z", "action": "setup", "container": "ctr"}]),
    )
    def test_logs_with_container(self, _mock: unittest.mock.Mock):
        with unittest.mock.patch("sys.stdout", new_callable=StringIO) as out:
            _cmd_logs("ctr", 10)
        self.assertIn("setup", out.getvalue())
