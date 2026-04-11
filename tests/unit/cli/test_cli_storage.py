# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok storage`` CLI command.

Verifies argument parsing, dispatch routing, and output for both
overview and detail modes — all with mocked domain calls.
"""

from __future__ import annotations

import argparse
import json
from unittest.mock import patch

from terok.cli.commands.storage import dispatch, register
from terok.lib.domain.image_cleanup import ImageInfo
from terok.lib.domain.storage import ProjectDetail, ProjectSummary, StorageOverview

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    """Build a parser with the storage command registered."""
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd")
    register(sub)
    return p


def _make_overview(**kwargs) -> StorageOverview:
    """Build a minimal StorageOverview for testing."""
    defaults = {
        "global_images": [ImageInfo("terok-l0", "bkwm", "id1", "1GB", "2d ago")],
        "shared_mounts": [],
        "projects": [ProjectSummary("proj1", 500_000_000, 200_000_000, 2)],
    }
    defaults.update(kwargs)
    return StorageOverview(**defaults)


def _make_detail(**kwargs) -> ProjectDetail:
    """Build a minimal ProjectDetail for testing."""
    defaults = {
        "project_id": "myproject",
        "images": [ImageInfo("myproject", "l2-cli", "id2", "3GB", "1d ago")],
        "tasks": [],
        "overlays": {},
    }
    defaults.update(kwargs)
    return ProjectDetail(**defaults)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


class TestRegister:
    """The ``storage`` command registers cleanly."""

    def test_default_mode(self):
        args = _parser().parse_args(["storage"])
        assert args.cmd == "storage"
        assert args.project is None
        assert args.json_output is False

    def test_project_flag(self):
        args = _parser().parse_args(["storage", "--project", "myproj"])
        assert args.project == "myproj"

    def test_json_flag(self):
        args = _parser().parse_args(["storage", "--json"])
        assert args.json_output is True


# ---------------------------------------------------------------------------
# Dispatch routing
# ---------------------------------------------------------------------------


class TestDispatch:
    """Dispatch returns True for ``storage`` and False for anything else."""

    def test_handles_storage(self):
        args = argparse.Namespace(cmd="storage", project=None, json_output=False)
        with patch("terok.cli.commands.storage._cmd_overview") as mock:
            assert dispatch(args) is True
            mock.assert_called_once()

    def test_ignores_other_commands(self):
        args = argparse.Namespace(cmd="image")
        assert dispatch(args) is False

    def test_routes_to_detail_with_project(self):
        args = argparse.Namespace(cmd="storage", project="myproj", json_output=False)
        with patch("terok.cli.commands.storage._cmd_detail") as mock:
            assert dispatch(args) is True
            mock.assert_called_once_with("myproj", json_output=False)


# ---------------------------------------------------------------------------
# Overview output
# ---------------------------------------------------------------------------


class TestOverviewOutput:
    """Overview mode prints a human-readable summary."""

    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir")
    @patch("terok.lib.domain.storage.list_projects", return_value=[])
    @patch("terok.lib.domain.storage.get_shared_mounts_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_images")
    @patch("terok.cli.commands.storage.supports_color", return_value=False)
    def test_prints_without_error(self, _color, mock_imgs, _shared, _projs, _mdir, capsys):
        mock_imgs.return_value = [ImageInfo("terok-l0", "bkwm", "id1", "1GB", "2d ago")]
        from terok.cli.commands.storage import _cmd_overview

        _cmd_overview()
        output = capsys.readouterr().out
        assert "Global" in output
        assert "terok-l0" in output
        assert "Grand total" in output

    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir")
    @patch("terok.lib.domain.storage.list_projects", return_value=[])
    @patch("terok.lib.domain.storage.get_shared_mounts_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_images", return_value=[])
    @patch("terok.cli.commands.storage.supports_color", return_value=False)
    def test_empty_system(self, _color, _imgs, _shared, _projs, _mdir, capsys):
        from terok.cli.commands.storage import _cmd_overview

        _cmd_overview()
        output = capsys.readouterr().out
        assert "Grand total" in output

    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir")
    @patch("terok.lib.domain.storage.list_projects", return_value=[])
    @patch("terok.lib.domain.storage.get_shared_mounts_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_images")
    def test_json_output(self, mock_imgs, _shared, _projs, _mdir, capsys):
        mock_imgs.return_value = [ImageInfo("terok-l0", "bkwm", "id1", "1GB", "2d ago")]
        from terok.cli.commands.storage import _cmd_overview

        _cmd_overview(json_output=True)
        data = json.loads(capsys.readouterr().out)
        assert "global" in data
        assert "projects" in data
        assert "grand_total_bytes" in data


# ---------------------------------------------------------------------------
# Detail output
# ---------------------------------------------------------------------------


class TestDetailOutput:
    """Detail mode prints per-task breakdown."""

    @patch("terok_sandbox.get_container_rw_sizes", return_value={})
    @patch("terok.lib.domain.storage.get_tasks_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_images")
    @patch("terok.lib.core.projects.load_project")
    @patch("terok.cli.commands.storage.supports_color", return_value=False)
    def test_prints_without_error(self, _color, mock_load, mock_imgs, _tasks, _ov, capsys):
        from unittest.mock import MagicMock

        from tests.testfs import MOCK_BASE

        proj = MagicMock()
        proj.tasks_root = MOCK_BASE / "tasks" / "myproject"
        mock_load.return_value = proj
        mock_imgs.return_value = [ImageInfo("myproject", "l2-cli", "id2", "3GB", "1d ago")]
        from terok.cli.commands.storage import _cmd_detail

        _cmd_detail("myproject")
        output = capsys.readouterr().out
        assert "myproject" in output
        assert "Project total" in output

    @patch("terok_sandbox.get_container_rw_sizes", return_value={})
    @patch("terok.lib.domain.storage.get_tasks_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_images")
    @patch("terok.lib.core.projects.load_project")
    def test_json_output(self, mock_load, mock_imgs, _tasks, _ov, capsys):
        from unittest.mock import MagicMock

        from tests.testfs import MOCK_BASE

        proj = MagicMock()
        proj.tasks_root = MOCK_BASE / "tasks" / "myproject"
        mock_load.return_value = proj
        mock_imgs.return_value = [ImageInfo("myproject", "l2-cli", "id2", "3GB", "1d ago")]
        from terok.cli.commands.storage import _cmd_detail

        _cmd_detail("myproject", json_output=True)
        data = json.loads(capsys.readouterr().out)
        assert data["project_id"] == "myproject"
        assert "images" in data
        assert "tasks" in data
