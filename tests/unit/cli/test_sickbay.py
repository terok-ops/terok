# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for sickbay health checks and hook reconciliation."""

from __future__ import annotations

import unittest.mock
from pathlib import Path

import pytest
from terok_sandbox import SelinuxCheckResult, SelinuxStatus

from terok.cli.commands.sickbay import (
    _check_gate_server,
    _check_selinux_policy,
    _check_ssh_signer,
    _check_task_hook,
    _check_vault,
    _check_vault_migration,
    _reconcile_post_stop,
    _update_worst,
)
from terok.lib.util.yaml import dump as yaml_dump

MOCK_BASE = Path("/tmp/terok-testing")


@pytest.fixture()
def task_meta_dir(tmp_path: Path) -> Path:
    """Create a temporary task metadata directory."""
    meta_dir = tmp_path / "tasks"
    meta_dir.mkdir()
    return meta_dir


def _write_meta(meta_dir: Path, tid: str, meta: dict) -> Path:
    """Write task metadata to a YAML file and return the path."""
    p = meta_dir / f"{tid}.yml"
    p.write_text(yaml_dump(meta))
    return p


class TestUpdateWorst:
    def test_ok_stays_ok(self) -> None:
        assert _update_worst("ok", "ok") == "ok"

    def test_warn_upgrades_ok(self) -> None:
        assert _update_worst("ok", "warn") == "warn"

    def test_error_upgrades_warn(self) -> None:
        assert _update_worst("warn", "error") == "error"

    def test_error_stays_error(self) -> None:
        assert _update_worst("error", "ok") == "error"

    def test_warn_stays_warn(self) -> None:
        assert _update_worst("warn", "ok") == "warn"


class TestCheckSshSigner:
    """Verify ``_check_ssh_signer`` diagnostics against the DB-backed vault."""

    @staticmethod
    def _mock_project(pid: str) -> unittest.mock.MagicMock:
        p = unittest.mock.MagicMock()
        p.id = pid
        return p

    def _patch_vault(self, assigned_scopes: list[str]):
        """Patch ``CredentialDB`` to report the given assigned scopes."""
        db = unittest.mock.MagicMock()
        db.list_scopes_with_ssh_keys.return_value = assigned_scopes
        return unittest.mock.patch("terok_sandbox.CredentialDB", return_value=db)

    def test_no_projects(self) -> None:
        """No projects configured → ok (nothing to check)."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch("terok.cli.commands.sickbay.list_projects", return_value=[]),
            self._patch_vault([]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "ok"
        assert "no projects" in detail

    def test_all_projects_have_keys(self) -> None:
        """Every project has an assignment → ok, N/N."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("proj")],
            ),
            self._patch_vault(["proj"]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "ok"
        assert "1/1" in detail

    def test_unregistered_project(self) -> None:
        """Project with no assignment → warn, naming the scope."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("myproj")],
            ),
            self._patch_vault([]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "warn"
        assert "myproj" in detail
        assert "0/1" in detail

    def test_custom_scopes_ignored(self) -> None:
        """Non-project scopes with keys don't cover the project's absence."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("proj")],
            ),
            self._patch_vault(["custom-scope"]),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "warn"
        assert "proj" in detail

    def test_vault_failure_degrades_to_warning(self) -> None:
        """A vault that refuses to open surfaces as a ``warn``, not a crash."""
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.list_projects",
                return_value=[self._mock_project("proj")],
            ),
            unittest.mock.patch(
                "terok_sandbox.CredentialDB", side_effect=RuntimeError("db locked")
            ),
        ):
            sev, _, detail = _check_ssh_signer()
        assert sev == "warn"
        assert "unreachable" in detail
        assert "db locked" in detail


class TestCheckTaskHook:
    def test_missing_meta_file_returns_none(self, tmp_path: Path) -> None:
        project = unittest.mock.MagicMock()
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=tmp_path
        ):
            assert _check_task_hook("proj", "99", project, fix=False) is None

    def test_no_mode_returns_none(self, task_meta_dir: Path) -> None:
        _write_meta(task_meta_dir, "1", {"status": "created"})
        project = unittest.mock.MagicMock()
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            assert _check_task_hook("proj", "1", project, fix=False) is None

    def test_running_container_returns_none(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        mock_runtime.container.return_value.state = "running"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            assert _check_task_hook("proj", "1", project, fix=False) is None

    def test_already_fired_returns_none(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli", "hooks_fired": ["post_stop"]})
        project = unittest.mock.MagicMock()
        mock_runtime.container.return_value.state = "exited"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            assert _check_task_hook("proj", "1", project, fix=False) is None

    def test_unfired_returns_warn(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli", "hooks_fired": ["post_start"]})
        project = unittest.mock.MagicMock()
        mock_runtime.container.return_value.state = "exited"
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            result = _check_task_hook("proj", "1", project, fix=False)
            assert result is not None
            assert result[0] == "warn"
            assert "post_stop" in result[2]

    def test_fix_calls_reconcile(self, task_meta_dir: Path, mock_runtime) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        project.hook_post_stop = "echo cleanup"
        project.tasks_root = task_meta_dir.parent
        mock_runtime.container.return_value.state = None
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
            ),
            unittest.mock.patch("terok.cli.commands.sickbay.run_hook") as mock_hook,
        ):
            result = _check_task_hook("proj", "1", project, fix=True)
            assert result is not None
            assert result[0] == "ok"
            assert "reconciled" in result[2]
            mock_hook.assert_called_once()

    def test_bad_metadata_returns_warn(self, task_meta_dir: Path) -> None:
        bad_path = task_meta_dir / "1.yml"
        bad_path.write_bytes(b"\x80\x81\x82")  # invalid UTF-8
        project = unittest.mock.MagicMock()
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
        ):
            result = _check_task_hook("proj", "1", project, fix=False)
            assert result is not None
            assert result[0] == "warn"
            assert "bad metadata" in result[2]


class TestReconcilePostStop:
    def test_success(self, tmp_path: Path) -> None:
        meta_path = tmp_path / "1.yml"
        meta_path.write_text(yaml_dump({"mode": "cli"}))
        project = unittest.mock.MagicMock()
        project.hook_post_stop = "echo done"
        project.tasks_root = tmp_path
        with unittest.mock.patch("terok.cli.commands.sickbay.run_hook"):
            result = _reconcile_post_stop("p", "1", "cli", "c", project, meta_path, "Task p/1")
            assert result[0] == "ok"

    def test_failure(self, tmp_path: Path) -> None:
        meta_path = tmp_path / "1.yml"
        meta_path.write_text(yaml_dump({"mode": "cli"}))
        project = unittest.mock.MagicMock()
        project.hook_post_stop = "exit 1"
        project.tasks_root = tmp_path
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.run_hook", side_effect=RuntimeError("boom")
        ):
            result = _reconcile_post_stop("p", "1", "cli", "c", project, meta_path, "Task p/1")
            assert result[0] == "error"
            assert "boom" in result[2]


class TestCheckVault:
    """Verify _check_vault three-state display."""

    def _make_status(self, **overrides: object) -> unittest.mock.MagicMock:
        """Build a mock VaultStatus with defaults."""
        defaults = {
            "mode": "none",
            "running": False,
            "healthy": False,
            "credentials_stored": (),
            "transport": None,
        }
        defaults.update(overrides)
        return unittest.mock.MagicMock(**defaults)

    def test_running_shows_ok(self) -> None:
        """Service active → ok with credential count and transport."""
        status = self._make_status(
            mode="systemd", running=True, credentials_stored=("claude",), transport="tcp"
        )
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_vault_status", return_value=status),
            unittest.mock.patch("terok.cli.commands.sickbay.get_services_mode", return_value="tcp"),
        ):
            sev, _, detail = _check_vault()
        assert sev == "ok"
        assert "1 credential(s)" in detail
        assert "tcp" in detail

    def test_transport_mismatch_warns(self) -> None:
        """Running on TCP when config says socket → warn."""
        status = self._make_status(
            mode="systemd", running=True, credentials_stored=("claude",), transport="tcp"
        )
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_vault_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_services_mode", return_value="socket"
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "services.mode: socket" in detail

    def test_systemd_socket_active_service_idle(self) -> None:
        """Socket active but service idle → ok with standby message."""
        status = self._make_status(mode="systemd", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_vault_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_vault_socket_active", return_value=True
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "ok"
        assert "starts on first connection" in detail

    def test_systemd_socket_inactive(self) -> None:
        """Socket installed but inactive → error."""
        status = self._make_status(mode="systemd", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_vault_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_vault_socket_active", return_value=False
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "error"
        assert "not active" in detail

    def test_not_installed_systemd_available(self) -> None:
        """No proxy, systemd available → warn with install hint."""
        status = self._make_status(mode="none", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_vault_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_vault_systemd_available", return_value=True
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "install" in detail

    def test_not_installed_no_systemd(self) -> None:
        """No proxy, no systemd → warn with start hint."""
        status = self._make_status(mode="none", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_vault_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_vault_systemd_available", return_value=False
            ),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "start" in detail

    def test_exception_returns_warn(self) -> None:
        """Exception during status check → warn."""
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.get_vault_status",
            side_effect=RuntimeError("oops"),
        ):
            sev, _, detail = _check_vault()
        assert sev == "warn"
        assert "oops" in detail


class TestCheckGateServerTransport:
    """Verify gate server transport mismatch detection."""

    def test_tcp_mode_tcp_transport_ok(self) -> None:
        """TCP mode with TCP transport → ok."""
        status = unittest.mock.MagicMock(mode="systemd", running=True, port=9418, transport="tcp")
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_server_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.check_units_outdated", return_value=None
            ),
            unittest.mock.patch("terok.cli.commands.sickbay.get_services_mode", return_value="tcp"),
        ):
            sev, _, detail = _check_gate_server()
        assert sev == "ok"
        assert "tcp" in detail

    def test_socket_mode_tcp_transport_warns(self) -> None:
        """Socket mode configured but gate running on TCP → warn."""
        status = unittest.mock.MagicMock(mode="systemd", running=True, port=9418, transport="tcp")
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config"),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_server_status", return_value=status
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.check_units_outdated", return_value=None
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_services_mode", return_value="socket"
            ),
        ):
            sev, _, detail = _check_gate_server()
        assert sev == "warn"
        assert "services.mode: socket" in detail


class TestCheckSelinuxPolicy:
    """Verify the five branches of the SELinux policy sickbay check.

    The decision tree itself lives in
    :func:`terok_sandbox.check_selinux_status` (exercised separately in
    terok-sandbox's ``test_selinux.py``).  Here we patch that helper
    with pre-built :class:`SelinuxCheckResult` values and verify the
    sickbay-side *rendering* — tuple severity, label, detail text.
    """

    @staticmethod
    def _run(result: SelinuxCheckResult) -> tuple[str, str, str]:
        """Execute ``_check_selinux_policy`` with ``check_selinux_status`` mocked."""
        with unittest.mock.patch("terok_sandbox.check_selinux_status", return_value=result):
            return _check_selinux_policy()

    def test_not_needed_in_tcp_mode(self) -> None:
        """``services.mode: tcp`` renders as ok."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.NOT_APPLICABLE_TCP_MODE))
        assert sev == "ok"
        assert "services.mode: tcp" in detail

    def test_not_needed_when_selinux_permissive(self) -> None:
        """Socket mode on a permissive host renders as ok."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.NOT_APPLICABLE_PERMISSIVE))
        assert sev == "ok"
        assert "not enforcing" in detail

    def test_warn_when_policy_missing(self) -> None:
        """Policy-missing renders both remedies (install-or-opt-out)."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.POLICY_MISSING))
        assert sev == "warn"
        assert "terok_socket_t NOT installed" in detail
        assert "sudo bash" in detail
        assert "install_policy.sh" in detail
        # Opt-out must be surfaced — the user may not have root.
        assert "services: {mode: tcp}" in detail

    def test_warn_also_names_missing_tools(self) -> None:
        """Policy-missing + missing tools renders the dnf prerequisite plus both remedies."""

        sev, _, detail = self._run(
            SelinuxCheckResult(
                SelinuxStatus.POLICY_MISSING,
                missing_policy_tools=("checkmodule", "semodule_package"),
            )
        )
        assert sev == "warn"
        assert "policy tools missing" in detail
        assert "checkmodule" in detail
        assert "selinux-policy-devel" in detail
        assert "services: {mode: tcp}" in detail

    def test_warn_when_libselinux_unloadable(self) -> None:
        """Libselinux-missing renders as warn naming the silent-fail vector."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.LIBSELINUX_MISSING))
        assert sev == "warn"
        assert "libselinux.so.1" in detail
        assert "unconfined_t" in detail

    def test_ok_when_everything_ready(self) -> None:
        """OK renders with the installer path for future reinstall/debug."""

        sev, _, detail = self._run(SelinuxCheckResult(SelinuxStatus.OK))
        assert sev == "ok"
        assert "terok_socket_t installed" in detail
        assert "install_policy.sh" in detail


class TestCheckVaultMigration:
    """``_check_vault_migration`` detects a lingering pre-vault ``credentials/`` dir."""

    def test_ok_when_no_legacy_dir(self, tmp_path: Path) -> None:
        """No legacy ``credentials/`` dir → ok."""

        def fake_ns(name: str) -> Path:
            return tmp_path / name  # neither exists

        with unittest.mock.patch("terok_sandbox.paths.namespace_state_dir", side_effect=fake_ns):
            sev, label, detail = _check_vault_migration()
        assert sev == "ok"
        assert label == "Vault migration"
        assert "no legacy" in detail

    def test_warn_when_only_legacy_exists(self, tmp_path: Path) -> None:
        """Legacy dir without new vault dir → warn pointing at the migration script."""
        legacy = tmp_path / "credentials"
        legacy.mkdir()  # exists
        vault = tmp_path / "vault"  # does not exist

        def fake_ns(name: str) -> Path:
            return legacy if name == "credentials" else vault

        with unittest.mock.patch("terok_sandbox.paths.namespace_state_dir", side_effect=fake_ns):
            sev, _, detail = _check_vault_migration()
        assert sev == "warn"
        assert str(legacy) in detail
        assert "terok-migrate-vault.py" in detail

    def test_info_when_both_exist(self, tmp_path: Path) -> None:
        """Both dirs present → info (migration ran but old dir survived for safety)."""
        legacy = tmp_path / "credentials"
        legacy.mkdir()
        vault = tmp_path / "vault"
        vault.mkdir()

        def fake_ns(name: str) -> Path:
            return legacy if name == "credentials" else vault

        with unittest.mock.patch("terok_sandbox.paths.namespace_state_dir", side_effect=fake_ns):
            sev, _, detail = _check_vault_migration()
        assert sev == "info"
        assert "still present" in detail
        assert "safe to remove" in detail

    def test_warn_when_probe_raises(self) -> None:
        """An exception inside the probe is surfaced as a warn result."""
        with unittest.mock.patch(
            "terok_sandbox.paths.namespace_state_dir",
            side_effect=RuntimeError("boom"),
        ):
            sev, label, detail = _check_vault_migration()
        assert sev == "warn"
        assert label == "Vault migration"
        assert "boom" in detail


class TestCheckDbusHubStateDir:
    """``_check_dbus_hub_state_dir`` surfaces env↔unit mismatch for the hub."""

    def test_ok_when_unit_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terok.cli.commands.sickbay import _check_dbus_hub_state_dir

        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        with unittest.mock.patch("terok_dbus._install.read_installed_unit", return_value=None):
            sev, _, detail = _check_dbus_hub_state_dir()
        assert sev == "ok"
        assert "not installed" in detail

    def test_ok_when_both_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terok.cli.commands.sickbay import _check_dbus_hub_state_dir

        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        unit_text = "[Service]\nExecStart=/a/terok-dbus serve\n"
        with unittest.mock.patch("terok_dbus._install.read_installed_unit", return_value=unit_text):
            sev, _, detail = _check_dbus_hub_state_dir()
        assert sev == "ok"
        assert "XDG default" in detail

    def test_ok_when_both_agree(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terok.cli.commands.sickbay import _check_dbus_hub_state_dir

        monkeypatch.setenv("TEROK_SHIELD_STATE_DIR", "/foo")
        unit_text = (
            "[Service]\nExecStart=/a/terok-dbus serve\nEnvironment=TEROK_SHIELD_STATE_DIR=/foo\n"
        )
        with unittest.mock.patch("terok_dbus._install.read_installed_unit", return_value=unit_text):
            sev, _, detail = _check_dbus_hub_state_dir()
        assert sev == "ok"
        assert "/foo" in detail

    def test_warn_when_env_set_but_unit_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terok.cli.commands.sickbay import _check_dbus_hub_state_dir

        monkeypatch.setenv("TEROK_SHIELD_STATE_DIR", "/foo")
        unit_text = "[Service]\nExecStart=/a/terok-dbus serve\n"
        with unittest.mock.patch("terok_dbus._install.read_installed_unit", return_value=unit_text):
            sev, _, detail = _check_dbus_hub_state_dir()
        assert sev == "warn"
        assert "absent from unit" in detail
        assert "terok setup" in detail

    def test_warn_when_unit_set_but_env_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terok.cli.commands.sickbay import _check_dbus_hub_state_dir

        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        unit_text = (
            "[Service]\nExecStart=/a/terok-dbus serve\nEnvironment=TEROK_SHIELD_STATE_DIR=/foo\n"
        )
        with unittest.mock.patch("terok_dbus._install.read_installed_unit", return_value=unit_text):
            sev, _, detail = _check_dbus_hub_state_dir()
        assert sev == "warn"
        assert "/foo" in detail
        assert "shell env unset" in detail

    def test_warn_when_values_differ(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from terok.cli.commands.sickbay import _check_dbus_hub_state_dir

        monkeypatch.setenv("TEROK_SHIELD_STATE_DIR", "/shell")
        unit_text = (
            "[Service]\nExecStart=/a/terok-dbus serve\nEnvironment=TEROK_SHIELD_STATE_DIR=/unit\n"
        )
        with unittest.mock.patch("terok_dbus._install.read_installed_unit", return_value=unit_text):
            sev, _, detail = _check_dbus_hub_state_dir()
        assert sev == "warn"
        assert "/shell" in detail
        assert "/unit" in detail
