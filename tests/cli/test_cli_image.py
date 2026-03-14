# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from unittest.mock import patch

import pytest

from terok.cli.commands.image import _cmd_cleanup, _cmd_list
from terok.lib.containers.image_cleanup import CleanupResult, ImageInfo

IMAGES = [
    ImageInfo("terok-l0", "ubuntu-24.04", "sha256:aaa", "500MB", "2 days ago"),
    ImageInfo("myproj", "l2-cli", "sha256:bbb", "1.5GB", "1 day ago"),
]


@pytest.mark.parametrize(
    ("images", "expected_lines"),
    [
        pytest.param([], ["No terok images found"], id="empty"),
        pytest.param(
            IMAGES,
            ["terok-l0:ubuntu-24.04", "myproj:l2-cli", "2 image(s)"],
            id="with-images",
        ),
    ],
)
def test_cmd_list_outputs_expected(
    images: list[ImageInfo], expected_lines: list[str], capsys
) -> None:
    """``image list`` prints either the empty state or the discovered images."""
    with patch("terok.cli.commands.image.list_images", return_value=images):
        _cmd_list(None)

    output = capsys.readouterr().out
    for expected in expected_lines:
        assert expected in output


@pytest.mark.parametrize(
    ("result", "expected_lines"),
    [
        pytest.param(
            CleanupResult(removed=[], failed=[], dry_run=False),
            ["No orphaned terok images found"],
            id="nothing-to-clean",
        ),
        pytest.param(
            CleanupResult(removed=["old-proj:l2-cli"], failed=[], dry_run=True),
            ["Would remove", "1 image(s) would be removed"],
            id="dry-run",
        ),
        pytest.param(
            CleanupResult(
                removed=["old-proj:l2-cli"],
                failed=["in-use-proj:l2-cli"],
                dry_run=False,
            ),
            ["Removed", "Failed", "1 failed"],
            id="with-failures",
        ),
    ],
)
def test_cmd_cleanup_outputs_expected(
    result: CleanupResult,
    expected_lines: list[str],
    capsys,
) -> None:
    """``image cleanup`` reports dry-run, success, and failure states."""
    with patch("terok.cli.commands.image.cleanup_images", return_value=result):
        _cmd_cleanup(dry_run=result.dry_run)

    output = capsys.readouterr().out
    for expected in expected_lines:
        assert expected in output
