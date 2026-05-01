# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok uninstall`` — symmetric teardown of ``terok setup``.

The sandbox aggregator owns the full teardown — bridge + clearance
+ gate + vault + shield — and bridge teardown is now its own
first-class phase (``run_bridge_uninstall_phase`` in
``terok_sandbox._setup``), not the earlier ``uninstall_shield_bridge``
workaround called from terok directly.  Terok's own phases shrink
to desktop-entry removal and optional credential-DB purge; the
``_uninstall_sandbox_stack`` wrapper is a thin delegating call.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from terok.cli.commands.uninstall import (
    _purge_credential_db,
    _uninstall_desktop_entry,
    _uninstall_sandbox_stack,
    cmd_uninstall,
)

# ── Individual phase helpers ─────────────────────────────────────────────


class TestUninstallDesktopEntry:
    """Desktop entry phase — thin wrapper over ``uninstall_desktop_entry``."""

    @patch("terok.cli.commands._desktop_entry.uninstall_desktop_entry")
    def test_ok_when_uninstall_succeeds(
        self, mock_uninstall: MagicMock, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert _uninstall_desktop_entry() is True
        mock_uninstall.assert_called_once()
        assert "ok" in capsys.readouterr().out

    @patch(
        "terok.cli.commands._desktop_entry.uninstall_desktop_entry",
        side_effect=PermissionError("read-only xdg dir"),
    )
    def test_fail_on_raise(self, _uninstall: MagicMock, capsys: pytest.CaptureFixture[str]) -> None:
        assert _uninstall_desktop_entry() is False
        assert "FAIL" in capsys.readouterr().out


class TestUninstallSandboxStack:
    """Sandbox aggregator owns the full teardown — bridge + clearance + gate + vault + shield."""

    def test_happy_path_delegates_to_aggregator(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("terok_sandbox.sandbox_uninstall") as aggregator:
            assert _uninstall_sandbox_stack(root=False) is True
        aggregator.assert_called_once_with(root=False)
        out = capsys.readouterr().out
        assert "Sandbox stack" in out
        assert "removed" in out

    def test_aggregator_failure_reports_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch(
            "terok_sandbox.sandbox_uninstall",
            side_effect=SystemExit("aggregator reported one or more failed phases"),
        ):
            assert _uninstall_sandbox_stack(root=False) is False
        out = capsys.readouterr().out
        assert "FAIL" in out
        assert "aggregator reported one or more failed phases" in out

    def test_root_flag_threaded_to_aggregator(self) -> None:
        with patch("terok_sandbox.sandbox_uninstall") as aggregator:
            _uninstall_sandbox_stack(root=True)
        aggregator.assert_called_once_with(root=True)


class TestPurgeCredentialDb:
    """Credential-DB purge is only run on ``--purge-credentials``."""

    def test_absent_db_is_ok(self, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
        """No DB on disk → stage reports ok (nothing to remove)."""
        from terok_sandbox import SandboxConfig

        fake_cfg = MagicMock(spec=SandboxConfig)
        fake_cfg.db_path = tmp_path / "nothing.db"  # does not exist
        with patch("terok_sandbox.SandboxConfig", return_value=fake_cfg):
            assert _purge_credential_db() is True
        assert "already absent" in capsys.readouterr().out

    def test_removes_existing_db(self, tmp_path, capsys: pytest.CaptureFixture[str]) -> None:
        from terok_sandbox import SandboxConfig

        db = tmp_path / "credentials.db"
        db.write_text("secrets")
        fake_cfg = MagicMock(spec=SandboxConfig)
        fake_cfg.db_path = db
        with patch("terok_sandbox.SandboxConfig", return_value=fake_cfg):
            assert _purge_credential_db() is True
        assert not db.exists()


# ── cmd_uninstall orchestration ───────────────────────────────────────


class TestCmdUninstall:
    """``cmd_uninstall`` runs desktop → sandbox-stack → [credentials] in order."""

    def test_default_runs_desktop_and_sandbox(self) -> None:
        with (
            patch(
                "terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True
            ) as desktop,
            patch(
                "terok.cli.commands.uninstall._uninstall_sandbox_stack", return_value=True
            ) as sandbox,
            patch("terok.cli.commands.uninstall._purge_credential_db") as purge,
        ):
            cmd_uninstall()
        desktop.assert_called_once()
        sandbox.assert_called_once_with(root=False)
        purge.assert_not_called()

    def test_no_desktop_entry_skips_desktop(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry") as desktop,
            patch("terok.cli.commands.uninstall._uninstall_sandbox_stack", return_value=True),
        ):
            cmd_uninstall(no_desktop_entry=True)
        desktop.assert_not_called()

    def test_no_sandbox_skips_sandbox_stack(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True),
            patch("terok.cli.commands.uninstall._uninstall_sandbox_stack") as sandbox,
        ):
            cmd_uninstall(no_sandbox=True)
        sandbox.assert_not_called()

    def test_purge_credentials_runs_purge_phase(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True),
            patch("terok.cli.commands.uninstall._uninstall_sandbox_stack", return_value=True),
            patch("terok.cli.commands.uninstall._purge_credential_db", return_value=True) as purge,
        ):
            cmd_uninstall(purge_credentials=True)
        purge.assert_called_once()

    def test_failing_phase_exits_nonzero(self) -> None:
        """Any phase reporting False trips a SystemExit(1) after the summary."""
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True),
            patch("terok.cli.commands.uninstall._uninstall_sandbox_stack", return_value=False),
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_uninstall()
        assert exc.value.code == 1

    def test_root_flag_threaded_to_sandbox_stack(self) -> None:
        with (
            patch("terok.cli.commands.uninstall._uninstall_desktop_entry", return_value=True),
            patch(
                "terok.cli.commands.uninstall._uninstall_sandbox_stack", return_value=True
            ) as sandbox,
        ):
            cmd_uninstall(root=True)
        sandbox.assert_called_once_with(root=True)
