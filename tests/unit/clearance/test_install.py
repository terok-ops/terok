# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok.clearance._install`` — notifier systemd unit installer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok.clearance import _install
from terok.clearance._install import (
    UNIT_NAME,
    check_units_outdated,
    default_unit_path,
    install_service,
    read_installed_unit_version,
)


class TestInstallService:
    """``install_service`` renders the unit template into the user systemd dir."""

    def test_writes_unit_with_bin_path_substituted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            dest = install_service(Path("/usr/local/bin/terok-clearance-notifier"))
        assert dest == tmp_path / "systemd" / "user" / UNIT_NAME
        body = dest.read_text()
        assert "{{BIN}}" not in body
        assert "{{UNIT_VERSION}}" not in body
        assert "/usr/local/bin/terok-clearance-notifier" in body

    def test_argv_list_quotes_each_token_individually(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            dest = install_service(
                [Path("/home/me/My Py/python"), "-m", "terok.clearance.notifier.app"]
            )
        body = dest.read_text()
        assert '"/home/me/My Py/python" -m terok.clearance.notifier.app' in body

    def test_refuses_control_characters_in_bin_path(self) -> None:
        with pytest.raises(ValueError):
            _install._render_exec_start(Path("/a/terok-clearance-notifier\nRestart=never"))

    def test_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            first = install_service(Path("/a/notifier")).read_text()
            second = install_service(Path("/a/notifier")).read_text()
        assert first == second

    def test_runs_daemon_reload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload") as reload:
            install_service(Path("/a/notifier"))
        reload.assert_called_once()

    def test_daemon_reload_handles_missing_systemctl(self) -> None:
        with patch.object(_install.shutil, "which", return_value=None):
            _install._daemon_reload()


class TestUnitVersion:
    """Version marker lets sickbay tell fresh installs from stale ones."""

    def test_rendered_unit_carries_current_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            install_service(Path("/a/notifier"))
        assert read_installed_unit_version() == _install._UNIT_VERSION

    def test_read_version_returns_none_without_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = default_unit_path()
        path.parent.mkdir(parents=True)
        path.write_text("[Unit]\nDescription=hand-written\n[Service]\nExecStart=/x\n")
        assert read_installed_unit_version() is None

    def test_check_outdated_silent_on_fresh_install(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            install_service(Path("/a/notifier"))
        assert check_units_outdated() is None

    def test_check_outdated_silent_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert check_units_outdated() is None

    def test_check_outdated_flags_unversioned_unit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = default_unit_path()
        path.parent.mkdir(parents=True)
        path.write_text("[Unit]\n[Service]\nExecStart=/x\n")
        msg = check_units_outdated()
        assert msg is not None
        assert "unversioned" in msg
        assert "terok setup" in msg

    def test_check_outdated_flags_older_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        path = default_unit_path()
        path.parent.mkdir(parents=True)
        path.write_text(
            f"# terok-clearance-notifier-version: {_install._UNIT_VERSION - 1}\n[Service]\n"
        )
        msg = check_units_outdated()
        assert msg is not None
        assert f"v{_install._UNIT_VERSION - 1}" in msg
        assert f"v{_install._UNIT_VERSION}" in msg
