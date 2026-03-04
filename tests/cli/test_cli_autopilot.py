# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for autopilot CLI commands: terokctl run (replaces run-claude)."""

import unittest
import unittest.mock

from terok.cli.main import main


class RunCliTests(unittest.TestCase):
    """Tests for terokctl run argument parsing."""

    def test_run_requires_project_and_prompt(self) -> None:
        """run requires project_id and prompt arguments."""
        with (
            unittest.mock.patch("sys.argv", ["terok", "run"]),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        # argparse exits with code 2 for missing required args
        self.assertEqual(ctx.exception.code, 2)

    def test_run_dispatches_to_task_run_headless(self) -> None:
        """run dispatches to task_run_headless with correct args."""
        with (
            unittest.mock.patch(
                "sys.argv",
                [
                    "terok",
                    "run",
                    "myproject",
                    "Fix the auth bug",
                    "--model",
                    "opus",
                    "--max-turns",
                    "50",
                    "--timeout",
                    "3600",
                ],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_headless") as mock_run,
        ):
            main()
            mock_run.assert_called_once_with(
                "myproject",
                "Fix the auth bug",
                config_path=None,
                model="opus",
                max_turns=50,
                timeout=3600,
                follow=True,
                agents=None,
                preset=None,
                name=None,
                provider=None,
                instructions=None,
            )

    def test_run_no_follow_flag(self) -> None:
        """run --no-follow passes follow=False."""
        with (
            unittest.mock.patch(
                "sys.argv",
                ["terok", "run", "myproject", "test", "--no-follow"],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_headless") as mock_run,
        ):
            main()
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            # follow should be False
            self.assertFalse(call_kwargs[1]["follow"])

    def test_run_with_config(self) -> None:
        """run --config passes config_path."""
        with (
            unittest.mock.patch(
                "sys.argv",
                [
                    "terok",
                    "run",
                    "myproject",
                    "test",
                    "--config",
                    "/path/to/agent.yml",
                ],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_headless") as mock_run,
        ):
            main()
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            self.assertEqual(call_kwargs[1]["config_path"], "/path/to/agent.yml")

    def test_run_with_agent_selection(self) -> None:
        """run --agent passes agents list to task_run_headless."""
        with (
            unittest.mock.patch(
                "sys.argv",
                [
                    "terok",
                    "run",
                    "myproject",
                    "test",
                    "--agent",
                    "debugger",
                    "--agent",
                    "planner",
                ],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_headless") as mock_run,
        ):
            main()
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            self.assertEqual(call_kwargs[1]["agents"], ["debugger", "planner"])

    def test_run_with_provider_flag(self) -> None:
        """run --provider passes provider to task_run_headless."""
        with (
            unittest.mock.patch(
                "sys.argv",
                ["terok", "run", "myproject", "test", "--provider", "codex"],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_headless") as mock_run,
        ):
            main()
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            self.assertEqual(call_kwargs[1]["provider"], "codex")

    def test_run_invalid_provider_rejected(self) -> None:
        """run --provider with invalid name is rejected by argparse."""
        with (
            unittest.mock.patch(
                "sys.argv",
                ["terok", "run", "myproject", "test", "--provider", "invalid"],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertEqual(ctx.exception.code, 2)

    def test_run_default_provider_is_none(self) -> None:
        """run without --provider passes provider=None."""
        with (
            unittest.mock.patch(
                "sys.argv",
                ["terok", "run", "myproject", "test"],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_headless") as mock_run,
        ):
            main()
            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args
            self.assertIsNone(call_kwargs[1]["provider"])

    def test_run_with_instructions_flag(self) -> None:
        """run --instructions FILE reads file and passes instructions."""
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("Custom agent instructions here.")
            f.flush()
            instr_path = f.name

        try:
            with (
                unittest.mock.patch(
                    "sys.argv",
                    ["terok", "run", "myproject", "test", "--instructions", instr_path],
                ),
                unittest.mock.patch("terok.cli.commands.task.task_run_headless") as mock_run,
            ):
                main()
                mock_run.assert_called_once()
                call_kwargs = mock_run.call_args
                self.assertEqual(call_kwargs[1]["instructions"], "Custom agent instructions here.")
        finally:
            Path(instr_path).unlink()

    def test_run_instructions_file_not_found(self) -> None:
        """run --instructions with nonexistent file raises SystemExit."""
        with (
            unittest.mock.patch(
                "sys.argv",
                [
                    "terok",
                    "run",
                    "myproject",
                    "test",
                    "--instructions",
                    "/nonexistent/path.md",
                ],
            ),
            self.assertRaises(SystemExit) as ctx,
        ):
            main()
        self.assertIn("not found", str(ctx.exception))

    def test_task_run_cli_with_agent_selection(self) -> None:
        """task run-cli --agent passes agents to task_run_cli."""
        with (
            unittest.mock.patch(
                "sys.argv",
                ["terok", "task", "run-cli", "myproject", "1", "--agent", "debugger"],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_cli") as mock_run,
        ):
            main()
            mock_run.assert_called_once_with(
                "myproject",
                "1",
                agents=["debugger"],
                preset=None,
            )

    def test_task_run_web_with_agent_selection(self) -> None:
        """task run-web --agent passes agents to task_run_web."""
        with (
            unittest.mock.patch(
                "sys.argv",
                [
                    "terok",
                    "--experimental",
                    "task",
                    "run-web",
                    "myproject",
                    "1",
                    "--agent",
                    "reviewer",
                ],
            ),
            unittest.mock.patch("terok.cli.commands.task.task_run_web") as mock_run,
        ):
            main()
            mock_run.assert_called_once_with(
                "myproject",
                "1",
                backend=None,
                agents=["reviewer"],
                preset=None,
            )
