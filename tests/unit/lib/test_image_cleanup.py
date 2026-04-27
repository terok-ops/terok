# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for image cleanup helpers."""

from __future__ import annotations

import unittest.mock

import pytest

from terok.lib.domain.image_cleanup import (
    ImageInfo,
    cleanup_images,
    find_orphaned_images,
    list_images,
)


def _mock_image(
    *,
    ref: str,
    repository: str,
    tag: str,
    size: str = "1GB",
    created: str = "1 day ago",
) -> unittest.mock.Mock:
    """Build a Mock that quacks like a [`terok_sandbox.Image`][]."""
    mock_image = unittest.mock.Mock()
    mock_image.ref = ref
    mock_image.repository = repository
    mock_image.tag = tag
    mock_image.size = size
    mock_image.created = created
    return mock_image


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
        """Dangling images get the short-id display; tagged images get ``repo:tag``."""
        assert image.full_name == expected


class TestListImages:
    """Tests for ``list_images()``."""

    @pytest.mark.parametrize(
        ("images_spec", "project_id", "expected_names"),
        [
            (
                [
                    ("sha256:aaa", "terok-l0", "ubuntu-24.04"),
                    ("sha256:bbb", "terok-l1-cli", "ubuntu-24.04"),
                    ("sha256:ccc", "myproj", "l2-cli"),
                    ("sha256:ddd", "ubuntu", "24.04"),
                ],
                None,
                {"terok-l0:ubuntu-24.04", "terok-l1-cli:ubuntu-24.04", "myproj:l2-cli"},
            ),
            (
                [
                    ("sha256:aaa", "terok-l0", "ubuntu-24.04"),
                    ("sha256:bbb", "proj-a", "l2-cli"),
                    ("sha256:ccc", "proj-b", "l2-cli"),
                ],
                "proj-a",
                {"terok-l0:ubuntu-24.04", "proj-a:l2-cli"},
            ),
            (
                [("sha256:aaa", "myproj", "l2-dev")],
                None,
                {"myproj:l2-dev"},
            ),
        ],
        ids=["all-terok-images", "filtered-by-project", "dev-tag"],
    )
    # removed — mock_runtime fixture handles it
    def test_list_images(
        self,
        mock_runtime: unittest.mock.Mock,
        images_spec: list[tuple[str, str, str]],
        project_id: str | None,
        expected_names: set[str],
    ) -> None:
        """Runtime enumeration is filtered to terok images (optionally by project)."""
        mock_runtime.images.return_value = [
            _mock_image(ref=ref, repository=repo, tag=tag) for ref, repo, tag in images_spec
        ]
        images = list_images(project_id)
        assert {image.full_name for image in images} == expected_names

    # removed — mock_runtime fixture handles it
    def test_list_images_podman_failure(self, mock_runtime: unittest.mock.Mock) -> None:
        """Runtime returns an empty list on podman failure — terok passes it through."""
        mock_runtime.images.return_value = []
        assert list_images() == []


class TestFindOrphanedImages:
    """Tests for ``find_orphaned_images()``."""

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
    @unittest.mock.patch("terok.lib.domain.image_cleanup._is_terok_built_image")
    @unittest.mock.patch("terok.lib.domain.image_cleanup._find_dangling_terok_images")
    @unittest.mock.patch("terok.lib.domain.image_cleanup.list_images")
    @unittest.mock.patch("terok.lib.domain.image_cleanup._known_project_ids")
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
        """Orphaned = L2-of-missing-project ∪ terok-built dangling, dedup'd by ID."""
        mock_known.return_value = known_projects
        mock_list.return_value = images
        mock_dangling.return_value = dangling
        mock_built.return_value = is_terok_built
        orphaned = find_orphaned_images()
        assert {image.image_id for image in orphaned} == expected_ids
        if known_projects is None:
            mock_list.assert_not_called()


class TestCleanupImages:
    """Tests for ``cleanup_images()``."""

    @pytest.mark.parametrize(
        ("dry_run", "remove_result", "expected_removed", "expected_failed"),
        [
            (True, True, ["old-proj:l2-cli"], []),
            (False, True, ["old-proj:l2-cli"], []),
            (False, False, [], ["old-proj:l2-cli"]),
        ],
        ids=["dry-run", "success", "failure"],
    )
    # removed — mock_runtime fixture handles it
    @unittest.mock.patch("terok.lib.domain.image_cleanup.find_orphaned_images")
    def test_cleanup_images(
        self,
        mock_orphaned: unittest.mock.Mock,
        mock_runtime: unittest.mock.Mock,
        dry_run: bool,
        remove_result: bool,
        expected_removed: list[str],
        expected_failed: list[str],
    ) -> None:
        """Dry run lists only; real runs delegate to ``Image.remove`` on the runtime."""
        mock_orphaned.return_value = [ORPHAN_IMAGE]
        mock_image = unittest.mock.Mock()
        mock_image.remove.return_value = remove_result
        mock_runtime.image.return_value = mock_image

        result = cleanup_images(dry_run=dry_run)
        assert result.dry_run is dry_run
        assert result.removed == expected_removed
        assert result.failed == expected_failed
        if dry_run:
            mock_image.remove.assert_not_called()
        else:
            mock_runtime.image.assert_called_once_with("sha256:abc")
            mock_image.remove.assert_called_once_with()

    @unittest.mock.patch("terok.lib.domain.image_cleanup.find_orphaned_images")
    def test_nothing_to_clean(self, mock_orphaned: unittest.mock.Mock) -> None:
        """Empty orphan list yields an empty cleanup result."""
        mock_orphaned.return_value = []
        result = cleanup_images()
        assert result.removed == []
        assert result.failed == []
