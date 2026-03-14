# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for shield CLI commands (registry-driven dispatch)."""

import argparse
import unittest
from io import StringIO
from unittest.mock import MagicMock, patch

from terok_shield import ExecError

from constants import MOCK_TASK_DIR_1
from terok.cli.commands.shield import _resolve_task, dispatch, register


class TestRegister(unittest.TestCase):
    """Tests for register() building subparsers from COMMANDS."""

    def setUp(self) -> None:
        """Create a parser with shield subparsers."""
        self.parser = argparse.ArgumentParser()
        subs = self.parser.add_subparsers(dest="cmd")
        register(subs)

    def test_status_without_task(self) -> None:
        """status subcommand parses without project/task."""
        args = self.parser.parse_args(["shield", "status"])
        self.assertEqual(args.shield_cmd, "status")

    def test_status_with_task(self) -> None:
        """status with project_id and task_id queries container state."""
        args = self.parser.parse_args(["shield", "status", "proj", "1"])
        self.assertEqual(args.shield_cmd, "status")
        self.assertEqual(args.project_id, "proj")
        self.assertEqual(args.task_id, "1")

    def test_allow_subcommand(self) -> None:
        """allow requires project_id, task_id, and target."""
        args = self.parser.parse_args(["shield", "allow", "proj", "task1", "example.com"])
        self.assertEqual(args.shield_cmd, "allow")
        self.assertEqual(args.project_id, "proj")
        self.assertEqual(args.task_id, "task1")
        self.assertEqual(args.target, "example.com")

    def test_deny_subcommand(self) -> None:
        """deny requires project_id, task_id, and target."""
        args = self.parser.parse_args(["shield", "deny", "proj", "task1", "example.com"])
        self.assertEqual(args.shield_cmd, "deny")

    def test_down_subcommand(self) -> None:
        """down accepts project_id, task_id, and optional --all."""
        args = self.parser.parse_args(["shield", "down", "proj", "task1", "--all"])
        self.assertEqual(args.shield_cmd, "down")
        self.assertTrue(args.allow_all)

    def test_up_subcommand(self) -> None:
        """up requires project_id and task_id."""
        args = self.parser.parse_args(["shield", "up", "proj", "task1"])
        self.assertEqual(args.shield_cmd, "up")

    def test_rules_subcommand(self) -> None:
        """rules requires project_id and task_id."""
        args = self.parser.parse_args(["shield", "rules", "proj", "task1"])
        self.assertEqual(args.shield_cmd, "rules")

    def test_profiles_subcommand(self) -> None:
        """profiles subcommand has no project/task args."""
        args = self.parser.parse_args(["shield", "profiles"])
        self.assertEqual(args.shield_cmd, "profiles")
        self.assertFalse(hasattr(args, "project_id"))

    def test_standalone_only_excluded(self) -> None:
        """prepare, run, resolve are not registered (standalone_only)."""
        for cmd in ("prepare", "run", "resolve"):
            with self.assertRaises(SystemExit):
                self.parser.parse_args(["shield", cmd])


class TestDispatch(unittest.TestCase):
    """Tests for dispatch()."""

    def test_wrong_cmd_returns_false(self) -> None:
        """dispatch returns False for non-shield commands."""
        args = argparse.Namespace(cmd="project")
        self.assertFalse(dispatch(args))

    @patch("terok.cli.commands.shield.make_shield")
    def test_status_without_task(self, mock_make: MagicMock) -> None:
        """dispatch handles bare status (no task) via registry handler."""
        mock_shield = MagicMock()
        mock_shield.status.return_value = {
            "mode": "hook",
            "profiles": ["dev-standard"],
            "audit_enabled": True,
        }
        mock_make.return_value = mock_shield

        args = argparse.Namespace(cmd="shield", shield_cmd="status")
        with patch("sys.stdout", new_callable=StringIO) as out:
            result = dispatch(args)

        self.assertTrue(result)
        output = out.getvalue()
        self.assertIn("Mode", output)
        self.assertIn("hook", output)

    def test_partial_task_selector_exits(self) -> None:
        """Providing project_id without task_id exits with error."""
        args = argparse.Namespace(
            cmd="shield", shield_cmd="status", project_id="proj", task_id=None
        )
        with (
            patch("sys.stderr", new_callable=StringIO) as err,
            self.assertRaises(SystemExit) as ctx,
        ):
            dispatch(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("both", err.getvalue())

    @patch("terok.cli.commands.shield._resolve_task")
    @patch("terok.cli.commands.shield.make_shield")
    def test_status_with_task(self, mock_make: MagicMock, mock_resolve: MagicMock) -> None:
        """dispatch handles status with project/task — queries container state."""
        mock_resolve.return_value = ("proj-cli-1", str(MOCK_TASK_DIR_1))
        mock_shield = MagicMock()
        mock_shield.state.return_value = MagicMock(value="up")
        mock_make.return_value = mock_shield

        args = argparse.Namespace(cmd="shield", shield_cmd="status", project_id="proj", task_id="1")
        with patch("sys.stdout", new_callable=StringIO) as out:
            result = dispatch(args)

        self.assertTrue(result)
        self.assertIn("up", out.getvalue())
        mock_shield.state.assert_called_once_with("proj-cli-1")

    @patch("terok.cli.commands.shield.make_shield")
    def test_preview_all_without_down_prints_error(self, mock_make: MagicMock) -> None:
        """preview --all without --down prints clean error to stderr."""
        mock_shield = MagicMock()
        mock_shield.preview.side_effect = ValueError("--all requires --down")
        mock_make.return_value = mock_shield

        args = argparse.Namespace(cmd="shield", shield_cmd="preview", down=False, allow_all=True)
        with (
            patch("sys.stderr", new_callable=StringIO) as err,
            self.assertRaises(SystemExit) as ctx,
        ):
            dispatch(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("--all requires --down", err.getvalue())

    @patch("terok.cli.commands.shield._resolve_task")
    @patch("terok.cli.commands.shield.make_shield")
    def test_exec_error_prints_not_running(
        self, mock_make: MagicMock, mock_resolve: MagicMock
    ) -> None:
        """ExecError from nft produces a 'not running' message."""
        mock_resolve.return_value = ("proj-cli-1", str(MOCK_TASK_DIR_1))
        mock_shield = MagicMock()
        mock_shield.state.side_effect = ExecError(["nft", "list"], 1, "no such process")
        mock_make.return_value = mock_shield

        args = argparse.Namespace(cmd="shield", shield_cmd="status", project_id="proj", task_id="1")
        with (
            patch("sys.stderr", new_callable=StringIO) as err,
            self.assertRaises(SystemExit) as ctx,
        ):
            dispatch(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("not running", err.getvalue())

    @patch("terok.cli.commands.shield._resolve_task")
    @patch("terok.cli.commands.shield.make_shield")
    def test_runtime_error_prints_message(
        self, mock_make: MagicMock, mock_resolve: MagicMock
    ) -> None:
        """RuntimeError from handler is caught and printed cleanly."""
        mock_resolve.return_value = ("proj-cli-1", str(MOCK_TASK_DIR_1))
        mock_shield = MagicMock()
        mock_shield.allow.side_effect = RuntimeError("No IPs allowed for proj-cli-1")
        mock_make.return_value = mock_shield

        args = argparse.Namespace(
            cmd="shield",
            shield_cmd="allow",
            project_id="proj",
            task_id="1",
            target="example.com",
        )
        with (
            patch("sys.stderr", new_callable=StringIO) as err,
            self.assertRaises(SystemExit) as ctx,
        ):
            dispatch(args)

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("No IPs allowed", err.getvalue())


class TestSetupSubcommand(unittest.TestCase):
    """Tests for the manually registered setup subcommand."""

    def setUp(self) -> None:
        """Create a parser with shield subparsers."""
        self.parser = argparse.ArgumentParser()
        subs = self.parser.add_subparsers(dest="cmd")
        register(subs)

    def test_setup_registered(self) -> None:
        """setup subcommand is registered and parses."""
        args = self.parser.parse_args(["shield", "setup"])
        self.assertEqual(args.shield_cmd, "setup")

    def test_setup_root_flag(self) -> None:
        """setup --root flag is parsed."""
        args = self.parser.parse_args(["shield", "setup", "--root"])
        self.assertTrue(args.root)
        self.assertFalse(args.user)

    def test_setup_user_flag(self) -> None:
        """setup --user flag is parsed."""
        args = self.parser.parse_args(["shield", "setup", "--user"])
        self.assertFalse(args.root)
        self.assertTrue(args.user)


class TestSetupDispatch(unittest.TestCase):
    """Tests for setup command dispatch."""

    @patch("terok.lib.facade.shield_run_setup")
    def test_setup_root_dispatch(self, mock_setup: MagicMock) -> None:
        """dispatch calls shield_run_setup(root=True) for --root."""
        args = argparse.Namespace(cmd="shield", shield_cmd="setup", root=True, user=False)
        result = dispatch(args)
        self.assertTrue(result)
        mock_setup.assert_called_once_with(root=True, user=False)

    @patch("terok.lib.facade.shield_run_setup")
    def test_setup_user_dispatch(self, mock_setup: MagicMock) -> None:
        """dispatch calls shield_run_setup(user=True) for --user."""
        args = argparse.Namespace(cmd="shield", shield_cmd="setup", root=False, user=True)
        result = dispatch(args)
        self.assertTrue(result)
        mock_setup.assert_called_once_with(root=False, user=True)


class TestResolveTask(unittest.TestCase):
    """Tests for _resolve_task()."""

    @patch("terok.lib.containers.tasks.load_task_meta", return_value=({"mode": None}, None))
    @patch("terok.lib.core.projects.load_project")
    def test_never_run_task_raises(self, mock_proj: MagicMock, _meta: MagicMock) -> None:
        """Task with mode=None raises ValueError."""
        mock_proj.return_value = MagicMock(id="proj")
        with self.assertRaisesRegex(ValueError, "has never been run"):
            _resolve_task("proj", "1")
