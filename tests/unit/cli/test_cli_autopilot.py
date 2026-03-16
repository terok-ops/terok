# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for autopilot CLI commands: ``terokctl run``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.containers.task_runners import HeadlessRunRequest
from tests.testcli import run_cli
from tests.testfs import NONEXISTENT_MARKDOWN_PATH


def capture_headless_request(*argv: str) -> HeadlessRunRequest:
    """Run ``terok run`` and return the forwarded headless request."""
    with patch("terok.cli.commands.task.task_run_headless") as mock_run:
        run_cli(*argv)

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
    """The top-level ``run`` command builds the expected headless request."""
    request = capture_headless_request(
        "run",
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


@pytest.mark.parametrize(
    ("argv", "code", "message"),
    [
        pytest.param(("run",), 2, None, id="missing-project-and-prompt"),
        pytest.param(
            ("run", "myproject", "test", "--provider", "invalid"),
            2,
            None,
            id="bad-provider",
        ),
        pytest.param(
            ("run", "myproject", "test", "--instructions", str(NONEXISTENT_MARKDOWN_PATH)),
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
    assert (
        getattr(capture_headless_request("run", "myproject", "test", *extra_args), field)
        == expected
    )


def test_run_with_instructions_flag(tmp_path: Path) -> None:
    """The ``--instructions`` flag loads the referenced file contents."""
    instructions_path = tmp_path / "instructions.md"
    instructions_path.write_text("Custom agent instructions here.", encoding="utf-8")

    request = capture_headless_request(
        "run",
        "myproject",
        "test",
        "--instructions",
        str(instructions_path),
    )
    assert request.instructions == "Custom agent instructions here."


@pytest.mark.parametrize(
    ("argv", "target", "expected_args", "expected_kwargs"),
    [
        pytest.param(
            ["task", "run-cli", "myproject", "1", "--agent", "debugger"],
            "terok.cli.commands.task.task_run_cli",
            ("myproject", "1"),
            {"agents": ["debugger"], "preset": None, "unrestricted": None},
            id="run-cli",
        ),
        pytest.param(
            ["task", "run-toad", "myproject", "1"],
            "terok.cli.commands.task.task_run_toad",
            ("myproject", "1"),
            {"agents": None, "preset": None, "unrestricted": None},
            id="run-toad",
        ),
        pytest.param(
            ["task", "run-toad", "myproject", "1", "--agent", "debugger"],
            "terok.cli.commands.task.task_run_toad",
            ("myproject", "1"),
            {"agents": ["debugger"], "preset": None, "unrestricted": None},
            id="run-toad-with-agent",
        ),
    ],
)
def test_task_run_commands_forward_selected_agents(
    argv: list[str],
    target: str,
    expected_args: tuple[str, ...],
    expected_kwargs: dict[str, object],
) -> None:
    """Selected agents and permission mode are forwarded to task runners."""
    with patch(target) as mock_run:
        run_cli(*argv)

    mock_run.assert_called_once_with(*expected_args, **expected_kwargs)
