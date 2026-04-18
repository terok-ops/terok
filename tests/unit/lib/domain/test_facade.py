# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the domain.facade thin-wrapper factories."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestGetProject:
    """get_project loads config and wraps it in a Project aggregate."""

    def test_returns_project_wrapping_loaded_config(self) -> None:
        from terok.lib.domain import facade
        from terok.lib.domain.project import Project

        fake_cfg = MagicMock()
        fake_cfg.id = "myproj"
        with patch("terok.lib.domain.facade.load_project", return_value=fake_cfg) as loader:
            result = facade.get_project("myproj")
        loader.assert_called_once_with("myproj")
        assert isinstance(result, Project)


class TestListProjects:
    """list_projects lifts every core config into a Project aggregate."""

    def test_wraps_each_core_config(self) -> None:
        from terok.lib.domain import facade
        from terok.lib.domain.project import Project

        a, b = MagicMock(id="a"), MagicMock(id="b")
        with patch("terok.lib.core.projects.list_projects", return_value=[a, b]) as lister:
            result = facade.list_projects()
        lister.assert_called_once()
        assert len(result) == 2
        assert all(isinstance(p, Project) for p in result)

    def test_empty_list_returns_empty(self) -> None:
        from terok.lib.domain import facade

        with patch("terok.lib.core.projects.list_projects", return_value=[]):
            assert facade.list_projects() == []


class TestDeriveProject:
    """derive_project composes the three domain steps and returns a Project."""

    def test_delegates_and_wraps_result(self) -> None:
        from terok.lib.domain import facade
        from terok.lib.domain.project import Project

        derived_cfg = MagicMock(id="derived")
        with (
            patch("terok.lib.domain.facade._derive_project") as derive,
            patch("terok.lib.domain.facade._share_ssh_key_registration") as share,
            patch("terok.lib.domain.facade.load_project", return_value=derived_cfg) as loader,
        ):
            result = facade.derive_project("source", "derived")
        derive.assert_called_once_with("source", "derived")
        share.assert_called_once_with("source", "derived")
        loader.assert_called_once_with("derived")
        assert isinstance(result, Project)


class TestShareSshKeyRegistration:
    """Copy every usable SSH key entry from the source scope to the new scope."""

    def _write_keys(self, tmp_path: Path, payload: object) -> Path:
        p = tmp_path / "ssh-keys.json"
        p.write_text(json.dumps(payload))
        return p

    def test_silent_noop_when_missing_file(self, tmp_path: Path) -> None:
        from terok.lib.domain import facade

        with (
            patch("terok.lib.core.config.make_sandbox_config") as mock_cfg,
            patch("terok.lib.domain.facade.register_ssh_key") as register,
        ):
            mock_cfg.return_value.ssh_keys_json_path = tmp_path / "missing.json"
            facade._share_ssh_key_registration("src", "new")
        register.assert_not_called()

    def test_silent_noop_when_corrupt_json(self, tmp_path: Path) -> None:
        from terok.lib.domain import facade

        path = tmp_path / "ssh-keys.json"
        path.write_text("{not json")
        with (
            patch("terok.lib.core.config.make_sandbox_config") as mock_cfg,
            patch("terok.lib.domain.facade.register_ssh_key") as register,
        ):
            mock_cfg.return_value.ssh_keys_json_path = path
            facade._share_ssh_key_registration("src", "new")
        register.assert_not_called()

    def test_registers_every_full_key_entry(self, tmp_path: Path) -> None:
        """Source with multiple usable entries → register every one."""
        from terok.lib.domain import facade

        path = self._write_keys(
            tmp_path,
            {
                "src": [
                    {"private_key": "/k1", "public_key": "/k1.pub"},
                    {"private_key": "/k2", "public_key": "/k2.pub"},
                ]
            },
        )
        with (
            patch("terok.lib.core.config.make_sandbox_config") as mock_cfg,
            patch("terok.lib.domain.facade.register_ssh_key") as register,
        ):
            mock_cfg.return_value.ssh_keys_json_path = path
            facade._share_ssh_key_registration("src", "new")
        assert register.call_count == 2
        registered_privs = [c.args[1]["private_key"] for c in register.call_args_list]
        assert registered_privs == ["/k1", "/k2"]

    def test_skips_entries_missing_fields(self, tmp_path: Path) -> None:
        """Entries lacking ``private_key`` or ``public_key`` are skipped silently."""
        from terok.lib.domain import facade

        path = self._write_keys(
            tmp_path,
            {
                "src": [
                    {"private_key": "/ok", "public_key": "/ok.pub"},
                    {"private_key": "/only-priv"},  # missing public_key
                    "not a dict",
                ]
            },
        )
        with (
            patch("terok.lib.core.config.make_sandbox_config") as mock_cfg,
            patch("terok.lib.domain.facade.register_ssh_key") as register,
        ):
            mock_cfg.return_value.ssh_keys_json_path = path
            facade._share_ssh_key_registration("src", "new")
        assert register.call_count == 1
        assert register.call_args.args[1]["private_key"] == "/ok"

    def test_accepts_legacy_single_dict_entry(self, tmp_path: Path) -> None:
        """Pre-0.8 ssh-keys.json stored a single dict per scope, not a list."""
        from terok.lib.domain import facade

        path = self._write_keys(
            tmp_path, {"src": {"private_key": "/legacy", "public_key": "/legacy.pub"}}
        )
        with (
            patch("terok.lib.core.config.make_sandbox_config") as mock_cfg,
            patch("terok.lib.domain.facade.register_ssh_key") as register,
        ):
            mock_cfg.return_value.ssh_keys_json_path = path
            facade._share_ssh_key_registration("src", "new")
        assert register.call_count == 1
        assert register.call_args.args[1]["private_key"] == "/legacy"


class TestRegisterSshKey:
    """register_ssh_key delegates to the sandbox helper with the right paths."""

    def test_forwards_paths_and_result(self, tmp_path: Path) -> None:
        from terok.lib.domain import facade

        init_result = {"private_key": "/p", "public_key": "/p.pub"}
        with (
            patch("terok.lib.core.config.make_sandbox_config") as mock_cfg,
            patch("terok_sandbox.update_ssh_keys_json") as upd,
        ):
            mock_cfg.return_value.ssh_keys_json_path = tmp_path / "ssh-keys.json"
            facade.register_ssh_key("myproj", init_result)
        upd.assert_called_once_with(tmp_path / "ssh-keys.json", "myproj", init_result)


class TestMaybePauseForSshKeyRegistration:
    """maybe_pause_for_ssh_key_registration only pauses for SSH upstreams."""

    def test_pauses_for_git_at_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url="git@example.com:org/repo.git")
        with (
            patch("terok.lib.domain.facade.load_project", return_value=project),
            patch("builtins.input", return_value=""),
        ):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" in capsys.readouterr().out

    def test_pauses_for_ssh_scheme_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url="ssh://git@example.com/org/repo.git")
        with (
            patch("terok.lib.domain.facade.load_project", return_value=project),
            patch("builtins.input", return_value=""),
        ):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" in capsys.readouterr().out

    def test_noop_for_https_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url="https://github.com/org/repo.git")
        with patch("terok.lib.domain.facade.load_project", return_value=project):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" not in capsys.readouterr().out

    def test_noop_for_empty_upstream(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.lib.domain import facade

        project = MagicMock(upstream_url=None)
        with patch("terok.lib.domain.facade.load_project", return_value=project):
            facade.maybe_pause_for_ssh_key_registration("myproj")
        assert "ACTION REQUIRED" not in capsys.readouterr().out
