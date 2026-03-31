# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for shield CLI commands (registry-driven dispatch)."""

from __future__ import annotations

import argparse
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_shield import ExecError

from terok.cli.commands.shield import _resolve_task, dispatch, register
from tests.testfs import MOCK_TASK_DIR_1

MISSING: object = object()
# Sentinel used when an argparse attribute should be absent.


@pytest.fixture()
def shield_parser() -> argparse.ArgumentParser:
    """Return an argument parser with the shield subcommands registered."""
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    return parser


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(["shield", "status"], {"shield_cmd": "status"}, id="status-no-task"),
        pytest.param(
            ["shield", "status", "proj", "1"],
            {"shield_cmd": "status", "project_id": "proj", "task_id": "1"},
            id="status-with-task",
        ),
        pytest.param(
            ["shield", "allow", "proj", "task1", "example.com"],
            {
                "shield_cmd": "allow",
                "project_id": "proj",
                "task_id": "task1",
                "target": "example.com",
            },
            id="allow",
        ),
        pytest.param(
            ["shield", "deny", "proj", "task1", "example.com"],
            {
                "shield_cmd": "deny",
                "project_id": "proj",
                "task_id": "task1",
                "target": "example.com",
            },
            id="deny",
        ),
        pytest.param(
            ["shield", "down", "proj", "task1", "--all"],
            {
                "shield_cmd": "down",
                "project_id": "proj",
                "task_id": "task1",
                "allow_all": True,
            },
            id="down-all",
        ),
        pytest.param(
            ["shield", "up", "proj", "task1"],
            {"shield_cmd": "up", "project_id": "proj", "task_id": "task1"},
            id="up",
        ),
        pytest.param(
            ["shield", "rules", "proj", "task1"],
            {"shield_cmd": "rules", "project_id": "proj", "task_id": "task1"},
            id="rules",
        ),
        pytest.param(
            ["shield", "profiles"],
            {"shield_cmd": "profiles", "project_id": MISSING},
            id="profiles",
        ),
        pytest.param(
            ["shield", "setup"],
            {"shield_cmd": "setup", "root": False, "user": False},
            id="setup",
        ),
        pytest.param(
            ["shield", "setup", "--root"],
            {"shield_cmd": "setup", "root": True, "user": False},
            id="setup-root",
        ),
        pytest.param(
            ["shield", "setup", "--user"],
            {"shield_cmd": "setup", "root": False, "user": True},
            id="setup-user",
        ),
    ],
)
def test_register_parses_shield_subcommands(
    shield_parser: argparse.ArgumentParser,
    argv: list[str],
    expected: dict[str, object],
) -> None:
    """Registered shield subcommands parse the expected argument shapes."""
    args = shield_parser.parse_args(argv)
    for key, value in expected.items():
        if value is MISSING:
            assert not hasattr(args, key)
        else:
            assert getattr(args, key) == value


@pytest.mark.parametrize("command", ["prepare", "run", "resolve"])
def test_register_excludes_standalone_only_commands(
    shield_parser: argparse.ArgumentParser,
    command: str,
) -> None:
    """Standalone-only commands are not available through the main CLI parser."""
    with pytest.raises(SystemExit):
        shield_parser.parse_args(["shield", command])


def test_dispatch_returns_false_for_non_shield_commands() -> None:
    """Dispatch ignores non-shield CLI namespaces."""
    assert not dispatch(argparse.Namespace(cmd="project"))


@patch("terok.cli.commands.shield.make_shield")
def test_dispatch_status_without_task(mock_make: MagicMock) -> None:
    """Bare ``shield status`` shows the runtime/available shield status."""
    mock_shield = MagicMock()
    mock_shield.status.return_value = {
        "mode": "hook",
        "profiles": ["dev-standard"],
        "audit_enabled": True,
    }
    mock_make.return_value = mock_shield

    with patch("sys.stdout", new_callable=StringIO) as out:
        assert dispatch(argparse.Namespace(cmd="shield", shield_cmd="status"))

    mock_shield.status.assert_called_once_with()
    assert "Mode" in out.getvalue()
    assert "hook" in out.getvalue()


def test_dispatch_partial_task_selector_exits() -> None:
    """Providing only one half of the project/task selector exits cleanly."""
    args = argparse.Namespace(cmd="shield", shield_cmd="status", project_id="proj", task_id=None)
    with (
        patch("sys.stderr", new_callable=StringIO) as err,
        pytest.raises(SystemExit) as exc_info,
    ):
        dispatch(args)

    assert exc_info.value.code == 1
    assert "both" in err.getvalue()


@pytest.mark.parametrize(
    ("shield_cmd", "shield_method", "shield_result", "expected_text"),
    [
        pytest.param("status", "state", MagicMock(value="up"), "up", id="task-status"),
    ],
)
@patch("terok.cli.commands.shield._resolve_task", return_value=("proj-cli-1", MOCK_TASK_DIR_1))
@patch("terok.cli.commands.shield.make_shield")
def test_dispatch_task_scoped_commands(
    mock_make: MagicMock,
    _resolve: MagicMock,
    shield_cmd: str,
    shield_method: str,
    shield_result: object,
    expected_text: str,
) -> None:
    """Task-scoped shield commands resolve the task and delegate to the shield object."""
    mock_shield = MagicMock()
    getattr(mock_shield, shield_method).return_value = shield_result
    mock_make.return_value = mock_shield

    args = argparse.Namespace(cmd="shield", shield_cmd=shield_cmd, project_id="proj", task_id="1")
    with patch("sys.stdout", new_callable=StringIO) as out:
        assert dispatch(args)

    getattr(mock_shield, shield_method).assert_called_once_with("proj-cli-1")
    assert expected_text in out.getvalue()


@patch("terok.cli.commands.shield.make_shield")
def test_dispatch_preview_all_without_down_prints_error(mock_make: MagicMock) -> None:
    """``preview --all`` without ``--down`` fails with a clean CLI error."""
    mock_shield = MagicMock()
    mock_shield.preview.side_effect = ValueError("--all requires --down")
    mock_make.return_value = mock_shield

    args = argparse.Namespace(cmd="shield", shield_cmd="preview", down=False, allow_all=True)
    with (
        patch("sys.stderr", new_callable=StringIO) as err,
        pytest.raises(SystemExit) as exc_info,
    ):
        dispatch(args)

    assert exc_info.value.code == 1
    assert "--all requires --down" in err.getvalue()


@patch("terok.cli.commands.shield._resolve_task", return_value=("proj-cli-1", MOCK_TASK_DIR_1))
@patch("terok.cli.commands.shield.make_shield")
def test_dispatch_exec_error_surfaces_details(
    mock_make: MagicMock,
    _resolve: MagicMock,
) -> None:
    """NFT execution errors surface the actual error details."""
    mock_shield = MagicMock()
    mock_shield.state.side_effect = ExecError(["nft", "list"], 1, "no such process")
    mock_make.return_value = mock_shield

    args = argparse.Namespace(cmd="shield", shield_cmd="status", project_id="proj", task_id="1")
    with (
        patch("sys.stderr", new_callable=StringIO) as err,
        pytest.raises(SystemExit) as exc_info,
    ):
        dispatch(args)

    assert exc_info.value.code == 1
    assert "shield operation failed" in err.getvalue()
    assert "task 1" in err.getvalue()


@patch("terok.cli.commands.shield._resolve_task", return_value=("proj-cli-1", MOCK_TASK_DIR_1))
@patch("terok.cli.commands.shield.make_shield")
def test_dispatch_runtime_error_prints_message(
    mock_make: MagicMock,
    _resolve: MagicMock,
) -> None:
    """Runtime errors from shield commands are surfaced cleanly."""
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
        pytest.raises(SystemExit) as exc_info,
    ):
        dispatch(args)

    assert exc_info.value.code == 1
    assert "No IPs allowed" in err.getvalue()


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        pytest.param({"root": True, "user": False}, {"root": True, "user": False}, id="setup-root"),
        pytest.param({"root": False, "user": True}, {"root": False, "user": True}, id="setup-user"),
    ],
)
@patch("terok_sandbox.run_setup")
def test_setup_dispatch(
    mock_setup: MagicMock,
    kwargs: dict[str, bool],
    expected: dict[str, bool],
) -> None:
    """The setup subcommand delegates to the facade with the parsed flags."""
    assert dispatch(argparse.Namespace(cmd="shield", shield_cmd="setup", **kwargs))
    mock_setup.assert_called_once_with(**expected)


@patch("terok.lib.orchestration.tasks.load_task_meta", return_value=({"mode": None}, None))
@patch("terok.lib.core.projects.load_project")
def test_resolve_task_errors(
    mock_project: MagicMock,
    _meta: MagicMock,
) -> None:
    """Task resolution rejects tasks that have never been run."""
    mock_project.return_value = MagicMock(id="proj")
    with pytest.raises(ValueError, match="has never been run"):
        _resolve_task("proj", "1")


# ── _persist_desired_state ───────────────────────────────


class TestPersistDesiredState:
    """Verify that shield up/down dispatch persists the desired state file."""

    def test_up_persists(self, tmp_path: Path) -> None:
        """``shield up`` writes 'up' to the desired state file."""
        from terok.cli.commands.shield import _persist_desired_state

        _persist_desired_state("up", tmp_path, {})
        assert (tmp_path / "shield_desired_state").read_text().strip() == "up"

    def test_down_persists(self, tmp_path: Path) -> None:
        """``shield down`` writes 'down' to the desired state file."""
        from terok.cli.commands.shield import _persist_desired_state

        _persist_desired_state("down", tmp_path, {})
        assert (tmp_path / "shield_desired_state").read_text().strip() == "down"

    def test_down_all_persists(self, tmp_path: Path) -> None:
        """``shield down --all`` writes 'down_all' to the desired state file."""
        from terok.cli.commands.shield import _persist_desired_state

        _persist_desired_state("down", tmp_path, {"allow_all": True})
        assert (tmp_path / "shield_desired_state").read_text().strip() == "down_all"

    def test_unrelated_command_noop(self, tmp_path: Path) -> None:
        """Non up/down commands do not create a state file."""
        from terok.cli.commands.shield import _persist_desired_state

        _persist_desired_state("rules", tmp_path, {})
        assert not (tmp_path / "shield_desired_state").exists()

    def test_oserror_swallowed(self, tmp_path: Path) -> None:
        """OSError during write is logged to stderr but does not raise."""
        from terok.cli.commands.shield import _persist_desired_state

        bad_dir = tmp_path / "no" / "such" / "dir"
        # Should not raise — the error is printed to stderr
        _persist_desired_state("up", bad_dir, {})

    @patch("terok.cli.commands.shield._resolve_task")
    @patch("terok.cli.commands.shield.make_shield")
    def test_dispatch_up_persists_state(
        self, mock_make: MagicMock, mock_resolve: MagicMock, tmp_path: Path
    ) -> None:
        """Full dispatch of ``shield up`` persists the desired state."""
        mock_resolve.return_value = ("proj-cli-1", tmp_path)
        mock_shield = MagicMock()
        mock_make.return_value = mock_shield

        args = argparse.Namespace(cmd="shield", shield_cmd="up", project_id="proj", task_id="1")
        assert dispatch(args)
        mock_shield.up.assert_called_once()
        assert (tmp_path / "shield_desired_state").read_text().strip() == "up"

    @patch("terok.cli.commands.shield._resolve_task")
    @patch("terok.cli.commands.shield.make_shield")
    def test_dispatch_down_persists_state(
        self, mock_make: MagicMock, mock_resolve: MagicMock, tmp_path: Path
    ) -> None:
        """Full dispatch of ``shield down`` persists the desired state."""
        mock_resolve.return_value = ("proj-cli-1", tmp_path)
        mock_shield = MagicMock()
        mock_make.return_value = mock_shield

        args = argparse.Namespace(
            cmd="shield", shield_cmd="down", project_id="proj", task_id="1", allow_all=False
        )
        assert dispatch(args)
        mock_shield.down.assert_called_once()
        assert (tmp_path / "shield_desired_state").read_text().strip() == "down"
