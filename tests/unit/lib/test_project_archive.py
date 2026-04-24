# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project archival helpers."""

from __future__ import annotations

import re
import tarfile
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from terok.lib.core.config import archive_dir, build_dir
from terok.lib.core.paths import core_state_dir
from terok.lib.core.projects import load_project
from terok.lib.domain.facade import delete_project
from terok.lib.domain.project import _archive_project
from terok.lib.util.fs import (
    archive_timestamp,
    create_archive_dir,
    create_archive_file,
    unique_archive_path,
)
from tests.test_utils import project_env


def project_yaml(project_id: str) -> str:
    """Build a minimal project config for archive tests."""
    return f"project:\n  id: {project_id}\ngit:\n  upstream_url: https://example.com/repo.git\n"


def archive_member_names(path: str) -> list[str]:
    """Return member names from a tar.gz archive."""
    with tarfile.open(path, "r:gz") as tar:
        return tar.getnames()


def create_task_state(project_id: str) -> None:
    """Create a sample task metadata file for an archived project."""
    meta_dir = core_state_dir() / "projects" / project_id / "tasks"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "1.yml").write_text("task_id: '1'\nname: test\n")


def create_build_dir(project_id: str) -> None:
    """Create a sample build artifact for an archived project."""
    staging = build_dir() / project_id
    staging.mkdir(parents=True, exist_ok=True)
    (staging / "L2.Dockerfile").write_text("FROM scratch")


class TestArchiveTimestamp:
    """Tests for archive_timestamp()."""

    def test_returns_utc_timestamp_string(self) -> None:
        assert re.search(r"^\d{8}T\d{6}(?:\d+)?Z$", archive_timestamp())

    def test_changes_when_time_changes(self) -> None:
        first_dt = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
        second_dt = datetime(2026, 3, 15, 12, 0, 1, tzinfo=UTC)
        with patch("terok.lib.util.fs.datetime") as mock_datetime:
            mock_datetime.now.side_effect = [first_dt, second_dt]
            first = archive_timestamp()
            second = archive_timestamp()
        assert first != second


@pytest.mark.parametrize(
    ("root_setup", "name", "suffix", "expected_name"),
    [
        ([], "test", ".tar.gz", "test.tar.gz"),
        (["test.tar.gz"], "test", ".tar.gz", "test_1.tar.gz"),
        (["test.tar.gz", "test_1.tar.gz"], "test", ".tar.gz", "test_2.tar.gz"),
        (["mydir/"], "mydir", "", "mydir_1"),
    ],
    ids=["basic", "collision", "multiple-collisions", "directory-style"],
)
def test_unique_archive_path(
    root_setup: list[str],
    name: str,
    suffix: str,
    expected_name: str,
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for entry in root_setup:
            path = root / entry.rstrip("/")
            if entry.endswith("/"):
                path.mkdir()
            else:
                path.write_text("existing")
        assert unique_archive_path(root, name, suffix) == root / expected_name


@pytest.mark.parametrize(
    ("factory", "suffix", "name", "expected_name", "setup_existing"),
    [
        (create_archive_dir, None, "myarchive", "myarchive", None),
        (create_archive_dir, None, "test", "test_1", "test"),
        (create_archive_file, None, "myarchive", "myarchive.tar.gz", None),
        (create_archive_file, None, "test", "test_1.tar.gz", "test.tar.gz"),
        (create_archive_file, ".zip", "myarchive", "myarchive.zip", None),
    ],
    ids=["dir", "dir-collision", "file", "file-collision", "file-custom-suffix"],
)
def test_archive_factories(
    factory: Callable[..., Path],
    suffix: str | None,
    name: str,
    expected_name: str,
    setup_existing: str | None,
) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        if setup_existing:
            existing = root / setup_existing
            if existing.suffix:
                existing.write_text("existing")
            else:
                existing.mkdir()
        kwargs = {} if suffix is None else {"suffix": suffix}
        path = factory(root, name, **kwargs)
        assert path.name == expected_name
        assert path.exists()


def test_archive_factories_create_missing_root() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "nested" / "root"
        assert create_archive_dir(root, "test").is_dir()
        assert create_archive_file(root, "test").is_file()


class TestArchiveProject:
    """Tests for _archive_project()."""

    @pytest.mark.parametrize(
        ("project_id", "setup", "expected_fragment"),
        [
            ("arch-cfg", lambda _pid: None, "config/"),
            ("arch-state", create_task_state, "state/"),
            ("arch-build", create_build_dir, "build/"),
        ],
        ids=["config", "state", "build"],
    )
    def test_archive_includes_expected_sections(
        self,
        project_id: str,
        setup: Callable[[str], None],
        expected_fragment: str,
    ) -> None:
        with project_env(project_yaml(project_id), project_id=project_id):
            setup(project_id)
            archive_path = _archive_project(project_id)
            assert archive_path is not None
            assert any(expected_fragment in name for name in archive_member_names(archive_path))

    def test_missing_dirs_graceful(self) -> None:
        with project_env(project_yaml("arch-min"), project_id="arch-min"):
            archive_path = _archive_project("arch-min")
            assert archive_path is not None
            assert Path(archive_path).is_file()

    def test_archive_stored_in_archive_dir(self) -> None:
        with project_env(project_yaml("arch-loc"), project_id="arch-loc"):
            archive_path = _archive_project("arch-loc")
            assert archive_path is not None
            assert Path(archive_path).parent == archive_dir()

    def test_archive_bundles_task_archives_and_cleans_up(self) -> None:
        """Project archive includes task archives and removes the subtree."""
        pid = "arch-tasks"
        with project_env(project_yaml(pid), project_id=pid):
            # Populate task archive entries
            task_archive_root = archive_dir() / pid / "tasks"
            task_archive_root.mkdir(parents=True, exist_ok=True)
            entry = task_archive_root / "20260301T100000Z_1_mytask"
            entry.mkdir()
            (entry / "task.yml").write_text("task_id: '1'\nname: mytask\n")

            archive_path = _archive_project(pid)
            assert archive_path is not None
            members = archive_member_names(archive_path)
            assert any("task-archives/" in name for name in members)
            # Task archive subtree removed after bundling
            assert not (archive_dir() / pid).is_dir()


class TestDeleteProjectArchive:
    """Tests for delete_project() archive integration."""

    def test_delete_creates_archive_before_deleting(self) -> None:
        with project_env(project_yaml("del-arch"), project_id="del-arch"):
            create_task_state("del-arch")
            create_build_dir("del-arch")
            result = delete_project("del-arch")
            assert "archive" in result
            archive_path = Path(result["archive"])
            members = archive_member_names(result["archive"])
            assert archive_path.is_file()
            assert archive_path.suffixes[-2:] == [".tar", ".gz"]
            assert "state/tasks/1.yml" in members
            assert "build/L2.Dockerfile" in members

    def test_archive_survives_deletion(self) -> None:
        with project_env(project_yaml("del-surv"), project_id="del-surv"):
            project = load_project("del-surv")
            result = delete_project("del-surv")
            assert not project.root.is_dir()
            assert Path(result["archive"]).is_file()

    def test_archive_contains_project_config(self) -> None:
        with project_env(project_yaml("del-cont"), project_id="del-cont"):
            result = delete_project("del-cont")
            assert any(
                name.startswith("config/") for name in archive_member_names(result["archive"])
            )
