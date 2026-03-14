# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import Mock, patch

import pytest

from terok.lib.containers.docker import build_images, generate_dockerfiles
from terok.lib.core.config import build_root, set_experimental
from terok.lib.core.images import base_dev_image
from test_utils import mock_git_config, project_env

UPSTREAM_URL = "https://example.com/repo.git"
DEFAULT_BRANCH = "main"


@pytest.fixture(autouse=True)
def experimental_enabled() -> Iterator[None]:
    """Run docker tests with experimental mode enabled unless overridden in the test."""
    set_experimental(True)
    yield
    set_experimental(False)


@contextmanager
def docker_project(project_id: str, *, security_class: str = "online") -> Iterator[object]:
    """Create a minimal project config suitable for Dockerfile generation tests."""
    yaml = f"""\
project:
  id: {project_id}
"""
    if security_class != "online":
        yaml += f"  security_class: {security_class}\n"
    yaml += f"""git:
  upstream_url: {UPSTREAM_URL}
  default_branch: {DEFAULT_BRANCH}
"""
    with project_env(yaml, project_id=project_id) as env:
        yield env


def _capture_build_commands(
    project_id: str,
    *,
    image_exists: bool = True,
    image_exists_side_effect=None,
    **build_kwargs: object,
) -> list[list[str]]:
    """Run ``build_images`` with podman mocked and return captured build commands."""
    commands: list[list[str]] = []

    def mock_run(cmd: list[str], **_kwargs: object) -> Mock:
        if isinstance(cmd, list) and "podman" in cmd and "build" in cmd:
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
    ):
        build_images(project_id, **build_kwargs)

    return commands


def test_generate_dockerfiles_outputs_expected_files_and_content() -> None:
    project_id = "proj4"
    with docker_project(project_id):
        generate_dockerfiles(project_id)
        out_dir = build_root() / project_id

        assert (out_dir / "L0.Dockerfile").is_file()
        assert (out_dir / "L1.cli.Dockerfile").is_file()
        assert (out_dir / "L1.ui.Dockerfile").is_file()
        assert (out_dir / "L2.Dockerfile").is_file()

        l0_content = (out_dir / "L0.Dockerfile").read_text(encoding="utf-8")
        for expected in (
            'LANG="en_US.UTF-8"',
            'LC_ALL="en_US.UTF-8"',
            'LANGUAGE="en_US:en"',
            "locales",
            "locale-gen en_US.UTF-8",
        ):
            assert expected in l0_content

        l2_content = (out_dir / "L2.Dockerfile").read_text(encoding="utf-8")
        assert f'SSH_KEY_NAME="id_ed25519_{project_id}"' in l2_content
        assert "{{DEFAULT_BRANCH}}" not in l2_content
        assert f'CODE_REPO="{UPSTREAM_URL}"' in l2_content

        scripts_dir = out_dir / "scripts"
        assert scripts_dir.is_dir()
        assert any(path.is_file() for path in scripts_dir.iterdir())


def test_generate_dockerfiles_uses_gatekeeping_code_repo() -> None:
    with docker_project("proj_gated", security_class="gatekeeping"):
        generate_dockerfiles("proj_gated")
        content = (build_root() / "proj_gated" / "L2.Dockerfile").read_text(encoding="utf-8")

    assert 'CODE_REPO="file:///git-gate/gate.git"' in content
    assert f'CODE_REPO="{UPSTREAM_URL}"' not in content


def test_l1_cli_pipx_inject_has_env_vars() -> None:
    with docker_project("proj_pipx_test"):
        generate_dockerfiles("proj_pipx_test")
        content = (build_root() / "proj_pipx_test" / "L1.cli.Dockerfile").read_text(
            encoding="utf-8"
        )

    assert "PIPX_HOME=/opt/pipx" in content
    assert "PIPX_BIN_DIR=/usr/local/bin" in content
    assert "pipx install mistral-vibe" in content
    assert "pipx inject mistral-vibe mistralai" in content


def test_build_images_l2_only_when_base_exists() -> None:
    with docker_project("proj_build_test"):
        generate_dockerfiles("proj_build_test")
        commands = _capture_build_commands("proj_build_test", image_exists=True)

    assert len(commands) == 2
    assert all("L2.Dockerfile" in " ".join(cmd) for cmd in commands)


def test_build_images_auto_detects_missing_base() -> None:
    with docker_project("proj_build_auto"):
        generate_dockerfiles("proj_build_auto")
        commands = _capture_build_commands("proj_build_auto", image_exists=False)
        out_dir = build_root() / "proj_build_auto"

    assert len(commands) == 5
    assert [next(part for part in cmd if part.endswith(".Dockerfile")) for cmd in commands] == [
        str(out_dir / name)
        for name in (
            "L0.Dockerfile",
            "L1.cli.Dockerfile",
            "L1.ui.Dockerfile",
            "L2.Dockerfile",
            "L2.Dockerfile",
        )
    ]


def test_build_images_rebuild_agents_builds_all_layers() -> None:
    with docker_project("proj_build_agents"):
        generate_dockerfiles("proj_build_agents")
        commands = _capture_build_commands(
            "proj_build_agents",
            image_exists=True,
            rebuild_agents=True,
        )

    assert len(commands) == 5
    assert "AGENT_CACHE_BUST" in " ".join(commands[1])


@pytest.mark.parametrize(
    ("build_kwargs", "required_tokens"),
    [
        pytest.param({"full_rebuild": True}, ["--no-cache", "--pull=always"], id="full"),
        pytest.param({"include_dev": True}, [":l2-dev"], id="include-dev"),
    ],
)
def test_build_images_applies_expected_flags(
    build_kwargs: dict[str, object],
    required_tokens: list[str],
) -> None:
    with docker_project("proj_build_flags"):
        generate_dockerfiles("proj_build_flags")
        commands = _capture_build_commands("proj_build_flags", image_exists=True, **build_kwargs)

    rendered = [" ".join(cmd) for cmd in commands]
    for token in required_tokens:
        assert any(token in cmd for cmd in rendered)


def test_build_images_auto_detects_missing_l1() -> None:
    project_id = "proj_build_l1miss"
    l0_image = base_dev_image("ubuntu:24.04")

    def l0_exists_only(image: str) -> bool:
        return image == l0_image

    with docker_project(project_id):
        generate_dockerfiles(project_id)
        commands = _capture_build_commands(
            project_id,
            image_exists_side_effect=l0_exists_only,
        )

    assert len(commands) == 5


def test_generate_dockerfiles_skips_ui_without_experimental() -> None:
    set_experimental(False)
    with docker_project("proj_no_ui"):
        generate_dockerfiles("proj_no_ui")
        out_dir = build_root() / "proj_no_ui"
        assert (out_dir / "L0.Dockerfile").is_file()
        assert (out_dir / "L1.cli.Dockerfile").is_file()
        assert not (out_dir / "L1.ui.Dockerfile").exists()
        assert (out_dir / "L2.Dockerfile").is_file()


def test_build_images_skips_web_without_experimental() -> None:
    set_experimental(False)
    with docker_project("proj_build_noweb"):
        generate_dockerfiles("proj_build_noweb")
        commands = _capture_build_commands("proj_build_noweb", image_exists=True)

    assert len(commands) == 1
    assert "L2.Dockerfile" in " ".join(commands[0])
