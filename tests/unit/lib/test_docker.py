# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import Mock, patch

from terok_agent import ImageSet

from terok.lib.core.config import build_dir
from terok.lib.orchestration.docker import build_images, generate_dockerfiles
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


def _mock_base_images(base_image: str = "ubuntu:24.04") -> ImageSet:
    """Return a mock ImageSet matching the default base image."""
    from terok_agent.build import l0_image_tag, l1_image_tag

    return ImageSet(l0=l0_image_tag(base_image), l1=l1_image_tag(base_image))


def build_commands(
    project_id: str,
    *,
    image_exists: bool = True,
    image_exists_side_effect: Callable[[str], bool] | None = None,
    **build_kwargs: object,
) -> list[list[str]]:
    """Run ``build_images`` with Podman mocked and return captured L2 build commands.

    L0+L1 builds are mocked via ``build_base_images`` — only L2 podman
    commands are captured.
    """
    commands: list[list[str]] = []

    def mock_run(cmd: list[str], **_kwargs: object) -> Mock:
        if "podman" in cmd and "build" in cmd:
            commands.append(cmd)
        return Mock(returncode=0)

    image_exists_patch = (
        patch("terok.lib.orchestration.docker._image_exists", side_effect=image_exists_side_effect)
        if image_exists_side_effect is not None
        else patch("terok.lib.orchestration.docker._image_exists", return_value=image_exists)
    )
    with (
        patch("subprocess.run", side_effect=mock_run),
        patch("terok.lib.orchestration.docker._check_podman_available"),
        patch(
            "terok.lib.orchestration.docker.build_base_images",
            return_value=_mock_base_images(),
        ),
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
        out_dir = build_dir() / project_id

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
        assert "SSH_KEY_NAME" not in l2_content
        assert "{{DEFAULT_BRANCH}}" not in l2_content
        assert f'CODE_REPO="{UPSTREAM_URL}"' in l2_content
        assert any(path.is_file() for path in (out_dir / "scripts").iterdir())


def test_generate_dockerfiles_uses_gatekeeping_code_repo() -> None:
    """Gatekeeping projects clone from the local git gate instead of upstream."""
    with docker_project("proj_gated", security_class="gatekeeping"):
        generate_dockerfiles("proj_gated")
        content = (build_dir() / "proj_gated" / "L2.Dockerfile").read_text(encoding="utf-8")
        assert 'CODE_REPO="file:///git-gate/gate.git"' in content
        assert f'CODE_REPO="{UPSTREAM_URL}"' not in content


def test_l2_includes_user_snippet_inline() -> None:
    """L2 renders inline user docker snippet into the Dockerfile."""
    yaml = (
        "project:\n  id: proj_snippet\n"
        "git:\n  upstream_url: https://example.com/repo.git\n"
        "docker:\n  user_snippet_inline: RUN apt-get install -y fortran-compiler\n"
    )
    with project_env(yaml, project_id="proj_snippet"):
        generate_dockerfiles("proj_snippet")
        content = (build_dir() / "proj_snippet" / "L2.Dockerfile").read_text(encoding="utf-8")
        assert "RUN apt-get install -y fortran-compiler" in content


def test_l2_includes_user_snippet_from_file() -> None:
    """L2 renders user docker snippet from a file reference."""
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".dockerfile", delete=False) as f:
        f.write("RUN pip install numpy\n")
        snippet_path = f.name

    try:
        yaml = (
            "project:\n  id: proj_snippet_file\n"
            "git:\n  upstream_url: https://example.com/repo.git\n"
            f"docker:\n  user_snippet_file: {snippet_path}\n"
        )
        with project_env(yaml, project_id="proj_snippet_file"):
            generate_dockerfiles("proj_snippet_file")
            content = (build_dir() / "proj_snippet_file" / "L2.Dockerfile").read_text(
                encoding="utf-8"
            )
            assert "RUN pip install numpy" in content
    finally:
        Path(snippet_path).unlink(missing_ok=True)


def test_l2_missing_snippet_file_exits() -> None:
    """Missing user_snippet_file raises SystemExit."""
    import pytest

    yaml = (
        "project:\n  id: proj_bad_snippet\n"
        "git:\n  upstream_url: https://example.com/repo.git\n"
        "docker:\n  user_snippet_file: /nonexistent/snippet.dockerfile\n"
    )
    with project_env(yaml, project_id="proj_bad_snippet"):
        with pytest.raises(SystemExit, match="not found"):
            generate_dockerfiles("proj_bad_snippet")


def test_l1_cli_pipx_inject_has_env_vars() -> None:
    """The CLI image sets the expected pipx env vars and package installation lines."""
    with docker_project("proj_pipx_test"):
        generate_dockerfiles("proj_pipx_test")
        content = (build_dir() / "proj_pipx_test" / "L1.cli.Dockerfile").read_text(encoding="utf-8")
        assert "PIPX_HOME=/opt/pipx" in content
        assert "PIPX_BIN_DIR=/usr/local/bin" in content
        assert "pipx install mistral-vibe" in content
        assert "pipx inject mistral-vibe mistralai" in content


def test_build_images_builds_l2() -> None:
    """build_images always produces an L2 podman build command."""
    with docker_project("proj_build_l2"):
        generate_dockerfiles("proj_build_l2")
        commands = build_commands("proj_build_l2")

    # L0+L1 are delegated to terok-agent (mocked); only L2 commands appear
    assert len(commands) == 1
    assert commands[0][0] == "podman"
    l2_dockerfile = next(p for p in commands[0] if p.endswith("L2.Dockerfile"))
    assert "L2.Dockerfile" in l2_dockerfile


def test_build_images_include_dev_adds_second_l2() -> None:
    """include_dev produces two L2 commands: cli + dev."""
    with docker_project("proj_build_dev"):
        generate_dockerfiles("proj_build_dev")
        commands = build_commands("proj_build_dev", include_dev=True)

    assert len(commands) == 2
    targets = [" ".join(cmd) for cmd in commands]
    assert any(":l2-cli" in t for t in targets)
    assert any(":l2-dev" in t for t in targets)


def test_build_images_full_rebuild_passes_no_cache() -> None:
    """full_rebuild passes --no-cache to L2 build."""
    with docker_project("proj_build_full"):
        generate_dockerfiles("proj_build_full")
        commands = build_commands("proj_build_full", full_rebuild=True)

    assert any("--no-cache" in cmd for cmd in commands[0])
