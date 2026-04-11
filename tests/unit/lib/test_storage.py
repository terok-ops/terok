# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the storage usage aggregation domain layer.

The domain module is the bridge between raw queries (agent, sandbox)
and the CLI presentation.  Tests verify classification, aggregation,
and the size parser — all with mocked sub-package calls.
"""

from __future__ import annotations

from unittest.mock import patch

from terok.lib.domain.image_cleanup import ImageInfo
from terok.lib.domain.storage import (
    ProjectSummary,
    StorageOverview,
    _is_global_image,
    format_bytes,
    get_project_storage_detail,
    get_storage_overview,
    parse_image_size,
)
from tests.testfs import MOCK_BASE

# ---------------------------------------------------------------------------
# parse_image_size — translating podman prose to integers
# ---------------------------------------------------------------------------


class TestParseImageSize:
    """Podman returns sizes as human-readable strings; we need bytes."""

    def test_gigabytes(self):
        assert parse_image_size("1.23GB") == 1_230_000_000

    def test_megabytes(self):
        assert parse_image_size("456MB") == 456_000_000

    def test_kilobytes(self):
        assert parse_image_size("12KB") == 12_000

    def test_bytes(self):
        assert parse_image_size("1024B") == 1024

    def test_garbage_returns_zero(self):
        assert parse_image_size("???") == 0

    def test_empty_returns_zero(self):
        assert parse_image_size("") == 0


# ---------------------------------------------------------------------------
# format_bytes — the inverse: integers to human-readable
# ---------------------------------------------------------------------------


class TestFormatBytes:
    """Symmetric companion to parse: bytes back to readable strings."""

    def test_gigabytes(self):
        assert format_bytes(1_500_000_000) == "1.5 GB"

    def test_megabytes(self):
        assert format_bytes(12_500_000) == "12.5 MB"

    def test_kilobytes(self):
        assert format_bytes(4_000) == "4.0 KB"

    def test_bytes(self):
        assert format_bytes(42) == "42 B"

    def test_zero(self):
        assert format_bytes(0) == "0 B"


# ---------------------------------------------------------------------------
# Image classification — global vs per-project
# ---------------------------------------------------------------------------


class TestImageClassification:
    """L0/L1 and dangling images are global; L2 images belong to a project."""

    def test_l0_is_global(self):
        img = ImageInfo("terok-l0", "bookworm", "sha256:abc", "1GB", "2d ago")
        assert _is_global_image(img)

    def test_l1_is_global(self):
        img = ImageInfo("terok-l1-cli", "bookworm", "sha256:def", "2GB", "3d ago")
        assert _is_global_image(img)

    def test_dangling_is_global(self):
        img = ImageInfo("<none>", "<none>", "sha256:ghi", "500MB", "1d ago")
        assert _is_global_image(img)

    def test_l2_is_not_global(self):
        img = ImageInfo("myproject", "l2-cli", "sha256:jkl", "3GB", "1d ago")
        assert not _is_global_image(img)


# ---------------------------------------------------------------------------
# StorageOverview properties
# ---------------------------------------------------------------------------


class TestStorageOverviewProperties:
    """The overview dataclass computes totals from its children."""

    def test_grand_total(self):
        overview = StorageOverview(
            global_images=[ImageInfo("terok-l0", "bkwm", "id1", "1GB", "2d")],
            shared_mounts=[],
            projects=[
                ProjectSummary(
                    "proj1", image_bytes=500_000_000, workspace_bytes=200_000_000, task_count=1
                )
            ],
        )
        # 1 GB image + 500M project images + 200M workspaces
        assert overview.grand_total == 1_000_000_000 + 500_000_000 + 200_000_000


# ---------------------------------------------------------------------------
# get_storage_overview — the orchestrator
# ---------------------------------------------------------------------------


_MOCK_IMAGES = [
    ImageInfo("terok-l0", "bookworm", "id1", "1GB", "2d ago"),
    ImageInfo("myproject", "l2-cli", "id2", "3GB", "1d ago"),
]


class TestGetStorageOverview:
    """Overview wires together image listing, mount queries, and project enumeration."""

    @patch("terok.lib.domain.storage.get_shared_mounts_storage", return_value=[])
    @patch("terok.lib.domain.storage.get_tasks_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_projects")
    @patch("terok.lib.domain.storage.list_images", return_value=_MOCK_IMAGES)
    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=MOCK_BASE / "mounts")
    def test_classifies_images(self, _mounts, _imgs, mock_projects, _tasks, _shared):
        mock_projects.return_value = [_fake_project("myproject")]
        overview = get_storage_overview()
        assert len(overview.global_images) == 1
        assert overview.global_images[0].repository == "terok-l0"
        assert len(overview.projects) == 1
        assert overview.projects[0].project_id == "myproject"

    @patch("terok.lib.domain.storage.get_shared_mounts_storage", return_value=[])
    @patch("terok.lib.domain.storage.get_tasks_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_projects", return_value=[])
    @patch("terok.lib.domain.storage.list_images", return_value=[])
    @patch("terok.lib.domain.storage.sandbox_live_mounts_dir", return_value=MOCK_BASE / "mounts")
    def test_empty_system(self, _mounts, _imgs, _projects, _tasks, _shared):
        overview = get_storage_overview()
        assert overview.grand_total == 0


# ---------------------------------------------------------------------------
# get_project_storage_detail — zooming in
# ---------------------------------------------------------------------------


class TestGetProjectStorageDetail:
    """Detail mode queries per-task sizes and overlay data."""

    @patch("terok_sandbox.get_container_rw_sizes", return_value={"abc": 1024})
    @patch("terok.lib.domain.storage.get_tasks_storage", return_value=[])
    @patch("terok.lib.domain.storage.list_images", return_value=[])
    @patch("terok.lib.core.projects.load_project")
    def test_returns_project_detail(self, mock_load, _imgs, _tasks, _overlays):
        mock_load.return_value = _fake_project("myproject")
        detail = get_project_storage_detail("myproject")
        assert detail.project_id == "myproject"
        assert detail.overlays == {"abc": 1024}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_project(pid: str):
    """Minimal stand-in for ProjectConfig."""
    from unittest.mock import MagicMock

    p = MagicMock()
    p.id = pid
    p.tasks_root = MOCK_BASE / "tasks" / pid
    return p
