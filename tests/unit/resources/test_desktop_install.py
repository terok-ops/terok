# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for :mod:`terok.resources.desktop` — XDG launcher + icon install."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from terok.resources import desktop


@pytest.fixture
def xdg_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``$XDG_DATA_HOME`` to a pytest tmp dir for every test."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    return tmp_path


class TestInstallDesktopEntry:
    """``install_desktop_entry`` writes the launcher + icon + refreshes caches."""

    def test_writes_desktop_file_with_templated_bin(self, xdg_data_home: Path) -> None:
        """``{{BIN}}`` / ``{{TRY_EXEC}}`` land as the resolved binary path."""
        with mock.patch("terok.resources.desktop.shutil.which", return_value=None):
            desktop.install_desktop_entry("/usr/local/bin/terok-tui")
        content = (xdg_data_home / "applications" / "terok.desktop").read_text()
        assert "Exec=/usr/local/bin/terok-tui" in content
        assert "TryExec=/usr/local/bin/terok-tui" in content
        # Sanity: the rest of the template carried through verbatim.
        assert "Icon=terok" in content
        assert "Terminal=true" in content

    def test_writes_icon_into_hicolor_tree(self, xdg_data_home: Path) -> None:
        """The bundled PNG ends up under hicolor/256x256/apps/terok.png."""
        with mock.patch("terok.resources.desktop.shutil.which", return_value=None):
            desktop.install_desktop_entry("terok-tui")
        icon = xdg_data_home / "icons" / "hicolor" / "256x256" / "apps" / "terok.png"
        assert icon.is_file()
        # PNG magic header — cheap check that it's the real file, not an empty write.
        assert icon.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    def test_runs_cache_refresh_binaries_when_present(self, xdg_data_home: Path) -> None:
        """``update-desktop-database`` + ``gtk-update-icon-cache`` are invoked when on PATH."""
        fake_proc = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=b"")
        calls: list[list[str]] = []

        def record(argv: list[str], *_a, **_kw):
            calls.append(argv)
            return fake_proc

        with (
            mock.patch(
                "terok.resources.desktop.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            mock.patch("terok.resources.desktop.subprocess.run", side_effect=record),
        ):
            desktop.install_desktop_entry("terok-tui")
        binaries = [argv[0].split("/")[-1] for argv in calls]
        assert "update-desktop-database" in binaries
        assert "gtk-update-icon-cache" in binaries

    def test_cache_refresh_skipped_when_binaries_missing(self, xdg_data_home: Path) -> None:
        """No ``update-desktop-database`` / ``gtk-update-icon-cache`` on PATH → silent skip."""
        with (
            mock.patch("terok.resources.desktop.shutil.which", return_value=None),
            mock.patch("terok.resources.desktop.subprocess.run") as run,
        ):
            desktop.install_desktop_entry("terok-tui")
        run.assert_not_called()

    def test_cache_refresh_swallows_subprocess_failure(self, xdg_data_home: Path) -> None:
        """A hung / broken cache refresh binary can't derail the install."""
        with (
            mock.patch(
                "terok.resources.desktop.shutil.which",
                side_effect=lambda name: f"/usr/bin/{name}",
            ),
            mock.patch(
                "terok.resources.desktop.subprocess.run", side_effect=OSError("exec format error")
            ),
        ):
            desktop.install_desktop_entry("terok-tui")  # must not raise


class TestUninstallDesktopEntry:
    """``uninstall_desktop_entry`` removes both files + refreshes caches."""

    def test_unlinks_desktop_file_and_icon(self, xdg_data_home: Path) -> None:
        with mock.patch("terok.resources.desktop.shutil.which", return_value=None):
            desktop.install_desktop_entry("terok-tui")
            assert desktop.is_desktop_entry_installed()
            desktop.uninstall_desktop_entry()
        assert not desktop.is_desktop_entry_installed()

    def test_uninstall_when_not_installed_is_noop(self, xdg_data_home: Path) -> None:
        """Running the teardown on a clean host doesn't raise."""
        with mock.patch("terok.resources.desktop.shutil.which", return_value=None):
            desktop.uninstall_desktop_entry()
        assert not desktop.is_desktop_entry_installed()


class TestIsDesktopEntryInstalled:
    """Presence check honours both files existing."""

    def test_returns_false_when_neither_present(self, xdg_data_home: Path) -> None:
        assert desktop.is_desktop_entry_installed() is False

    def test_returns_false_when_only_desktop_file(self, xdg_data_home: Path) -> None:
        (xdg_data_home / "applications").mkdir(parents=True)
        (xdg_data_home / "applications" / "terok.desktop").write_text("")
        assert desktop.is_desktop_entry_installed() is False

    def test_returns_true_when_both_present(self, xdg_data_home: Path) -> None:
        with mock.patch("terok.resources.desktop.shutil.which", return_value=None):
            desktop.install_desktop_entry("terok-tui")
        assert desktop.is_desktop_entry_installed() is True
