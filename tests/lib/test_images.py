# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for image-tag helper functions."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from terok.lib.core import images

BASE_TAG_CASES = [
    pytest.param("", "ubuntu-24.04", id="empty"),
    pytest.param("   ", "ubuntu-24.04", id="whitespace"),
    pytest.param("ubuntu-22.04", "ubuntu-22.04", id="simple"),
    pytest.param("Ubuntu-22.04", "ubuntu-22.04", id="lowercases"),
    pytest.param("ubuntu@22#04", "ubuntu-22-04", id="sanitizes-specials"),
    pytest.param("test@#$%^&*()image", "test-image", id="collapses-specials"),
    pytest.param("--ubuntu-22.04--", "ubuntu-22.04", id="strips-edges"),
    pytest.param("ubuntu.22.04", "ubuntu.22.04", id="preserves-dots"),
    pytest.param("ubuntu_22_04", "ubuntu_22_04", id="preserves-underscores"),
    pytest.param("ubuntu-22.04_LTS", "ubuntu-22.04_lts", id="mixed-valid-chars"),
    pytest.param("@#$%^&*()", "ubuntu-24.04", id="only-specials"),
]

BASE_IMAGE_FUNCS: list[tuple[Callable[[str], str], str]] = [
    (images.base_dev_image, "terok-l0"),
    (images.agent_cli_image, "terok-l1-cli"),
    (images.agent_ui_image, "terok-l1-ui"),
]


@pytest.mark.parametrize(("base_image", "expected"), BASE_TAG_CASES)
def test_base_tag_simple_cases(base_image: str, expected: str) -> None:
    assert images._base_tag(base_image) == expected


def test_base_tag_long_name_under_limit() -> None:
    name = "a" * 120
    result = images._base_tag(name)

    assert result == name
    assert len(result) == 120


def test_base_tag_long_name_over_limit() -> None:
    result = images._base_tag("a" * 121)
    hash_part = result.split("-")[-1]

    assert len(result) == 120
    assert result.startswith("a" * 111)
    assert "-" in result[111:]
    assert len(hash_part) == 8
    assert hash_part.isalnum()


def test_base_tag_long_name_hash_is_stable() -> None:
    name = "b" * 150
    assert images._base_tag(name) == images._base_tag(name)


def test_base_tag_long_name_hash_changes_for_different_inputs() -> None:
    assert images._base_tag("c" * 150) != images._base_tag("d" * 150)


def test_base_tag_long_name_sanitizes_before_truncating() -> None:
    result = images._base_tag("ubuntu@special" * 20)

    assert len(result) == 120
    assert "@" not in result


@pytest.mark.parametrize(
    ("func", "base_image", "expected"),
    [
        pytest.param(images.base_dev_image, "ubuntu-22.04", "terok-l0:ubuntu-22.04", id="l0"),
        pytest.param(
            images.base_dev_image,
            "ubuntu@22.04",
            "terok-l0:ubuntu-22.04",
            id="l0-sanitized",
        ),
        pytest.param(
            images.agent_cli_image,
            "ubuntu-22.04",
            "terok-l1-cli:ubuntu-22.04",
            id="l1-cli",
        ),
        pytest.param(
            images.agent_cli_image,
            "ubuntu@22.04",
            "terok-l1-cli:ubuntu-22.04",
            id="l1-cli-sanitized",
        ),
        pytest.param(
            images.agent_ui_image,
            "ubuntu-22.04",
            "terok-l1-ui:ubuntu-22.04",
            id="l1-ui",
        ),
        pytest.param(
            images.agent_ui_image,
            "ubuntu@22.04",
            "terok-l1-ui:ubuntu-22.04",
            id="l1-ui-sanitized",
        ),
    ],
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
        pytest.param(images.project_cli_image, "my-project:l2-cli", id="project-cli"),
        pytest.param(images.project_web_image, "my-project:l2-web", id="project-web"),
        pytest.param(images.project_dev_image, "my-project:l2-dev", id="project-dev"),
    ],
)
def test_project_image_functions(func: Callable[[str], str], expected: str) -> None:
    assert func("my-project") == expected


@pytest.mark.parametrize(
    "func,prefix", BASE_IMAGE_FUNCS, ids=[prefix for _, prefix in BASE_IMAGE_FUNCS]
)
def test_base_image_functions_handle_empty_input(
    func: Callable[[str], str],
    prefix: str,
) -> None:
    assert func("") == f"{prefix}:ubuntu-24.04"


@pytest.mark.parametrize(
    "func", [f for f, _ in BASE_IMAGE_FUNCS], ids=[p for _, p in BASE_IMAGE_FUNCS]
)
def test_base_image_functions_share_long_tag_generation(func: Callable[[str], str]) -> None:
    result = func("x" * 150).split(":", 1)[1]

    assert len(result) == 120


def test_base_image_functions_reuse_the_same_long_tag() -> None:
    long_name = "x" * 150
    tags = {func(long_name).split(":", 1)[1] for func, _ in BASE_IMAGE_FUNCS}

    assert len(tags) == 1
