# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok uninstall`` — symmetric teardown of ``terok setup``."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.uninstall import (
    _purge_credential_db,
    _uninstall_dbus_bridge,
    _uninstall_desktop_entry,
    _uninstall_sandbox_services,
    cmd_uninstall,
)

# ── Individual phase helpers ─────────────────────────────────────────────


@patch("terok.cli.commands._desktop_entry.uninstall_desktop_entry")
def test_desktop_entry_removed(
    mock_uninstall: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Desktop entry phase reports ok when uninstall succeeds."""
    assert _uninstall_desktop_entry() is True
    mock_uninstall.assert_called_once()
    assert "ok" in capsys.readouterr().out


@patch(
    "terok.cli.commands._desktop_entry.uninstall_desktop_entry",
    side_effect=Exception("boom"),
)
def test_desktop_entry_failure_reported(
    _mock_uninstall: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exceptions are caught and reported as FAIL, not raised."""
    assert _uninstall_desktop_entry() is False
    assert "FAIL" in capsys.readouterr().out


@patch("terok_clearance.uninstall_service")
@patch("terok_sandbox.uninstall_shield_bridge")
def test_dbus_bridge_removed(
    mock_reader: MagicMock, mock_uninstall: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Happy path: reader removed, clearance teardown delegated, ok reported."""
    assert _uninstall_dbus_bridge() is True
    mock_reader.assert_called_once()
    mock_uninstall.assert_called_once()
    assert "ok" in capsys.readouterr().out


@patch("terok_sandbox.uninstall_shield_bridge", side_effect=Exception("reader boom"))
def test_dbus_bridge_reader_failure_reported(
    _mock: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reader uninstall exception → FAIL and False return."""
    assert _uninstall_dbus_bridge() is False
    assert "FAIL" in capsys.readouterr().out


@patch("terok_sandbox.commands._handle_sandbox_uninstall")
def test_sandbox_services_default_passes_root_false(
    mock_handle: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Default invocation delegates to sandbox aggregator with root=False."""
    assert _uninstall_sandbox_services(root=False) is True
    mock_handle.assert_called_once_with(root=False)
    assert "ok" in capsys.readouterr().out


@patch("terok_sandbox.commands._handle_sandbox_uninstall")
def test_sandbox_services_root_flag_propagates(
    mock_handle: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--root`` reaches the aggregator so shield system hooks are removed too."""
    assert _uninstall_sandbox_services(root=True) is True
    mock_handle.assert_called_once_with(root=True)


@patch("terok_sandbox.commands._handle_sandbox_uninstall", side_effect=SystemExit("nope"))
def test_sandbox_services_systemexit_caught(
    _mock: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    """Sandbox aggregator raising SystemExit → FAIL, not bubbled."""
    assert _uninstall_sandbox_services(root=False) is False
    assert "FAIL" in capsys.readouterr().out


# ── Credential DB purge (opt-in) ─────────────────────────────────────────


@patch("terok_sandbox.SandboxConfig")
def test_purge_credentials_absent_is_success(
    mock_cfg: MagicMock, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """No DB on disk → already-absent is a clean success."""
    mock_cfg.return_value.db_path = tmp_path / "absent.db"
    assert _purge_credential_db() is True
    assert "ok" in capsys.readouterr().out


@patch("terok_sandbox.SandboxConfig")
def test_purge_credentials_removes_existing_file(
    mock_cfg: MagicMock, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DB file → unlinked, ok reported."""
    db = tmp_path / "credentials.db"
    db.write_text("")
    mock_cfg.return_value.db_path = db
    assert _purge_credential_db() is True
    assert not db.exists()


# ── Orchestrator ─────────────────────────────────────────────────────────


class TestCmdUninstall:
    """End-to-end phase ordering and opt-out flag propagation."""

    def _spies(self):
        return (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry"),
            patch("terok.cli.commands.uninstall._uninstall_dbus_bridge"),
            patch("terok.cli.commands.uninstall._uninstall_sandbox_services"),
            patch("terok.cli.commands.uninstall._purge_credential_db"),
        )

    def test_default_runs_every_non_optional_phase(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True) as de,
            patch("terok.cli.commands.uninstall._uninstall_dbus_bridge", return_value=True) as db,
            patch(
                "terok.cli.commands.uninstall._uninstall_sandbox_services", return_value=True
            ) as sb,
            patch("terok.cli.commands.uninstall._purge_credential_db", return_value=True) as purge,
        ):
            cmd_uninstall()

        de.assert_called_once()
        db.assert_called_once()
        sb.assert_called_once_with(root=False)
        purge.assert_not_called()  # opt-in

    def test_phases_run_in_reverse_install_order(self) -> None:
        order: list[str] = []
        with (
            patch(
                "terok.cli.commands.uninstall._uninstall_desktop_entry",
                side_effect=lambda: order.append("desktop") or True,
            ),
            patch(
                "terok.cli.commands.uninstall._uninstall_dbus_bridge",
                side_effect=lambda: order.append("dbus") or True,
            ),
            patch(
                "terok.cli.commands.uninstall._uninstall_sandbox_services",
                side_effect=lambda root: order.append("sandbox") or True,
            ),
        ):
            cmd_uninstall()
        assert order == ["desktop", "dbus", "sandbox"]

    def test_opt_outs_skip_exactly_their_phase(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True) as de,
            patch("terok.cli.commands.uninstall._uninstall_dbus_bridge", return_value=True) as db,
            patch(
                "terok.cli.commands.uninstall._uninstall_sandbox_services", return_value=True
            ) as sb,
        ):
            cmd_uninstall(no_desktop_entry=True, no_dbus_bridge=True, no_sandbox=True)

        de.assert_not_called()
        db.assert_not_called()
        sb.assert_not_called()

    def test_purge_credentials_runs_only_when_requested(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True),
            patch("terok.cli.commands.uninstall._uninstall_dbus_bridge", return_value=True),
            patch("terok.cli.commands.uninstall._uninstall_sandbox_services", return_value=True),
            patch("terok.cli.commands.uninstall._purge_credential_db", return_value=True) as purge,
        ):
            cmd_uninstall(purge_credentials=True)
        purge.assert_called_once()

    def test_failed_phase_raises_exit_1(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True),
            patch("terok.cli.commands.uninstall._uninstall_dbus_bridge", return_value=False),
            patch("terok.cli.commands.uninstall._uninstall_sandbox_services", return_value=True),
            pytest.raises(SystemExit) as exc,
        ):
            cmd_uninstall()
        assert exc.value.code == 1


# ── Dispatch wiring ──────────────────────────────────────────────────────


class TestDispatch:
    """Wiring from argparse.Namespace into ``cmd_uninstall``."""

    def test_dispatch_returns_false_for_non_uninstall(self) -> None:
        from argparse import Namespace

        from terok.cli.commands.uninstall import dispatch

        assert dispatch(Namespace(cmd="setup")) is False

    def test_dispatch_invokes_cmd_uninstall(self) -> None:
        from argparse import Namespace

        from terok.cli.commands.uninstall import dispatch

        args = Namespace(
            cmd="uninstall",
            root=True,
            no_desktop_entry=False,
            no_dbus_bridge=False,
            no_sandbox=False,
            purge_credentials=False,
        )
        with patch("terok.cli.commands.uninstall.cmd_uninstall") as mock_cmd:
            assert dispatch(args) is True
        mock_cmd.assert_called_once_with(
            root=True,
            no_desktop_entry=False,
            no_dbus_bridge=False,
            no_sandbox=False,
            purge_credentials=False,
        )
