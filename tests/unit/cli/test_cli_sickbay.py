# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sickbay CLI command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox import GateServerStatus

from terok.cli.commands.sickbay import _check_credential_proxy, _check_shield, _cmd_sickbay
from tests.testfs import MOCK_BASE
from tests.testgate import OUTDATED_UNITS_MESSAGE, make_gate_server_status

MOCK_PROXY_SOCKET = MOCK_BASE / "run" / "credential-proxy.sock"
MOCK_PROXY_DB = MOCK_BASE / "proxy" / "credentials.db"


def _make_proxy_status(*, running: bool = True, mode: str = "systemd") -> MagicMock:
    """Return a mock CredentialProxyStatus."""
    s = MagicMock()
    s.running = running
    s.mode = mode
    s.socket_path = MOCK_PROXY_SOCKET
    s.db_path = MOCK_PROXY_DB
    s.credentials_stored = ["claude", "gh"]
    return s


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
            ["WARN", "gate start"],
            id="not-running-with-systemd",
        ),
        pytest.param(
            make_gate_server_status("none"),
            None,
            False,
            1,
            ["WARN", "gate start"],
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
    tmp_path: Path,
) -> None:
    """Sickbay exits with warning/error codes and prints useful remediation hints."""
    import json

    # SSH agent check needs a valid ssh-keys.json with an existing key file
    dummy_key = tmp_path / "id"
    dummy_key.write_text("k")
    (tmp_path / "id.pub").write_text("pub")
    ssh_keys = tmp_path / "ssh-keys.json"
    ssh_keys.write_text(
        json.dumps({"p": {"private_key": str(dummy_key), "public_key": str(tmp_path / "id.pub")}})
    )

    mock_ec = MagicMock(health="ok", hooks="per-container", dns_tier="dnsmasq")
    mock_cfg = MagicMock()
    mock_cfg.return_value.ssh_keys_json_path = ssh_keys
    with (
        patch("terok.cli.commands.sickbay.get_server_status", return_value=status),
        patch("terok.cli.commands.sickbay.check_units_outdated", return_value=outdated),
        patch("terok.cli.commands.sickbay.is_systemd_available", return_value=systemd_available),
        patch("terok.cli.commands.sickbay.check_environment", return_value=mock_ec),
        patch("terok.cli.commands.sickbay.get_proxy_status", return_value=_make_proxy_status()),
        patch("terok.cli.commands.sickbay.is_proxy_systemd_available", return_value=False),
        patch("terok.cli.commands.sickbay.make_sandbox_config", mock_cfg),
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


@pytest.mark.parametrize(
    ("health", "setup_hint", "issues", "side_effect", "expected_status", "expected_detail"),
    [
        pytest.param(
            "bypass",
            "",
            [],
            None,
            "warn",
            "bypass_firewall_no_protection",
            id="bypass",
        ),
        pytest.param(
            "stale-hooks",
            "",
            [],
            None,
            "warn",
            "hooks outdated",
            id="stale-hooks",
        ),
        pytest.param(
            "setup-needed",
            "run 'terok shield setup --user'",
            ["nft not found"],
            None,
            "warn",
            "nft not found",
            id="setup-needed-with-hint",
        ),
        pytest.param(
            "setup-needed",
            "",
            [],
            None,
            "warn",
            "setup needed",
            id="setup-needed-no-hint",
        ),
        pytest.param(
            None,
            "",
            [],
            RuntimeError("nft binary not found"),
            "warn",
            "check failed",
            id="check-exception",
        ),
        pytest.param(
            "ok",
            "",
            [],
            None,
            "ok",
            "active",
            id="ok",
        ),
    ],
)
def test_check_shield_states(
    health: str | None,
    setup_hint: str,
    issues: list[str],
    side_effect: Exception | None,
    expected_status: str,
    expected_detail: str,
) -> None:
    """_check_shield maps EnvironmentCheck states to the correct severity and message."""
    mock_ec = MagicMock(
        health=health,
        hooks="per-container",
        dns_tier="dnsmasq",
        setup_hint=setup_hint,
        issues=issues,
    )
    with patch(
        "terok.cli.commands.sickbay.check_environment",
        return_value=mock_ec,
        side_effect=side_effect,
    ):
        status, label, detail = _check_shield()

    assert status == expected_status
    assert label == "Shield"
    assert expected_detail in detail


@pytest.mark.parametrize(
    ("running", "mode", "systemd_avail", "side_effect", "expected_status", "expected_detail"),
    [
        pytest.param(
            True,
            "systemd",
            False,
            None,
            "ok",
            "2 credential(s)",
            id="running-with-creds",
        ),
        pytest.param(
            False,
            "systemd",
            True,
            None,
            "error",
            "not active",
            id="socket-inactive",
        ),
        pytest.param(
            False,
            "none",
            True,
            None,
            "warn",
            "install",
            id="not-running-systemd-available",
        ),
        pytest.param(
            False,
            "none",
            False,
            None,
            "warn",
            "start",
            id="not-running-no-systemd",
        ),
        pytest.param(
            False,
            "none",
            False,
            OSError("socket gone"),
            "warn",
            "check failed",
            id="check-exception",
        ),
    ],
)
def test_check_credential_proxy_states(
    running: bool,
    mode: str,
    systemd_avail: bool,
    side_effect: Exception | None,
    expected_status: str,
    expected_detail: str,
) -> None:
    """_check_credential_proxy maps proxy states to the correct severity and message."""
    with (
        patch(
            "terok.cli.commands.sickbay.get_proxy_status",
            return_value=_make_proxy_status(running=running, mode=mode),
            side_effect=side_effect,
        ),
        patch(
            "terok.cli.commands.sickbay.is_proxy_systemd_available",
            return_value=systemd_avail,
        ),
    ):
        status, label, detail = _check_credential_proxy()

    assert status == expected_status
    assert label == "Credential proxy"
    assert expected_detail in detail
