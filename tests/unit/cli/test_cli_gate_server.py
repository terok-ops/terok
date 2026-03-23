# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for gate-server CLI commands."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest
from terok_sandbox import GateServerStatus

from terok.cli.commands.gate_server import (
    _cmd_install,
    _cmd_start,
    _cmd_status,
    _cmd_stop,
    _cmd_uninstall,
)
from tests.testgate import OUTDATED_UNITS_MESSAGE, make_gate_server_status


@pytest.mark.parametrize(
    ("command", "action_path", "expected"),
    [
        pytest.param(
            _cmd_install,
            "terok.cli.commands.gate_server.install_systemd_units",
            "installed",
            id="install",
        ),
        pytest.param(
            _cmd_uninstall,
            "terok.cli.commands.gate_server.uninstall_systemd_units",
            "removed",
            id="uninstall",
        ),
    ],
)
def test_systemd_commands_succeed(
    command: Callable[[], None],
    action_path: str,
    expected: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Install and uninstall delegate to their systemd helpers when available."""
    with (
        patch("terok.cli.commands.gate_server.is_systemd_available", return_value=True),
        patch(action_path) as mock_action,
    ):
        command()

    mock_action.assert_called_once()
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize("command", [_cmd_install, _cmd_uninstall], ids=["install", "uninstall"])
def test_systemd_commands_require_systemd(command: Callable[[], None]) -> None:
    """Install and uninstall exit with an error when systemd is unavailable."""
    with patch("terok.cli.commands.gate_server.is_systemd_available", return_value=False):
        with pytest.raises(SystemExit):
            command()


def test_start_invokes_start_daemon(capsys: pytest.CaptureFixture[str]) -> None:
    """Start delegates to the daemon helper when the server is stopped."""
    with (
        patch(
            "terok.cli.commands.gate_server.get_server_status",
            return_value=make_gate_server_status(),
        ),
        patch("terok.cli.commands.gate_server.start_daemon") as mock_start,
    ):
        _cmd_start(port=9999)

    mock_start.assert_called_once_with(port=9999)
    assert "Gate server started" in capsys.readouterr().out


def test_start_rejects_already_running_server() -> None:
    """Start exits when the gate server is already running."""
    with patch(
        "terok.cli.commands.gate_server.get_server_status",
        return_value=make_gate_server_status("systemd", running=True),
    ):
        with pytest.raises(SystemExit):
            _cmd_start(port=None)


@pytest.mark.parametrize(
    ("status", "daemon_running", "expected", "should_stop"),
    [
        pytest.param(
            make_gate_server_status("daemon", running=True),
            True,
            "Gate server stopped",
            True,
            id="daemon-running",
        ),
        pytest.param(
            make_gate_server_status("none"),
            False,
            "not running",
            False,
            id="already-stopped",
        ),
        pytest.param(
            make_gate_server_status("systemd", running=True),
            False,
            "managed by systemd",
            False,
            id="systemd-managed",
        ),
    ],
)
def test_stop_behaviour(
    status: GateServerStatus,
    daemon_running: bool,
    expected: str,
    should_stop: bool,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop handles daemon, stopped, and systemd-managed servers correctly."""
    with (
        patch("terok.cli.commands.gate_server.get_server_status", return_value=status),
        patch("terok.cli.commands.gate_server.is_daemon_running", return_value=daemon_running),
        patch("terok.cli.commands.gate_server.stop_daemon") as mock_stop,
    ):
        _cmd_stop()

    assert (mock_stop.call_count == 1) is should_stop
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    ("status", "outdated", "systemd_available", "expected", "unexpected"),
    [
        pytest.param(
            make_gate_server_status("daemon", running=True),
            None,
            False,
            ["Mode:   daemon", "Status: running", "Port:   9418"],
            ["Warning", "Hint:"],
            id="running",
        ),
        pytest.param(
            make_gate_server_status("systemd", running=True),
            OUTDATED_UNITS_MESSAGE,
            True,
            ["Warning", "outdated", "gate-server install"],
            ["Hint:"],
            id="outdated-units",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            True,
            ["Mode:   none", "Status: stopped", "gate-server install"],
            ["Warning:"],
            id="stopped-with-systemd",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            False,
            ["Mode:   none", "Status: stopped"],
            ["gate-server install", "Warning:"],
            id="stopped-without-systemd",
        ),
    ],
)
def test_status_outputs_expected_information(
    status: GateServerStatus,
    outdated: str | None,
    systemd_available: bool,
    expected: list[str],
    unexpected: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status reports runtime details, warnings, and hints as appropriate."""
    with (
        patch("terok.cli.commands.gate_server.get_server_status", return_value=status),
        patch("terok.cli.commands.gate_server.check_units_outdated", return_value=outdated),
        patch(
            "terok.cli.commands.gate_server.is_systemd_available",
            return_value=systemd_available,
        ),
    ):
        _cmd_status()

    output = capsys.readouterr().out
    for needle in expected:
        assert needle in output
    for needle in unexpected:
        assert needle not in output
