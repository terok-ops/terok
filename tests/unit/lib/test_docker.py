# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from terok.lib.containers.docker import build_images, generate_dockerfiles
from terok.lib.core.config import build_root
from terok.lib.core.images import base_dev_image
from tests.test_utils import mock_git_config, project_env

UPSTREAM_URL = "https://example.com/repo.git"
DEFAULT_BRANCH = "main"


@contextmanager
def docker_project(project_id: str, *, security_class: str = "online") -> Iterator[object]:
    """Create a minimal project config suitable for Dockerfile generation tests."""
    lines = [f"project:\n  id: {project_id}\n"]
    if security_class != "online":
        lines.append(f"  security_class: {security_class}\n")
    lines.append("git:\n")
    lines.append(f"  upstream_url: {UPSTREAM_URL}\n")
    lines.append(f"  default_branch: {DEFAULT_BRANCH}\n")
    yaml = "".join(lines)
    with project_env(yaml, project_id=project_id) as env:
        yield env


def build_commands(
    project_id: str,
    *,
    image_exists: bool = True,
    image_exists_side_effect: Callable[[str], bool] | None = None,
    **build_kwargs: object,
) -> list[list[str]]:
    """Run ``build_images`` with Podman mocked and return captured build commands."""
    commands: list[list[str]] = []

    def mock_run(cmd: list[str], **_kwargs: object) -> Mock:
        if "podman" in cmd and "build" in cmd:
            commands.append(cmd)
        return Mock(returncode=0)

    image_exists_patch = (
        patch("terok.lib.containers.docker._image_exists", side_effect=image_exists_side_effect)
        if image_exists_side_effect is not None
        else patch("terok.lib.containers.docker._image_exists", return_value=image_exists)
    )
    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("terok.lib.containers.docker._check_podman_available"),
        image_exists_patch,
        mock_git_config(),
    ):
        build_images(project_id, **build_kwargs)
    return commands


def test_generate_dockerfiles_outputs_expected_files_and_content() -> None:
    """Dockerfile generation writes all expected layers and helper scripts."""
    project_id = "proj4"
    with docker_project(project_id):
        generate_dockerfiles(project_id)
        out_dir = build_root() / project_id

        assert all(
            (out_dir / name).is_file()
            for name in ("L0.Dockerfile", "L1.cli.Dockerfile", "L2.Dockerfile")
        )
        l0_content = (out_dir / "L0.Dockerfile").read_text(encoding="utf-8")
        assert all(
            token in l0_content
            for token in (
                'LANG="en_US.UTF-8"',
                'LC_ALL="en_US.UTF-8"',
                'LANGUAGE="en_US:en"',
                "locales",
                "locale-gen en_US.UTF-8",
            )
        )
        l2_content = (out_dir / "L2.Dockerfile").read_text(encoding="utf-8")
        l1_cli_content = (out_dir / "L1.cli.Dockerfile").read_text(encoding="utf-8")
        assert "hilfe --kurz" in l1_cli_content
        assert f'SSH_KEY_NAME="id_ed25519_{project_id}"' in l2_content
        assert "{{DEFAULT_BRANCH}}" not in l2_content
        assert f'CODE_REPO="{UPSTREAM_URL}"' in l2_content
        assert any(path.is_file() for path in (out_dir / "scripts").iterdir())


def test_generate_dockerfiles_uses_gatekeeping_code_repo() -> None:
    """Gatekeeping projects clone from the local git gate instead of upstream."""
    with docker_project("proj_gated", security_class="gatekeeping"):
        generate_dockerfiles("proj_gated")
        content = (build_root() / "proj_gated" / "L2.Dockerfile").read_text(encoding="utf-8")
        assert 'CODE_REPO="file:///git-gate/gate.git"' in content
        assert f'CODE_REPO="{UPSTREAM_URL}"' not in content


def test_l1_cli_pipx_inject_has_env_vars() -> None:
    """The CLI image sets the expected pipx env vars and package installation lines."""
    with docker_project("proj_pipx_test"):
        generate_dockerfiles("proj_pipx_test")
        content = (build_root() / "proj_pipx_test" / "L1.cli.Dockerfile").read_text(
            encoding="utf-8"
        )
        assert "PIPX_HOME=/opt/pipx" in content
        assert "PIPX_BIN_DIR=/usr/local/bin" in content
        assert "pipx install mistral-vibe" in content
        assert "pipx inject mistral-vibe mistralai" in content


@pytest.mark.parametrize(
    ("project_id", "capture_kwargs", "expected_count", "expected_suffixes"),
    [
        pytest.param(
            "proj_build_test",
            {"image_exists": True},
            1,
            ["L2.Dockerfile"],
            id="l2-only-when-base-exists",
        ),
        pytest.param(
            "proj_build_auto",
            {"image_exists": False},
            3,
            [
                "L0.Dockerfile",
                "L1.cli.Dockerfile",
                "L2.Dockerfile",
            ],
            id="auto-detect-missing-base",
        ),
    ],
)
def test_build_images_layer_selection(
    project_id: str,
    capture_kwargs: dict[str, object],
    expected_count: int,
    expected_suffixes: list[str],
) -> None:
    """Image building selects the expected Dockerfile layers."""
    with docker_project(project_id):
        generate_dockerfiles(project_id)
        commands = build_commands(project_id, **capture_kwargs)

    assert len(commands) == expected_count
    dockerfile_names = [
        next(part for part in cmd if part.endswith(".Dockerfile")).rsplit("/", 1)[-1]
        for cmd in commands
    ]
    assert dockerfile_names == expected_suffixes


def test_build_images_rebuild_agents_builds_all_layers() -> None:
    """``rebuild_agents=True`` rebuilds the full stack and busts the agent cache."""
    with docker_project("proj_build_agents"):
        generate_dockerfiles("proj_build_agents")
        commands = build_commands("proj_build_agents", image_exists=True, rebuild_agents=True)

    assert len(commands) == 3
    assert "AGENT_CACHE_BUST" in " ".join(commands[1])


@pytest.mark.parametrize(
    ("build_kwargs", "required_tokens"),
    [
        pytest.param({"full_rebuild": True}, ["--no-cache", "--pull=always"], id="full-rebuild"),
        pytest.param({"include_dev": True}, [":l2-dev"], id="include-dev"),
    ],
)
def test_build_images_applies_expected_flags(
    build_kwargs: dict[str, object],
    required_tokens: list[str],
) -> None:
    """Build flags are passed through to the rendered Podman build commands."""
    with docker_project("proj_build_flags"):
        generate_dockerfiles("proj_build_flags")
        commands = build_commands("proj_build_flags", image_exists=True, **build_kwargs)

    rendered = [" ".join(cmd) for cmd in commands]
    assert any(all(token in cmd for token in required_tokens) for cmd in rendered)


def test_build_images_auto_detects_missing_l1() -> None:
    """Missing L1 layers trigger a rebuild of the whole stack."""
    project_id = "proj_build_l1miss"
    l0_image = base_dev_image("ubuntu:24.04")

    def l0_exists_only(image: str) -> bool:
        return image == l0_image

    with docker_project(project_id):
        generate_dockerfiles(project_id)
        commands = build_commands(project_id, image_exists_side_effect=l0_exists_only)

    assert len(commands) == 3
