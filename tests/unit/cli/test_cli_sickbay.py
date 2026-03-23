# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sickbay CLI command."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from terok_sandbox import GateServerStatus

from terok.cli.commands.sickbay import _cmd_sickbay
from tests.testgate import OUTDATED_UNITS_MESSAGE, make_gate_server_status


@pytest.mark.parametrize(
    ("status", "outdated", "systemd_available", "exit_code", "expected"),
    [
        pytest.param(
            make_gate_server_status("systemd", running=True),
            None,
            False,
            None,
            ["Gate server", "ok", "systemd"],
            id="all-ok",
        ),
        pytest.param(
            make_gate_server_status("systemd", running=True),
            OUTDATED_UNITS_MESSAGE,
            False,
            1,
            ["WARN", "outdated", "gate-server install"],
            id="outdated-units",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            True,
            1,
            ["WARN", "gate-server install"],
            id="not-running-with-systemd",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            False,
            1,
            ["WARN", "gate-server start"],
            id="not-running-without-systemd",
        ),
        pytest.param(
            make_gate_server_status("systemd"),
            None,
            False,
            2,
            ["ERROR", "not active"],
            id="socket-inactive",
        ),
    ],
)
def test_cmd_sickbay_reports_health(
    status: GateServerStatus,
    outdated: str | None,
    systemd_available: bool,
    exit_code: int | None,
    expected: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Sickbay exits with warning/error codes and prints useful remediation hints."""
    with (
        patch("terok.cli.commands.sickbay.get_server_status", return_value=status),
        patch("terok.cli.commands.sickbay.check_units_outdated", return_value=outdated),
        patch("terok.cli.commands.sickbay.is_systemd_available", return_value=systemd_available),
    ):
        if exit_code is None:
            _cmd_sickbay()
        else:
            with pytest.raises(SystemExit) as exc_info:
                _cmd_sickbay()
            assert exc_info.value.code == exit_code

    output = capsys.readouterr().out
    for needle in expected:
        assert needle in output
