# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for image cleanup helpers."""

from __future__ import annotations

import subprocess
import unittest.mock

import pytest

from terok.lib.containers.image_cleanup import (
    ImageInfo,
    cleanup_images,
    find_orphaned_images,
    list_images,
)


def podman_result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    """Create a mock podman result."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


ORPHAN_IMAGE = ImageInfo("old-proj", "l2-cli", "sha256:abc", "1GB", "5 days ago")


class TestImageInfo:
    """Tests for ImageInfo dataclass."""

    @pytest.mark.parametrize(
        ("image", "expected"),
        [
            (
                ImageInfo("terok-l0", "ubuntu-24.04", "sha256:abc", "500MB", "2 days ago"),
                "terok-l0:ubuntu-24.04",
            ),
            (
                ImageInfo("<none>", "<none>", "sha256:abc123def456", "500MB", "2 days ago"),
                "<none> (sha256:abc12)",
            ),
        ],
        ids=["tagged", "dangling"],
    )
    def test_full_name(self, image: ImageInfo, expected: str) -> None:
        assert image.full_name == expected


class TestListImages:
    """Tests for list_images()."""

    @pytest.mark.parametrize(
        ("stdout", "project_id", "expected_names"),
        [
            (
                "terok-l0\tubuntu-24.04\tsha256:aaa\t500MB\t2 days ago\n"
                "terok-l1-cli\tubuntu-24.04\tsha256:bbb\t1.2GB\t2 days ago\n"
                "myproj\tl2-cli\tsha256:ccc\t1.5GB\t1 day ago\n"
                "ubuntu\t24.04\tsha256:ddd\t77MB\t3 weeks ago\n",
                None,
                {"terok-l0:ubuntu-24.04", "terok-l1-cli:ubuntu-24.04", "myproj:l2-cli"},
            ),
            (
                "terok-l0\tubuntu-24.04\tsha256:aaa\t500MB\t2 days ago\n"
                "proj-a\tl2-cli\tsha256:bbb\t1.5GB\t1 day ago\n"
                "proj-b\tl2-cli\tsha256:ccc\t1.5GB\t1 day ago\n",
                "proj-a",
                {"terok-l0:ubuntu-24.04", "proj-a:l2-cli"},
            ),
            (
                "myproj\tl2-dev\tsha256:aaa\t1GB\t1 day ago\n",
                None,
                {"myproj:l2-dev"},
            ),
        ],
        ids=["all-terok-images", "filtered-by-project", "dev-tag"],
    )
    @unittest.mock.patch("terok.lib.containers.image_cleanup._run_podman")
    def test_list_images(
        self,
        mock_podman: unittest.mock.Mock,
        stdout: str,
        project_id: str | None,
        expected_names: set[str],
    ) -> None:
        mock_podman.return_value = podman_result(stdout)
        images = list_images(project_id)
        assert {image.full_name for image in images} == expected_names

    @unittest.mock.patch("terok.lib.containers.image_cleanup._run_podman")
    def test_list_images_podman_failure(self, mock_podman: unittest.mock.Mock) -> None:
        mock_podman.return_value = podman_result(returncode=1)
        assert list_images() == []


class TestFindOrphanedImages:
    """Tests for find_orphaned_images()."""

    @pytest.mark.parametrize(
        ("known_projects", "images", "dangling", "is_terok_built", "expected_ids"),
        [
            (
                {"proj-a"},
                [
                    ImageInfo("proj-a", "l2-cli", "sha256:aaa", "1GB", "1 day ago"),
                    ImageInfo("proj-deleted", "l2-cli", "sha256:bbb", "1GB", "5 days ago"),
                ],
                [],
                True,
                {"sha256:bbb"},
            ),
            (
                set(),
                [ImageInfo("foreign-img", "l2-cli", "sha256:fff", "1GB", "1 day ago")],
                [],
                False,
                set(),
            ),
            (
                set(),
                [ImageInfo("proj-x", "l2-cli", "sha256:same", "1GB", "1 day ago")],
                [ImageInfo("proj-x", "l2-cli", "sha256:same", "1GB", "1 day ago")],
                True,
                {"sha256:same"},
            ),
            (
                None,
                [ImageInfo("proj-a", "l2-cli", "sha256:aaa", "1GB", "1 day ago")],
                [],
                True,
                set(),
            ),
        ],
        ids=[
            "finds-orphaned-l2",
            "skips-non-terok-l2",
            "deduplicates-by-image-id",
            "skips-on-discovery-failure",
        ],
    )
    @unittest.mock.patch("terok.lib.containers.image_cleanup._is_terok_built_image")
    @unittest.mock.patch("terok.lib.containers.image_cleanup._find_dangling_terok_images")
    @unittest.mock.patch("terok.lib.containers.image_cleanup.list_images")
    @unittest.mock.patch("terok.lib.containers.image_cleanup._known_project_ids")
    def test_find_orphaned_images(
        self,
        mock_known: unittest.mock.Mock,
        mock_list: unittest.mock.Mock,
        mock_dangling: unittest.mock.Mock,
        mock_built: unittest.mock.Mock,
        known_projects: set[str] | None,
        images: list[ImageInfo],
        dangling: list[ImageInfo],
        is_terok_built: bool,
        expected_ids: set[str],
    ) -> None:
        mock_known.return_value = known_projects
        mock_list.return_value = images
        mock_dangling.return_value = dangling
        mock_built.return_value = is_terok_built
        orphaned = find_orphaned_images()
        assert {image.image_id for image in orphaned} == expected_ids
        if known_projects is None:
            mock_list.assert_not_called()


class TestCleanupImages:
    """Tests for cleanup_images()."""

    @pytest.mark.parametrize(
        ("dry_run", "podman_returncode", "expected_removed", "expected_failed"),
        [
            (True, 0, ["old-proj:l2-cli"], []),
            (False, 0, ["old-proj:l2-cli"], []),
            (False, 1, [], ["old-proj:l2-cli"]),
        ],
        ids=["dry-run", "success", "failure"],
    )
    @unittest.mock.patch("terok.lib.containers.image_cleanup._run_podman")
    @unittest.mock.patch("terok.lib.containers.image_cleanup.find_orphaned_images")
    def test_cleanup_images(
        self,
        mock_orphaned: unittest.mock.Mock,
        mock_podman: unittest.mock.Mock,
        dry_run: bool,
        podman_returncode: int,
        expected_removed: list[str],
        expected_failed: list[str],
    ) -> None:
        mock_orphaned.return_value = [ORPHAN_IMAGE]
        mock_podman.return_value = podman_result(returncode=podman_returncode)
        result = cleanup_images(dry_run=dry_run)
        assert result.dry_run is dry_run
        assert result.removed == expected_removed
        assert result.failed == expected_failed
        if dry_run:
            mock_podman.assert_not_called()
        else:
            mock_podman.assert_called_once_with("image", "rm", "sha256:abc")

    @unittest.mock.patch("terok.lib.containers.image_cleanup.find_orphaned_images")
    def test_nothing_to_clean(self, mock_orphaned: unittest.mock.Mock) -> None:
        mock_orphaned.return_value = []
        result = cleanup_images()
        assert result.removed == []
        assert result.failed == []
