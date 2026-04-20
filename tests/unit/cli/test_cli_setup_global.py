# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok setup`` — global bootstrap command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox import SelinuxCheckResult, SelinuxStatus, VaultUnreachableError

from terok.cli.commands.setup import (
    _check_dbus_send,
    _check_host_binaries,
    _check_selinux_policy,
    _disable_dbus_bridge,
    _disable_user_service,
    _enable_user_service,
    _ensure_bridge_reader,
    _ensure_dbus_bridge,
    _ensure_dbus_hub,
    _ensure_gate,
    _ensure_shield,
    _ensure_vault,
    cmd_setup,
    dispatch,
)
from tests.testfs import FAKE_CREDENTIALS_DIR, MOCK_BASE
from tests.testgate import make_gate_server_status

MOCK_VAULT_SOCKET = MOCK_BASE / "run" / "vault.sock"
MOCK_VAULT_DB = FAKE_CREDENTIALS_DIR / "credentials.db"

# ── Host binary checks ──────────────────────────────────────────────────


@patch("terok.cli.commands.setup.shutil.which")
def test_host_binaries_all_found(mock_which: MagicMock, capsys: pytest.CaptureFixture) -> None:
    """All mandatory and recommended binaries present."""
    mock_which.return_value = "/usr/bin/whatever"
    assert _check_host_binaries(color=False) is True
    out = capsys.readouterr().out
    assert "podman" in out
    assert "git" in out


@patch("terok.cli.commands.setup.shutil.which")
def test_host_binaries_mandatory_missing(
    mock_which: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """Missing mandatory binary returns False."""
    mock_which.side_effect = lambda name: None if name == "podman" else "/usr/bin/x"
    assert _check_host_binaries(color=False) is False
    out = capsys.readouterr().out
    assert "FAIL" in out


@patch("terok.cli.commands.setup.shutil.which")
def test_host_binaries_recommended_missing(
    mock_which: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """Missing recommended binary still returns True."""
    mock_which.side_effect = lambda name: None if name == "dnsmasq" else "/usr/bin/x"
    assert _check_host_binaries(color=False) is True
    out = capsys.readouterr().out
    assert "WARN" in out


# ── Shield ───────────────────────────────────────────────────────────────


@patch("terok_sandbox.check_environment")
def test_shield_already_installed(mock_env: MagicMock, capsys: pytest.CaptureFixture) -> None:
    """Shield hooks present → skip."""
    mock_env.return_value = MagicMock(health="ok")
    assert _ensure_shield(check_only=False, color=False) is True
    out = capsys.readouterr().out
    assert "active" in out


@patch("terok_sandbox.check_environment")
def test_shield_check_only_missing(mock_env: MagicMock, capsys: pytest.CaptureFixture) -> None:
    """Shield missing + check_only → report False."""
    mock_env.return_value = MagicMock(
        health="setup-needed", issues=["no hooks"], setup_hint="run X"
    )
    assert _ensure_shield(check_only=True, color=False) is False
    out = capsys.readouterr().out
    assert "FAIL" in out


@patch("terok_sandbox.setup_hooks_direct")
@patch("terok_sandbox.check_environment")
def test_shield_install_and_verify(
    mock_env: MagicMock, mock_setup: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """Shield missing → install → verify health = ok."""
    mock_env.side_effect = [
        MagicMock(health="setup-needed", issues=[], setup_hint=""),
        MagicMock(health="ok"),
    ]
    assert _ensure_shield(check_only=False, color=False) is True
    mock_setup.assert_called_once_with(root=False)
    out = capsys.readouterr().out
    assert "installed" in out


@patch("terok_sandbox.setup_hooks_direct")
@patch("terok_sandbox.check_environment")
def test_shield_install_verify_still_unhealthy(
    mock_env: MagicMock, _setup: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """Shield install succeeds but post-verify health is not ok → False."""
    mock_env.side_effect = [
        MagicMock(health="setup-needed", issues=[], setup_hint=""),
        MagicMock(health="stale-hooks"),
    ]
    assert _ensure_shield(check_only=False, color=False) is False
    out = capsys.readouterr().out
    assert "stale-hooks" in out


@patch("terok_sandbox.setup_hooks_direct", side_effect=RuntimeError("hook install boom"))
@patch("terok_sandbox.check_environment")
def test_shield_install_exception(
    mock_env: MagicMock, _setup: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """Shield install raises → caught, returns False."""
    mock_env.return_value = MagicMock(health="setup-needed", issues=[], setup_hint="")
    assert _ensure_shield(check_only=False, color=False) is False
    out = capsys.readouterr().out
    assert "FAIL" in out


@patch("terok_sandbox.check_environment")
def test_shield_bypass_active(mock_env: MagicMock, capsys: pytest.CaptureFixture) -> None:
    """Shield in bypass mode → warn but succeed."""
    mock_env.return_value = MagicMock(health="bypass")
    assert _ensure_shield(check_only=False, color=False) is True
    out = capsys.readouterr().out
    assert "bypass" in out


# ── Vault ──────────────────────────────────────────────────────────────


def _make_vault_status(*, running: bool = True, mode: str = "systemd") -> MagicMock:
    s = MagicMock()
    s.running = running
    s.mode = mode
    return s


@patch("terok_sandbox.get_vault_status")
@patch("terok_sandbox.ensure_vault_reachable")
@patch("terok_sandbox.is_vault_socket_active", return_value=True)
def test_vault_check_reachable(
    _sock: MagicMock, _reach: MagicMock, mock_status: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """check_only mode: vault reachable → ok."""
    mock_status.return_value = _make_vault_status(running=True)
    assert _ensure_vault(check_only=True, color=False) is True
    out = capsys.readouterr().out
    assert "reachable" in out


@patch(
    "terok_sandbox.ensure_vault_reachable",
    side_effect=VaultUnreachableError(socket_path=MOCK_VAULT_SOCKET, db_path=MOCK_VAULT_DB),
)
@patch("terok_sandbox.is_vault_socket_active", return_value=True)
def test_vault_check_unreachable(
    _sock: MagicMock, _reach: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """check_only mode: vault installed but unreachable → FAIL."""
    assert _ensure_vault(check_only=True, color=False) is False
    out = capsys.readouterr().out
    assert "NOT reachable" in out


@patch(
    "terok_sandbox.ensure_vault_reachable",
    side_effect=VaultUnreachableError(socket_path=MOCK_VAULT_SOCKET, db_path=MOCK_VAULT_DB),
)
@patch("terok_sandbox.is_vault_socket_active", return_value=False)
def test_vault_check_not_installed(
    _sock: MagicMock, _reach: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """check_only mode: vault not even installed → reports 'not installed'."""
    assert _ensure_vault(check_only=True, color=False) is False
    out = capsys.readouterr().out
    assert "not installed" in out


@patch("terok_sandbox.get_vault_status")
@patch("terok_sandbox.ensure_vault_reachable")
@patch("terok_sandbox.install_vault_systemd")
@patch("terok_executor.ensure_vault_routes")
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.uninstall_vault_systemd")
@patch("terok_sandbox.stop_vault")
def test_vault_reinstall_and_verify(
    _stop: MagicMock,
    _uninstall: MagicMock,
    _cfg: MagicMock,
    _routes: MagicMock,
    _install: MagicMock,
    _reach: MagicMock,
    mock_status: MagicMock,
) -> None:
    """Install mode: clean reinstall → verify reachable → ok."""
    mock_status.return_value = _make_vault_status(running=True)
    assert _ensure_vault(check_only=False, color=False) is True
    _stop.assert_called_once()
    _uninstall.assert_called_once()
    _install.assert_called_once()
    _reach.assert_called_once()


@patch("terok_sandbox.install_vault_systemd", side_effect=RuntimeError("install boom"))
@patch("terok_executor.ensure_vault_routes")
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.uninstall_vault_systemd")
@patch("terok_sandbox.stop_vault")
def test_vault_install_fails(
    _stop: MagicMock,
    _uninstall: MagicMock,
    _cfg: MagicMock,
    _routes: MagicMock,
    _install: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """Install mode: install raises → returns False."""
    assert _ensure_vault(check_only=False, color=False) is False
    out = capsys.readouterr().out
    assert "install failed" in out


@patch(
    "terok_sandbox.ensure_vault_reachable",
    side_effect=VaultUnreachableError(socket_path=MOCK_VAULT_SOCKET, db_path=MOCK_VAULT_DB),
)
@patch("terok_sandbox.install_vault_systemd")
@patch("terok_executor.ensure_vault_routes")
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.uninstall_vault_systemd")
@patch("terok_sandbox.stop_vault")
def test_vault_installed_but_unreachable(
    _stop: MagicMock,
    _uninstall: MagicMock,
    _cfg: MagicMock,
    _routes: MagicMock,
    _install: MagicMock,
    _reach: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """Install mode: installed ok but TCP probe fails → returns False with journal hint."""
    assert _ensure_vault(check_only=False, color=False) is False
    out = capsys.readouterr().out
    assert "NOT reachable" in out
    assert "journalctl" in out


# ── Gate server ──────────────────────────────────────────────────────────


@patch("terok_sandbox.ensure_server_reachable")
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_check_running_and_reachable(
    mock_status: MagicMock,
    _cfg: MagicMock,
    mock_reach: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """check_only: gate running + reachable → ok."""
    mock_status.return_value = make_gate_server_status("systemd", running=True)
    assert _ensure_gate(check_only=True, color=False) is True
    mock_reach.assert_called_once()
    out = capsys.readouterr().out
    assert "reachable" in out


@patch("terok_sandbox.ensure_server_reachable", side_effect=SystemExit("stale"))
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_check_running_but_unreachable(
    mock_status: MagicMock,
    _cfg: MagicMock,
    _reach: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """check_only: gate process exists but TCP unreachable → FAIL."""
    mock_status.return_value = make_gate_server_status("systemd", running=True)
    assert _ensure_gate(check_only=True, color=False) is False
    out = capsys.readouterr().out
    assert "NOT reachable" in out


@patch("terok_sandbox.ensure_server_reachable")
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_check_systemd_socket_reachable(
    mock_status: MagicMock,
    _cfg: MagicMock,
    mock_reach: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """check_only: gate socket installed (not running) + reachable after activation → ok."""
    mock_status.return_value = make_gate_server_status("systemd", running=False)
    assert _ensure_gate(check_only=True, color=False) is True
    mock_reach.assert_called_once()


@patch("terok_sandbox.ensure_server_reachable", side_effect=SystemExit("down"))
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_check_systemd_unreachable(
    mock_status: MagicMock,
    _cfg: MagicMock,
    _reach: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """check_only: gate socket installed but service won't start → FAIL."""
    mock_status.return_value = make_gate_server_status("systemd", running=False)
    assert _ensure_gate(check_only=True, color=False) is False
    out = capsys.readouterr().out
    assert "NOT reachable" in out


@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_check_not_installed(
    mock_status: MagicMock, _cfg: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """check_only: gate not installed → FAIL."""
    mock_status.return_value = make_gate_server_status("none")
    assert _ensure_gate(check_only=True, color=False) is False
    out = capsys.readouterr().out
    assert "not installed" in out


@patch("terok_sandbox.ensure_server_reachable")
@patch("terok_sandbox.install_systemd_units")
@patch("terok_sandbox.uninstall_systemd_units")
@patch("terok_sandbox.stop_daemon")
@patch("terok_sandbox.is_systemd_available", return_value=True)
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_reinstall_and_verify(
    mock_status: MagicMock,
    _cfg: MagicMock,
    _systemd: MagicMock,
    _stop: MagicMock,
    _uninstall: MagicMock,
    _install: MagicMock,
    _reach: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """Install mode: clean reinstall → verify reachable → ok."""
    mock_status.return_value = make_gate_server_status("none")
    assert _ensure_gate(check_only=False, color=False) is True
    _stop.assert_called_once()
    _uninstall.assert_called_once()
    _install.assert_called_once()
    _reach.assert_called_once()


@patch("terok_sandbox.install_systemd_units", side_effect=RuntimeError("unit boom"))
@patch("terok_sandbox.uninstall_systemd_units")
@patch("terok_sandbox.stop_daemon")
@patch("terok_sandbox.is_systemd_available", return_value=True)
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_install_fails(
    mock_status: MagicMock,
    _cfg: MagicMock,
    _systemd: MagicMock,
    _stop: MagicMock,
    _uninstall: MagicMock,
    _install: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """Install mode: install raises → returns False."""
    mock_status.return_value = make_gate_server_status("none")
    assert _ensure_gate(check_only=False, color=False) is False
    out = capsys.readouterr().out
    assert "install failed" in out


@patch("terok_sandbox.ensure_server_reachable", side_effect=SystemExit("port dead"))
@patch("terok_sandbox.install_systemd_units")
@patch("terok_sandbox.uninstall_systemd_units")
@patch("terok_sandbox.stop_daemon")
@patch("terok_sandbox.is_systemd_available", return_value=True)
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_installed_but_unreachable(
    mock_status: MagicMock,
    _cfg: MagicMock,
    _systemd: MagicMock,
    _stop: MagicMock,
    _uninstall: MagicMock,
    _install: MagicMock,
    _reach: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """Install mode: installed ok but TCP probe fails → returns False."""
    mock_status.return_value = make_gate_server_status("none")
    assert _ensure_gate(check_only=False, color=False) is False
    out = capsys.readouterr().out
    assert "NOT reachable" in out


@patch("terok_sandbox.is_systemd_available", return_value=False)
@patch("terok.lib.core.config.make_sandbox_config")
@patch("terok_sandbox.get_server_status")
def test_gate_no_systemd_skips(
    mock_status: MagicMock, _cfg: MagicMock, _systemd: MagicMock, capsys: pytest.CaptureFixture
) -> None:
    """Gate missing + no systemd → skip gracefully."""
    mock_status.return_value = make_gate_server_status("none")
    assert _ensure_gate(check_only=False, color=False) is True
    out = capsys.readouterr().out
    assert "systemd not available" in out


# ── cmd_setup integration ───────────────────────────────────────────────


@patch("terok.cli.commands.setup._ensure_dbus_bridge", return_value=True)
@patch("terok.cli.commands.setup._ensure_gate", return_value=True)
@patch("terok.cli.commands.setup._ensure_vault", return_value=True)
@patch("terok.cli.commands.setup._ensure_shield", return_value=True)
@patch("terok.cli.commands.setup._check_host_binaries", return_value=True)
def test_cmd_setup_all_ok(
    _bins: MagicMock,
    _shield: MagicMock,
    _proxy: MagicMock,
    _gate: MagicMock,
    _bridge: MagicMock,
    capsys: pytest.CaptureFixture,
) -> None:
    """All steps succeed → prints summary with next steps."""
    cmd_setup(check_only=False)
    out = capsys.readouterr().out
    assert "Setup complete" in out
    assert "project wizard" in out


@patch("terok.cli.commands.setup._ensure_dbus_bridge", return_value=True)
@patch("terok.cli.commands.setup._ensure_gate", return_value=True)
@patch("terok.cli.commands.setup._ensure_vault", return_value=True)
@patch("terok.cli.commands.setup._ensure_shield", return_value=True)
@patch("terok.cli.commands.setup._check_host_binaries", return_value=False)
def test_cmd_setup_missing_binary_exits_2(
    _bins: MagicMock,
    _shield: MagicMock,
    _proxy: MagicMock,
    _gate: MagicMock,
    _bridge: MagicMock,
) -> None:
    """Missing mandatory binary → exit code 2."""
    with pytest.raises(SystemExit, match="2"):
        cmd_setup(check_only=False)


@patch("terok.cli.commands.setup._ensure_dbus_bridge", return_value=True)
@patch("terok.cli.commands.setup._ensure_gate", return_value=False)
@patch("terok.cli.commands.setup._ensure_vault", return_value=True)
@patch("terok.cli.commands.setup._ensure_shield", return_value=True)
@patch("terok.cli.commands.setup._check_host_binaries", return_value=True)
def test_cmd_setup_service_failure_exits_1(
    _bins: MagicMock,
    _shield: MagicMock,
    _proxy: MagicMock,
    _gate: MagicMock,
    _bridge: MagicMock,
) -> None:
    """Service installation failure → exit code 1."""
    with pytest.raises(SystemExit, match="1"):
        cmd_setup(check_only=False)


# ── SELinux prereq check ───────────────────────────────────────────────


def _run_selinux_check(
    capsys: pytest.CaptureFixture, result: SelinuxCheckResult
) -> tuple[bool, str]:
    """Run ``_check_selinux_policy`` with ``check_selinux_status`` mocked.

    The decision tree itself is tested separately in terok-sandbox's
    ``test_selinux.py``.  Here we pin the setup-side rendering: what
    the user sees and whether the function's return flows into
    ``cmd_setup``'s non-zero exit.
    """
    with patch("terok_sandbox.check_selinux_status", return_value=result):
        ok = _check_selinux_policy(color=False)
    return ok, capsys.readouterr().out


class TestSelinuxPrereqPrint:
    """Verify the printed SELinux block in ``terok setup`` plus its return value."""

    def test_silent_in_tcp_mode(self, capsys: pytest.CaptureFixture) -> None:
        """TCP mode emits nothing and returns ok."""

        ok, out = _run_selinux_check(
            capsys, SelinuxCheckResult(SelinuxStatus.NOT_APPLICABLE_TCP_MODE)
        )
        assert out == ""
        assert ok is True

    def test_silent_when_not_enforcing(self, capsys: pytest.CaptureFixture) -> None:
        """Permissive host emits nothing and returns ok."""

        ok, out = _run_selinux_check(
            capsys, SelinuxCheckResult(SelinuxStatus.NOT_APPLICABLE_PERMISSIVE)
        )
        assert out == ""
        assert ok is True

    def test_warns_when_policy_missing(self, capsys: pytest.CaptureFixture) -> None:
        """Policy-missing prints two-option fix hint (install or opt out) and returns False."""

        ok, out = _run_selinux_check(capsys, SelinuxCheckResult(SelinuxStatus.POLICY_MISSING))
        assert "SELinux:" in out
        assert "policy NOT installed" in out
        # Both remedies surfaced — install the policy *or* opt out to tcp.
        assert "install_policy.sh" in out
        assert "services: {mode: tcp}" in out
        assert ok is False

    def test_warns_with_missing_tools_hint(self, capsys: pytest.CaptureFixture) -> None:
        """Missing tools → dnf prerequisite plus both remedies, return False."""

        ok, out = _run_selinux_check(
            capsys,
            SelinuxCheckResult(SelinuxStatus.POLICY_MISSING, missing_policy_tools=("checkmodule",)),
        )
        assert "Policy tools missing: checkmodule" in out
        assert "selinux-policy-devel" in out
        assert "install_policy.sh" in out
        assert "services: {mode: tcp}" in out
        assert ok is False

    def test_warns_when_libselinux_unloadable(self, capsys: pytest.CaptureFixture) -> None:
        """Libselinux-missing prints the silent-fail hint and returns False."""

        ok, out = _run_selinux_check(capsys, SelinuxCheckResult(SelinuxStatus.LIBSELINUX_MISSING))
        assert "libselinux.so.1 not loadable" in out
        assert "unconfined_t" in out
        assert ok is False

    def test_ok_exposes_installer_path(self, capsys: pytest.CaptureFixture) -> None:
        """Happy path surfaces the installer path and returns True."""

        ok, out = _run_selinux_check(capsys, SelinuxCheckResult(SelinuxStatus.OK))
        assert "policy installed" in out
        assert "install_policy.sh" in out
        assert ok is True


@patch("terok_sandbox.check_selinux_status")
@patch("terok.cli.commands.setup._ensure_gate", return_value=True)
@patch("terok.cli.commands.setup._ensure_vault", return_value=True)
@patch("terok.cli.commands.setup._ensure_shield", return_value=True)
@patch("terok.cli.commands.setup._check_host_binaries", return_value=True)
def test_cmd_setup_selinux_missing_exits_1(
    _bins: MagicMock,
    _shield: MagicMock,
    _vault: MagicMock,
    _gate: MagicMock,
    mock_status: MagicMock,
) -> None:
    """Services install OK but policy missing in socket mode → exit 1.

    Pins the contract that an unmet SELinux prerequisite is treated as
    a setup failure: a task container launched now would fail with AVC
    denials, so the setup run should too.
    """

    mock_status.return_value = SelinuxCheckResult(SelinuxStatus.POLICY_MISSING)
    with pytest.raises(SystemExit) as exc:
        cmd_setup(check_only=False)
    assert exc.value.code == 1


# ── Dispatch ────────────────────────────────────────────────────────────


class TestDispatch:
    """``setup.dispatch`` only handles the ``setup`` top-level verb."""

    def test_ignores_non_setup(self) -> None:
        """Non-setup namespaces fall through."""
        import argparse

        assert dispatch(argparse.Namespace(cmd="task")) is False

    def test_setup_invokes_cmd_setup(self) -> None:
        """``terok setup`` calls cmd_setup with the --check and --no-dbus-bridge flags."""
        import argparse

        args = argparse.Namespace(cmd="setup", check=True, no_dbus_bridge=False)
        with patch("terok.cli.commands.setup.cmd_setup") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with(check_only=True, no_dbus_bridge=False)

    def test_setup_defaults_check_to_false(self) -> None:
        """Missing --check/--no-dbus-bridge attributes default to False."""
        import argparse

        args = argparse.Namespace(cmd="setup")
        with patch("terok.cli.commands.setup.cmd_setup") as mock:
            assert dispatch(args) is True
        mock.assert_called_once_with(check_only=False, no_dbus_bridge=False)


# ── D-Bus clearance bridge phase ────────────────────────────────────────


class TestEnsureDbusBridge:
    """``_ensure_dbus_bridge`` composes the reader + hub stages when enabled."""

    @patch("terok.cli.commands.setup._check_dbus_send", return_value=True)
    @patch("terok.cli.commands.setup._ensure_bridge_reader", return_value=True)
    @patch("terok.cli.commands.setup._ensure_dbus_hub", return_value=True)
    def test_enabled_all_ok(self, _hub: MagicMock, _reader: MagicMock, _dbus: MagicMock) -> None:
        """Both sub-stages green → bridge ok."""
        assert _ensure_dbus_bridge(check_only=False, enabled=True, color=False) is True

    @patch("terok.cli.commands.setup._check_dbus_send", return_value=True)
    @patch("terok.cli.commands.setup._ensure_bridge_reader", return_value=False)
    @patch("terok.cli.commands.setup._ensure_dbus_hub", return_value=True)
    def test_enabled_reader_fail_propagates(
        self, _hub: MagicMock, _reader: MagicMock, _dbus: MagicMock
    ) -> None:
        """Reader failure must surface as a failed bridge stage."""
        assert _ensure_dbus_bridge(check_only=False, enabled=True, color=False) is False

    @patch("terok.cli.commands.setup._check_dbus_send", return_value=False)
    @patch("terok.cli.commands.setup._ensure_bridge_reader", return_value=True)
    @patch("terok.cli.commands.setup._ensure_dbus_hub", return_value=True)
    def test_enabled_missing_dbus_send_does_not_mask_success(
        self, _hub: MagicMock, _reader: MagicMock, _dbus: MagicMock
    ) -> None:
        """dbus-send absence warns only — it must not mask a clean install."""
        assert _ensure_dbus_bridge(check_only=False, enabled=True, color=False) is True

    @patch("terok.cli.commands.setup._disable_dbus_bridge", return_value=True)
    def test_disabled_routes_to_teardown(self, mock_disable: MagicMock) -> None:
        """``enabled=False`` delegates to ``_disable_dbus_bridge``."""
        assert _ensure_dbus_bridge(check_only=False, enabled=False, color=False) is True
        mock_disable.assert_called_once_with(check_only=False, color=False)


class TestEnsureBridgeReader:
    """``_ensure_bridge_reader`` installs the stdlib-only NFLOG reader script."""

    def test_check_only_present(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """check_only reports ok when the reader script exists on disk."""
        dest = tmp_path / ".local/share/terok-shield/nflog-reader.py"
        dest.parent.mkdir(parents=True)
        dest.write_text("#!/usr/bin/env python3\n")
        with patch("terok.cli.commands.setup.Path.home", return_value=tmp_path):
            assert _ensure_bridge_reader(check_only=True, color=False) is True
        out = capsys.readouterr().out
        assert "installed" in out

    def test_check_only_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """check_only reports false when the reader script is absent."""
        with patch("terok.cli.commands.setup.Path.home", return_value=tmp_path):
            assert _ensure_bridge_reader(check_only=True, color=False) is False
        out = capsys.readouterr().out
        assert "not installed" in out

    @patch("terok_sandbox.install_shield_bridge")
    def test_install_success(
        self, mock_install: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Successful install reports ok and calls the sandbox helper once."""
        with patch("terok.cli.commands.setup.Path.home", return_value=tmp_path):
            assert _ensure_bridge_reader(check_only=False, color=False) is True
        mock_install.assert_called_once()
        assert "installed" in capsys.readouterr().out

    @patch("terok_sandbox.install_shield_bridge", side_effect=RuntimeError("copy failed"))
    def test_install_failure(
        self, _install: MagicMock, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Install raising → caught, reported, returns False."""
        with patch("terok.cli.commands.setup.Path.home", return_value=tmp_path):
            assert _ensure_bridge_reader(check_only=False, color=False) is False
        assert "copy failed" in capsys.readouterr().out


class TestEnsureDbusHub:
    """``_ensure_dbus_hub`` installs the systemd user unit that owns org.terok.Shield1."""

    def test_check_only_present(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """check_only reports ok when the unit file exists."""
        unit_dir = tmp_path / "systemd/user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "terok-dbus.service").write_text("[Service]\n")
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}):
            assert _ensure_dbus_hub(check_only=True, color=False) is True
        assert "installed" in capsys.readouterr().out

    def test_check_only_missing(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """check_only reports false when the unit file is absent."""
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}):
            assert _ensure_dbus_hub(check_only=True, color=False) is False
        assert "not installed" in capsys.readouterr().out

    def test_install_success(self, capsys: pytest.CaptureFixture) -> None:
        """install_service + enable path reports ok."""
        with (
            patch("terok.cli.commands.setup.shutil.which", return_value="/bin/terok-dbus"),
            patch("terok_dbus._install.install_service") as mock_install,
            patch("terok.cli.commands.setup._enable_user_service") as mock_enable,
        ):
            assert _ensure_dbus_hub(check_only=False, color=False) is True
        mock_install.assert_called_once_with("/bin/terok-dbus")
        mock_enable.assert_called_once_with("terok-dbus")
        assert "installed" in capsys.readouterr().out

    def test_install_uses_module_fallback_when_bin_missing(self) -> None:
        """If ``terok-dbus`` isn't on PATH, fall back to ``python -m terok_dbus._cli``."""
        with (
            patch("terok.cli.commands.setup.shutil.which", return_value=None),
            patch("terok_dbus._install.install_service") as mock_install,
            patch("terok.cli.commands.setup._enable_user_service"),
        ):
            _ensure_dbus_hub(check_only=False, color=False)
        (bin_path,) = mock_install.call_args[0]
        assert "terok_dbus._cli" in bin_path

    def test_import_failure_soft_fails(self, capsys: pytest.CaptureFixture) -> None:
        """An ImportError out of terok_dbus._install must not crash setup."""
        with patch.dict(
            "sys.modules",
            {"terok_dbus._install": None},
        ):
            assert _ensure_dbus_hub(check_only=False, color=False) is False
        assert "import failed" in capsys.readouterr().out

    def test_install_raises_returns_false(self, capsys: pytest.CaptureFixture) -> None:
        """install_service exceptions are caught, reported, return False."""
        with (
            patch("terok.cli.commands.setup.shutil.which", return_value="/bin/terok-dbus"),
            patch(
                "terok_dbus._install.install_service",
                side_effect=RuntimeError("template missing"),
            ),
            patch("terok.cli.commands.setup._enable_user_service"),
        ):
            assert _ensure_dbus_hub(check_only=False, color=False) is False
        assert "template missing" in capsys.readouterr().out


class TestDisableDbusBridge:
    """``_disable_dbus_bridge`` idempotently removes the bridge + hub artifacts."""

    def test_check_only_reports_opt_out(self, capsys: pytest.CaptureFixture) -> None:
        """check_only prints an informational WARN and returns True."""
        assert _disable_dbus_bridge(check_only=True, color=False) is True
        out = capsys.readouterr().out
        assert "opted out" in out

    def test_clean_teardown_reports_ok(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """No unit on disk + sandbox uninstall succeeds → True."""
        with (
            patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}),
            patch("terok_sandbox.uninstall_shield_bridge") as mock_uninstall,
        ):
            assert _disable_dbus_bridge(check_only=False, color=False) is True
        mock_uninstall.assert_called_once()
        assert "disabled" in capsys.readouterr().out

    def test_teardown_with_unit_removes_and_disables(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Present unit → unlinked + systemctl disable invoked."""
        unit_dir = tmp_path / "systemd/user"
        unit_dir.mkdir(parents=True)
        unit = unit_dir / "terok-dbus.service"
        unit.write_text("[Service]\n")
        with (
            patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}),
            patch("terok_sandbox.uninstall_shield_bridge"),
            patch("terok.cli.commands.setup._disable_user_service") as mock_disable,
        ):
            assert _disable_dbus_bridge(check_only=False, color=False) is True
        assert not unit.exists()
        mock_disable.assert_called_once_with("terok-dbus")

    def test_uninstall_exception_returns_false(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Reader uninstall raising → WARN line + False."""
        with (
            patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}),
            patch(
                "terok_sandbox.uninstall_shield_bridge",
                side_effect=RuntimeError("permission denied"),
            ),
        ):
            assert _disable_dbus_bridge(check_only=False, color=False) is False
        out = capsys.readouterr().out
        assert "reader uninstall" in out
        assert "permission denied" in out

    def test_unit_removal_oserror_returns_false(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """Unit file present but unlink raises OSError → WARN line + False."""
        unit_dir = tmp_path / "systemd/user"
        unit_dir.mkdir(parents=True)
        unit = unit_dir / "terok-dbus.service"
        unit.write_text("[Service]\n")
        with (
            patch.dict("os.environ", {"XDG_CONFIG_HOME": str(tmp_path)}),
            patch("terok_sandbox.uninstall_shield_bridge"),
            patch("pathlib.Path.unlink", side_effect=OSError("read-only fs")),
        ):
            assert _disable_dbus_bridge(check_only=False, color=False) is False
        out = capsys.readouterr().out
        assert "unit removal" in out


class TestCheckDbusSend:
    """``_check_dbus_send`` warns the operator when the host tool is absent."""

    def test_present_returns_true_silently(self, capsys: pytest.CaptureFixture) -> None:
        """Binary on PATH → True, no output."""
        with patch("terok.cli.commands.setup.shutil.which", return_value="/usr/bin/dbus-send"):
            assert _check_dbus_send(color=False) is True
        assert capsys.readouterr().out == ""

    def test_missing_emits_warning_and_returns_false(self, capsys: pytest.CaptureFixture) -> None:
        """Binary absent → WARN line mentioning package names and returns False."""
        with patch("terok.cli.commands.setup.shutil.which", return_value=None):
            assert _check_dbus_send(color=False) is False
        out = capsys.readouterr().out
        assert "dbus-send" in out
        assert "dbus-tools" in out


class TestUserServiceHelpers:
    """``_enable_user_service`` / ``_disable_user_service`` wrap ``systemctl --user``."""

    def test_enable_invokes_three_systemctl_calls(self) -> None:
        """daemon-reload + enable + restart — each as a separate run()."""
        import subprocess as sp

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value="/bin/systemctl"),
            patch.object(sp, "run") as mock_run,
        ):
            _enable_user_service("terok-dbus")
        commands = [call.args[0] for call in mock_run.call_args_list]
        verbs = [cmd[2] for cmd in commands]
        assert verbs == ["daemon-reload", "enable", "restart"]
        for cmd in commands:
            assert cmd[0] == "/bin/systemctl"

    def test_enable_noop_when_systemctl_missing(self) -> None:
        """Missing systemctl → no subprocess calls issued."""
        import subprocess as sp

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value=None),
            patch.object(sp, "run") as mock_run,
        ):
            _enable_user_service("terok-dbus")
        mock_run.assert_not_called()

    def test_disable_invokes_single_systemctl_call(self) -> None:
        """``disable --now`` is one call; no daemon-reload needed on teardown."""
        import subprocess as sp

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value="/bin/systemctl"),
            patch.object(sp, "run") as mock_run,
        ):
            _disable_user_service("terok-dbus")
        mock_run.assert_called_once()
        argv = mock_run.call_args.args[0]
        assert argv[0] == "/bin/systemctl"
        assert "disable" in argv
        assert "--now" in argv

    def test_disable_noop_when_systemctl_missing(self) -> None:
        """Missing systemctl → no subprocess calls issued."""
        import subprocess as sp

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value=None),
            patch.object(sp, "run") as mock_run,
        ):
            _disable_user_service("terok-dbus")
        mock_run.assert_not_called()
