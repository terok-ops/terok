# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for image-tag helper functions."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from terok.lib.core import images

BASE_IMAGE_FUNCS: list[tuple[Callable[[str], str], str]] = [
    (images.base_dev_image, "terok-l0"),
    (images.agent_cli_image, "terok-l1-cli"),
]
BASE_IMAGE_IDS = [prefix for _, prefix in BASE_IMAGE_FUNCS]
BASE_IMAGE_CALLABLES = [func for func, _ in BASE_IMAGE_FUNCS]


@pytest.mark.parametrize(
    ("base_image", "expected"),
    [
        ("", "ubuntu-24.04"),
        ("   ", "ubuntu-24.04"),
        ("ubuntu-22.04", "ubuntu-22.04"),
        ("Ubuntu-22.04", "ubuntu-22.04"),
        ("ubuntu@22#04", "ubuntu-22-04"),
        ("test@#$%^&*()image", "test-image"),
        ("--ubuntu-22.04--", "ubuntu-22.04"),
        ("ubuntu.22.04", "ubuntu.22.04"),
        ("ubuntu_22_04", "ubuntu_22_04"),
        ("ubuntu-22.04_LTS", "ubuntu-22.04_lts"),
        ("@#$%^&*()", "ubuntu-24.04"),
    ],
    ids=[
        "empty",
        "whitespace",
        "simple",
        "lowercases",
        "sanitizes-specials",
        "collapses-specials",
        "strips-edges",
        "preserves-dots",
        "preserves-underscores",
        "mixed-valid-chars",
        "only-specials",
    ],
)
def test_base_tag_simple_cases(base_image: str, expected: str) -> None:
    assert images._base_tag(base_image) == expected


@pytest.mark.parametrize(
    ("name", "prefix_len", "suffix_len"),
    [("a" * 120, 120, 0), ("a" * 121, 111, 8), ("ubuntu@special" * 20, 111, 8)],
    ids=["under-limit", "over-limit", "sanitize-then-truncate"],
)
def test_base_tag_length_and_hash(name: str, prefix_len: int, suffix_len: int) -> None:
    result = images._base_tag(name)
    if len(name) <= 120 and "@" not in name:
        assert result == name
        return
    hash_part = result.split("-")[-1]
    assert len(result) == 120
    assert len(hash_part) == suffix_len
    assert hash_part.isalnum()
    if "@" in name:
        assert "@" not in result
    else:
        assert result.startswith("a" * prefix_len)


def test_base_tag_long_name_hash_is_stable() -> None:
    name = "b" * 150
    assert images._base_tag(name) == images._base_tag(name)


def test_base_tag_long_name_hash_changes_for_different_inputs() -> None:
    assert images._base_tag("c" * 150) != images._base_tag("d" * 150)


@pytest.mark.parametrize(
    ("func", "base_image", "expected"),
    [
        (images.base_dev_image, "ubuntu-22.04", "terok-l0:ubuntu-22.04"),
        (images.base_dev_image, "ubuntu@22.04", "terok-l0:ubuntu-22.04"),
        (images.agent_cli_image, "ubuntu-22.04", "terok-l1-cli:ubuntu-22.04"),
        (images.agent_cli_image, "ubuntu@22.04", "terok-l1-cli:ubuntu-22.04"),
    ],
    ids=["l0", "l0-sanitized", "l1-cli", "l1-cli-sanitized"],
)
def test_base_image_functions(
    func: Callable[[str], str],
    base_image: str,
    expected: str,
) -> None:
    assert func(base_image) == expected


@pytest.mark.parametrize(
    ("func", "expected"),
    [
        (images.project_cli_image, "my-project:l2-cli"),
        (images.project_dev_image, "my-project:l2-dev"),
    ],
    ids=["project-cli", "project-dev"],
)
def test_project_image_functions(func: Callable[[str], str], expected: str) -> None:
    assert func("my-project") == expected


@pytest.mark.parametrize(("func", "prefix"), BASE_IMAGE_FUNCS, ids=BASE_IMAGE_IDS)
def test_base_image_functions_handle_empty_input(
    func: Callable[[str], str],
    prefix: str,
) -> None:
    assert func("") == f"{prefix}:ubuntu-24.04"


@pytest.mark.parametrize("func", BASE_IMAGE_CALLABLES, ids=BASE_IMAGE_IDS)
def test_base_image_functions_share_long_tag_generation(func: Callable[[str], str]) -> None:
    assert len(func("x" * 150).split(":", 1)[1]) == 120


def test_base_image_functions_reuse_the_same_long_tag() -> None:
    long_name = "x" * 150
    tags = {func(long_name).split(":", 1)[1] for func, _ in BASE_IMAGE_FUNCS}
    assert len(tags) == 1
