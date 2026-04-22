# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for autopilot CLI: ``terok task run --mode headless``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.orchestration.task_runners import HeadlessRunRequest
from tests.testcli import run_cli
from tests.testfs import NONEXISTENT_MARKDOWN_PATH


def capture_headless_request(project: str, prompt: str, *extra_args: str) -> HeadlessRunRequest:
    """Run ``terok task run --mode headless`` and capture the forwarded request."""
    with (
        patch("terok.cli.commands.task.project_image_exists", return_value=True),
        patch("terok.cli.commands.task.task_run_headless") as mock_run,
    ):
        run_cli("task", "run", project, "--mode", "headless", "--prompt", prompt, *extra_args)

    mock_run.assert_called_once()
    request = mock_run.call_args.args[0]
    assert isinstance(request, HeadlessRunRequest)
    return request


def assert_cli_exit(*argv: str, code: int | None = None, message: str | None = None) -> None:
    """Assert that invoking the CLI exits, optionally checking code or message."""
    with pytest.raises(SystemExit) as exc_info:
        run_cli(*argv)

    if code is not None:
        assert exc_info.value.code == code
    if message is not None:
        assert message in str(exc_info.value)


def test_run_dispatches_to_task_run_headless() -> None:
    """``terok task run --mode headless`` builds the expected headless request."""
    request = capture_headless_request(
        "myproject",
        "Fix the auth bug",
        "--model",
        "opus",
        "--max-turns",
        "50",
        "--timeout",
        "3600",
    )

    assert request.project_id == "myproject"
    assert request.prompt == "Fix the auth bug"
    assert request.config_path is None
    assert request.model == "opus"
    assert request.max_turns == 50
    assert request.timeout == 3600
    assert request.follow is True
    assert request.agents is None
    assert request.preset is None
    assert request.name is None
    assert request.provider is None
    assert request.instructions is None


def test_headless_without_prompt_exits() -> None:
    """``--mode headless`` with no ``--prompt`` exits with a clear message."""
    assert_cli_exit("task", "run", "myproject", "--mode", "headless", message="--prompt")


@pytest.mark.parametrize(
    ("argv", "code", "message"),
    [
        pytest.param(("task", "run"), 2, None, id="missing-project"),
        pytest.param(
            ("task", "run", "myproject", "--mode", "headless", "--prompt", "t", "--provider", "x"),
            2,
            None,
            id="bad-provider",
        ),
        pytest.param(
            (
                "task",
                "run",
                "myproject",
                "--mode",
                "headless",
                "--prompt",
                "t",
                "--instructions",
                str(NONEXISTENT_MARKDOWN_PATH),
            ),
            None,
            "not found",
            id="missing-instructions-file",
        ),
    ],
)
def test_run_rejects_invalid_invocations(
    argv: tuple[str, ...],
    code: int | None,
    message: str | None,
) -> None:
    """Invalid CLI invocations fail with parser or validation errors."""
    assert_cli_exit(*argv, code=code, message=message)


@pytest.mark.parametrize(
    ("extra_args", "field", "expected"),
    [
        pytest.param(["--no-follow"], "follow", False, id="no-follow"),
        pytest.param(
            ["--config", "/path/to/agent.yml"],
            "config_path",
            "/path/to/agent.yml",
            id="config",
        ),
        pytest.param(
            ["--agent", "debugger", "--agent", "planner"],
            "agents",
            ["debugger", "planner"],
            id="agents",
        ),
        pytest.param(["--provider", "codex"], "provider", "codex", id="provider"),
        pytest.param([], "provider", None, id="provider-default"),
    ],
)
def test_run_forwards_optional_flags(
    extra_args: list[str],
    field: str,
    expected: object,
) -> None:
    """Optional autopilot flags are forwarded into the headless request."""
    assert getattr(capture_headless_request("myproject", "test", *extra_args), field) == expected


def test_instructions_file_unicode_error_exits_cleanly(tmp_path: Path) -> None:
    """Non-UTF-8 instructions file raises SystemExit with an actionable hint."""
    bad = tmp_path / "binary.md"
    bad.write_bytes(b"\xff\xfe\x00\x00\xff\xfe")  # UTF-16 BOM — not UTF-8

    assert_cli_exit(
        "task",
        "run",
        "myproject",
        "--mode",
        "headless",
        "--prompt",
        "test",
        "--instructions",
        str(bad),
        message="UTF-8",
    )


def test_instructions_file_read_error_exits_cleanly(tmp_path: Path) -> None:
    """OSError while reading instructions raises SystemExit with context."""
    instructions_path = tmp_path / "unreadable.md"
    instructions_path.write_text("x", encoding="utf-8")

    with patch("pathlib.Path.read_text", side_effect=OSError("permission denied")):
        assert_cli_exit(
            "task",
            "run",
            "myproject",
            "--mode",
            "headless",
            "--prompt",
            "test",
            "--instructions",
            str(instructions_path),
            message="Failed to read",
        )


def test_run_with_instructions_flag(tmp_path: Path) -> None:
    """The ``--instructions`` flag loads the referenced file contents."""
    instructions_path = tmp_path / "instructions.md"
    instructions_path.write_text("Custom agent instructions here.", encoding="utf-8")

    request = capture_headless_request(
        "myproject",
        "test",
        "--instructions",
        str(instructions_path),
    )
    assert request.instructions == "Custom agent instructions here."


# ---------------------------------------------------------------------------
# ``task run --mode cli|toad`` (interactive modes: create a new task and run it)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "runner_target"),
    [
        ("cli", "terok.cli.commands.task.task_run_cli"),
        ("toad", "terok.cli.commands.task.task_run_toad"),
    ],
)
def test_run_interactive_mode_creates_and_runs(mode: str, runner_target: str) -> None:
    """``task run --mode cli|toad`` creates a new task and delegates to the runner."""
    with (
        patch("terok.cli.commands.task.project_image_exists", return_value=True),
        patch("terok.cli.commands.task.task_new", return_value="42") as mock_new,
        patch("terok.cli.commands.task.task_login") as mock_login,
        patch(runner_target) as mock_runner,
    ):
        # --no-attach keeps the CLI test path quiet regardless of TTY state
        # in the harness; toad mode never attaches.
        run_cli("task", "run", "myproj", "--mode", mode, "--no-attach")

    mock_new.assert_called_once_with("myproj", name=None)
    mock_runner.assert_called_once_with("myproj", "42", agents=None, preset=None, unrestricted=None)
    mock_login.assert_not_called()


def test_run_cli_mode_attaches_by_default_on_tty() -> None:
    """With no flag, TTY stdio triggers ``task_login`` via ``_resolve_attach``."""
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("sys.stdout.isatty", return_value=True),
        patch("terok.cli.commands.task.project_image_exists", return_value=True),
        patch("terok.cli.commands.task.task_new", return_value="42"),
        patch("terok.cli.commands.task.task_run_cli"),
        patch("terok.cli.commands.task.task_login") as mock_login,
    ):
        # No --attach / --no-attach — exercise the default-on-TTY branch.
        run_cli("task", "run", "myproj")

    mock_login.assert_called_once_with("myproj", "42")


def test_run_missing_image_exits_on_non_tty(capsys: pytest.CaptureFixture[str]) -> None:
    """Preflight refuses to build silently when stdout is not a TTY."""
    with (
        patch("terok.cli.commands.task.project_image_exists", return_value=False),
        patch("terok.cli.commands.task.task_new") as mock_new,
        patch("terok.cli.commands.task.task_run_cli") as mock_runner,
        pytest.raises(SystemExit) as exc,
    ):
        run_cli("task", "run", "myproj", "--no-attach")

    assert "terok project build myproj" in str(exc.value)
    mock_new.assert_not_called()
    mock_runner.assert_not_called()


# ---------------------------------------------------------------------------
# ``terokctl task attach`` (scripting: run an existing task)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "runner_target"),
    [
        ("cli", "terok.cli.commands.task.task_run_cli"),
        ("toad", "terok.cli.commands.task.task_run_toad"),
    ],
)
def test_task_attach_forwards_to_runner(mode: str, runner_target: str) -> None:
    """``terokctl task attach <p> <t> --mode cli|toad`` routes to the right runner."""
    with (
        patch("terok.cli.commands.task.resolve_task_id", side_effect=lambda _pid, tid: tid),
        patch(runner_target) as mock_run,
    ):
        run_cli(
            "task",
            "attach",
            "myproject",
            "1",
            "--mode",
            mode,
            "--agent",
            "debugger",
            prog="terokctl",
        )

    mock_run.assert_called_once_with(
        "myproject", "1", agents=["debugger"], preset=None, unrestricted=None
    )


def test_task_attach_not_in_terok() -> None:
    """``terok task attach`` is not registered on the human-facing surface."""
    with pytest.raises(SystemExit):
        run_cli("task", "attach", "myproject", "1", "--mode", "cli")
