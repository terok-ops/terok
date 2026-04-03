# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for sickbay health checks and hook reconciliation."""

from __future__ import annotations

import unittest.mock
from pathlib import Path

import pytest

from terok.cli.commands.sickbay import (
    _check_credential_proxy,
    _check_ssh_agent,
    _check_task_hook,
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


class TestCheckSshAgent:
    """Verify _check_ssh_agent diagnostics."""

    def test_missing_keys_file(self, tmp_path: Path) -> None:
        """No ssh-keys.json → warn with ssh-init hint."""
        with unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config") as mock_cfg:
            mock_cfg.return_value.ssh_keys_json_path = tmp_path / "no-such.json"
            sev, _, detail = _check_ssh_agent()
        assert sev == "warn"
        assert "ssh-init" in detail

    def test_empty_keys_file(self, tmp_path: Path) -> None:
        """Empty mapping → warn with ssh-init hint."""
        kf = tmp_path / "ssh-keys.json"
        kf.write_text("{}")
        with unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config") as mock_cfg:
            mock_cfg.return_value.ssh_keys_json_path = kf
            sev, _, detail = _check_ssh_agent()
        assert sev == "warn"
        assert "no projects" in detail

    def test_all_keys_present(self, tmp_path: Path) -> None:
        """All registered keys exist → ok."""
        import json

        priv = tmp_path / "id"
        pub = tmp_path / "id.pub"
        priv.write_text("key")
        pub.write_text("pubkey")
        kf = tmp_path / "ssh-keys.json"
        kf.write_text(json.dumps({"proj": {"private_key": str(priv), "public_key": str(pub)}}))
        with unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config") as mock_cfg:
            mock_cfg.return_value.ssh_keys_json_path = kf
            sev, _, detail = _check_ssh_agent()
        assert sev == "ok"
        assert "1 project(s)" in detail

    def test_missing_key_files(self, tmp_path: Path) -> None:
        """Registered keys with missing files → error."""
        import json

        kf = tmp_path / "ssh-keys.json"
        kf.write_text(
            json.dumps({"bad": {"private_key": "/gone/id", "public_key": "/gone/id.pub"}})
        )
        with unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config") as mock_cfg:
            mock_cfg.return_value.ssh_keys_json_path = kf
            sev, _, detail = _check_ssh_agent()
        assert sev == "error"
        assert "bad" in detail
        assert "ssh-init" in detail

    def test_corrupt_json(self, tmp_path: Path) -> None:
        """Corrupt JSON → error."""
        kf = tmp_path / "ssh-keys.json"
        kf.write_text("{bad")
        with unittest.mock.patch("terok.cli.commands.sickbay.make_sandbox_config") as mock_cfg:
            mock_cfg.return_value.ssh_keys_json_path = kf
            sev, _, _ = _check_ssh_agent()
        assert sev == "error"


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

    def test_running_container_returns_none(self, task_meta_dir: Path) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_container_state", return_value="running"
            ),
        ):
            assert _check_task_hook("proj", "1", project, fix=False) is None

    def test_already_fired_returns_none(self, task_meta_dir: Path) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli", "hooks_fired": ["post_stop"]})
        project = unittest.mock.MagicMock()
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_container_state", return_value="exited"
            ),
        ):
            assert _check_task_hook("proj", "1", project, fix=False) is None

    def test_unfired_returns_warn(self, task_meta_dir: Path) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli", "hooks_fired": ["post_start"]})
        project = unittest.mock.MagicMock()
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_container_state", return_value="exited"
            ),
        ):
            result = _check_task_hook("proj", "1", project, fix=False)
            assert result is not None
            assert result[0] == "warn"
            assert "post_stop" in result[2]

    def test_fix_calls_reconcile(self, task_meta_dir: Path) -> None:
        _write_meta(task_meta_dir, "1", {"mode": "cli"})
        project = unittest.mock.MagicMock()
        project.hook_post_stop = "echo cleanup"
        project.tasks_root = task_meta_dir.parent
        with (
            unittest.mock.patch(
                "terok.cli.commands.sickbay.tasks_meta_dir", return_value=task_meta_dir
            ),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.get_container_state", return_value=None
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


class TestCheckCredentialProxy:
    """Verify _check_credential_proxy three-state display."""

    def _make_status(self, **overrides: object) -> unittest.mock.MagicMock:
        """Build a mock CredentialProxyStatus with defaults."""
        defaults = {
            "mode": "none",
            "running": False,
            "healthy": False,
            "credentials_stored": (),
        }
        defaults.update(overrides)
        return unittest.mock.MagicMock(**defaults)

    def test_running_shows_ok(self) -> None:
        """Service active → ok with credential count."""
        status = self._make_status(mode="systemd", running=True, credentials_stored=("claude",))
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.get_proxy_status", return_value=status
        ):
            sev, _, detail = _check_credential_proxy()
        assert sev == "ok"
        assert "1 credential(s)" in detail

    def test_systemd_socket_active_service_idle(self) -> None:
        """Socket active but service idle → ok with standby message."""
        status = self._make_status(mode="systemd", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_proxy_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_proxy_socket_active", return_value=True
            ),
        ):
            sev, _, detail = _check_credential_proxy()
        assert sev == "ok"
        assert "starts on first connection" in detail

    def test_systemd_socket_inactive(self) -> None:
        """Socket installed but inactive → error."""
        status = self._make_status(mode="systemd", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_proxy_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_proxy_socket_active", return_value=False
            ),
        ):
            sev, _, detail = _check_credential_proxy()
        assert sev == "error"
        assert "not active" in detail

    def test_not_installed_systemd_available(self) -> None:
        """No proxy, systemd available → warn with install hint."""
        status = self._make_status(mode="none", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_proxy_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_proxy_systemd_available", return_value=True
            ),
        ):
            sev, _, detail = _check_credential_proxy()
        assert sev == "warn"
        assert "install" in detail

    def test_not_installed_no_systemd(self) -> None:
        """No proxy, no systemd → warn with start hint."""
        status = self._make_status(mode="none", running=False)
        with (
            unittest.mock.patch("terok.cli.commands.sickbay.get_proxy_status", return_value=status),
            unittest.mock.patch(
                "terok.cli.commands.sickbay.is_proxy_systemd_available", return_value=False
            ),
        ):
            sev, _, detail = _check_credential_proxy()
        assert sev == "warn"
        assert "start" in detail

    def test_exception_returns_warn(self) -> None:
        """Exception during status check → warn."""
        with unittest.mock.patch(
            "terok.cli.commands.sickbay.get_proxy_status",
            side_effect=RuntimeError("oops"),
        ):
            sev, _, detail = _check_credential_proxy()
        assert sev == "warn"
        assert "oops" in detail
