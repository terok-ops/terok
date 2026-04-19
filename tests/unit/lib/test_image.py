# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from unittest.mock import Mock, patch

from terok_executor import ImageSet

from terok.lib.core.config import build_dir
from terok.lib.orchestration.image import build_images, generate_dockerfiles
from tests.test_utils import mock_git_config, project_env

UPSTREAM_URL = "https://example.com/repo.git"
DEFAULT_BRANCH = "main"


@contextmanager
def image_project(project_id: str, *, security_class: str = "online") -> Iterator[object]:
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
    from terok_executor.container.build import l0_image_tag, l1_image_tag

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
        patch("terok.lib.orchestration.image._image_exists", side_effect=image_exists_side_effect)
        if image_exists_side_effect is not None
        else patch("terok.lib.orchestration.image._image_exists", return_value=image_exists)
    )
    with (
        patch("subprocess.run", side_effect=mock_run),
        patch(
            "terok.lib.orchestration.image.build_base_images",
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
    with image_project(project_id):
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
    with image_project("proj_gated", security_class="gatekeeping"):
        generate_dockerfiles("proj_gated")
        content = (build_dir() / "proj_gated" / "L2.Dockerfile").read_text(encoding="utf-8")
        assert 'CODE_REPO="file:///git-gate/gate.git"' in content
        assert f'CODE_REPO="{UPSTREAM_URL}"' not in content


def test_l2_includes_user_snippet_inline() -> None:
    """L2 renders inline user image snippet into the Dockerfile."""
    yaml = (
        "project:\n  id: proj_snippet\n"
        "git:\n  upstream_url: https://example.com/repo.git\n"
        "image:\n  user_snippet_inline: RUN apt-get install -y fortran-compiler\n"
    )
    with project_env(yaml, project_id="proj_snippet"):
        generate_dockerfiles("proj_snippet")
        content = (build_dir() / "proj_snippet" / "L2.Dockerfile").read_text(encoding="utf-8")
        assert "RUN apt-get install -y fortran-compiler" in content


def test_l2_includes_user_snippet_from_file() -> None:
    """L2 renders user image snippet from a file reference."""
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".dockerfile", delete=False) as f:
        f.write("RUN pip install numpy\n")
        snippet_path = f.name

    try:
        yaml = (
            "project:\n  id: proj_snippet_file\n"
            "git:\n  upstream_url: https://example.com/repo.git\n"
            f"image:\n  user_snippet_file: {snippet_path}\n"
        )
        with project_env(yaml, project_id="proj_snippet_file"):
            generate_dockerfiles("proj_snippet_file")
            content = (build_dir() / "proj_snippet_file" / "L2.Dockerfile").read_text(
                encoding="utf-8"
            )
            assert "RUN pip install numpy" in content
    finally:
        Path(snippet_path).unlink(missing_ok=True)


def test_l2_combines_snippet_file_and_inline() -> None:
    """L2 includes both snippet file and inline, file first."""
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(mode="w", suffix=".dockerfile", delete=False) as f:
        f.write("RUN pip install numpy\n")
        snippet_path = f.name

    try:
        yaml = (
            "project:\n  id: proj_both_snippets\n"
            "git:\n  upstream_url: https://example.com/repo.git\n"
            f"image:\n  user_snippet_file: {snippet_path}\n"
            "  user_snippet_inline: RUN apt-get install -y fortran-compiler\n"
        )
        with project_env(yaml, project_id="proj_both_snippets"):
            generate_dockerfiles("proj_both_snippets")
            content = (build_dir() / "proj_both_snippets" / "L2.Dockerfile").read_text(
                encoding="utf-8"
            )
            assert "RUN pip install numpy" in content
            assert "RUN apt-get install -y fortran-compiler" in content
            # File comes before inline
            assert content.index("pip install numpy") < content.index("fortran-compiler")
    finally:
        Path(snippet_path).unlink(missing_ok=True)


def test_l2_missing_snippet_file_exits() -> None:
    """Missing user_snippet_file raises SystemExit."""
    import pytest

    yaml = (
        "project:\n  id: proj_bad_snippet\n"
        "git:\n  upstream_url: https://example.com/repo.git\n"
        "image:\n  user_snippet_file: /nonexistent/snippet.dockerfile\n"
    )
    with project_env(yaml, project_id="proj_bad_snippet"):
        with pytest.raises(SystemExit, match="not found"):
            generate_dockerfiles("proj_bad_snippet")


def test_l1_cli_pipx_inject_has_env_vars() -> None:
    """The CLI image sets the expected pipx env vars and package installation lines."""
    with image_project("proj_pipx_test"):
        generate_dockerfiles("proj_pipx_test")
        content = (build_dir() / "proj_pipx_test" / "L1.cli.Dockerfile").read_text(encoding="utf-8")
        assert "PIPX_HOME=/opt/pipx" in content
        assert "PIPX_BIN_DIR=/usr/local/bin" in content
        assert "pipx install mistral-vibe" in content
        assert "pipx inject mistral-vibe mistralai" in content


def test_build_images_builds_l2() -> None:
    """build_images always produces an L2 podman build command."""
    with image_project("proj_build_l2"):
        generate_dockerfiles("proj_build_l2")
        commands = build_commands("proj_build_l2")

    # L0+L1 are delegated to terok-executor (mocked); only L2 commands appear
    assert len(commands) == 1
    assert commands[0][0] == "podman"
    l2_dockerfile = next(p for p in commands[0] if p.endswith("L2.Dockerfile"))
    assert "L2.Dockerfile" in l2_dockerfile


def test_build_images_include_dev_adds_second_l2() -> None:
    """include_dev produces two L2 commands: cli + dev."""
    with image_project("proj_build_dev"):
        generate_dockerfiles("proj_build_dev")
        commands = build_commands("proj_build_dev", include_dev=True)

    assert len(commands) == 2
    targets = [" ".join(cmd) for cmd in commands]
    assert any(":l2-cli" in t for t in targets)
    assert any(":l2-dev" in t for t in targets)


def test_build_images_full_rebuild_passes_no_cache() -> None:
    """full_rebuild passes --no-cache to L2 build."""
    with image_project("proj_build_full"):
        generate_dockerfiles("proj_build_full")
        commands = build_commands("proj_build_full", full_rebuild=True)

    assert any("--no-cache" in cmd for cmd in commands[0])


# ---------- Per-layer hashing ----------


class TestPerLayerHashes:
    """Verify per-layer content hashes are stable and independent."""

    def test_per_layer_hashes_are_deterministic(self) -> None:
        """Same inputs produce the same per-layer hashes."""
        from terok.lib.orchestration.image import (
            l0_content_hash,
            l1_content_hash,
            l2_content_hash,
        )

        rendered = {
            "L0.Dockerfile": "FROM ubuntu:24.04",
            "L1.cli.Dockerfile": "FROM terok-l0:ubuntu-24-04",
            "L2.Dockerfile": "FROM terok-l1-cli:ubuntu-24-04",
        }
        assert l0_content_hash("ubuntu:24.04", rendered) == l0_content_hash(
            "ubuntu:24.04", rendered
        )
        assert l1_content_hash(rendered) == l1_content_hash(rendered)
        assert l2_content_hash(rendered) == l2_content_hash(rendered)

    def test_l0_changes_dont_affect_l1_hash(self) -> None:
        """Changing L0 Dockerfile does not change L1 hash."""
        from terok.lib.orchestration.image import l0_content_hash, l1_content_hash

        rendered_a = {
            "L0.Dockerfile": "FROM ubuntu:24.04",
            "L1.cli.Dockerfile": "FROM l0-tag",
            "L2.Dockerfile": "FROM l1-tag",
        }
        rendered_b = {**rendered_a, "L0.Dockerfile": "FROM ubuntu:26.04"}

        assert l0_content_hash("ubuntu:24.04", rendered_a) != l0_content_hash(
            "ubuntu:26.04", rendered_b
        )
        assert l1_content_hash(rendered_a) == l1_content_hash(rendered_b)

    def test_l2_changes_dont_affect_l0_or_l1_hash(self) -> None:
        """Changing L2 Dockerfile does not change L0 or L1 hashes."""
        from terok.lib.orchestration.image import l0_content_hash, l1_content_hash

        rendered_a = {
            "L0.Dockerfile": "FROM ubuntu:24.04",
            "L1.cli.Dockerfile": "FROM l0-tag",
            "L2.Dockerfile": "FROM l1-tag",
        }
        rendered_b = {**rendered_a, "L2.Dockerfile": "FROM l1-tag\nRUN echo added"}

        assert l0_content_hash("ubuntu:24.04", rendered_a) == l0_content_hash(
            "ubuntu:24.04", rendered_b
        )
        assert l1_content_hash(rendered_a) == l1_content_hash(rendered_b)

    def test_l1_dockerfile_change_changes_l1_hash(self) -> None:
        """Changing L1 Dockerfile content changes the L1 hash."""
        from terok.lib.orchestration.image import l1_content_hash

        rendered_a = {
            "L0.Dockerfile": "FROM ubuntu:24.04",
            "L1.cli.Dockerfile": "FROM l0-tag\nRUN install agents",
            "L2.Dockerfile": "FROM l1-tag",
        }
        rendered_b = {**rendered_a, "L1.cli.Dockerfile": "FROM l0-tag\nRUN install NEW agents"}

        assert l1_content_hash(rendered_a) != l1_content_hash(rendered_b)

    def test_base_image_change_changes_l0_hash(self) -> None:
        """Changing base_image string changes L0 hash."""
        from terok.lib.orchestration.image import l0_content_hash

        rendered = {
            "L0.Dockerfile": "FROM base",
            "L1.cli.Dockerfile": "x",
            "L2.Dockerfile": "y",
        }
        assert l0_content_hash("ubuntu:24.04", rendered) != l0_content_hash(
            "ubuntu:26.04", rendered
        )

    def test_combined_hash_derives_from_per_layer(self) -> None:
        """Combined hash changes when any per-layer hash changes."""
        from terok.lib.orchestration.image import (
            build_context_hash_from_rendered,
        )

        with image_project("proj_hash_comb"):
            from terok.lib.core.projects import load_project
            from terok.lib.orchestration.image import render_all_dockerfiles

            project = load_project("proj_hash_comb")
            rendered = render_all_dockerfiles(project)
            h1 = build_context_hash_from_rendered(project, rendered)

            # Tweak L2 only
            rendered["L2.Dockerfile"] += "\nRUN echo hello"
            h2 = build_context_hash_from_rendered(project, rendered)
            assert h1 != h2


# ---------- Package family (deb/rpm) plumbing ----------


@contextmanager
def image_project_with(
    project_id: str,
    *,
    base_image: str = "ubuntu:24.04",
    family: str | None = None,
) -> Iterator[object]:
    """Variant of :func:`image_project` that sets ``image.base_image`` (and ``image.family``)."""
    lines = [
        f"project:\n  id: {project_id}\n",
        "git:\n",
        f"  upstream_url: {UPSTREAM_URL}\n",
        f"  default_branch: {DEFAULT_BRANCH}\n",
        "image:\n",
        f"  base_image: {base_image}\n",
    ]
    if family is not None:
        lines.append(f"  family: {family}\n")
    with project_env("".join(lines), project_id=project_id) as env:
        yield env


class TestPackageFamily:
    """Verify that the family field flows from project.yml to L0/L1 rendering."""

    def test_fedora_renders_dnf_dockerfiles(self) -> None:
        """A fedora base image yields dnf-flavoured L0/L1 (no apt-get)."""
        from terok.lib.core.projects import load_project
        from terok.lib.orchestration.image import render_all_dockerfiles

        with image_project_with("proj_fedora", base_image="fedora:43"):
            project = load_project("proj_fedora")
            rendered = render_all_dockerfiles(project)

        assert "dnf install" in rendered["L0.Dockerfile"]
        assert "apt-get" not in rendered["L0.Dockerfile"]
        assert "dnf install" in rendered["L1.cli.Dockerfile"]
        assert "apt-get" not in rendered["L1.cli.Dockerfile"]

    def test_explicit_family_unblocks_unknown_image(self) -> None:
        """Setting image.family lets unknown images through detection."""
        from terok.lib.core.projects import load_project
        from terok.lib.orchestration.image import render_all_dockerfiles

        with image_project_with("proj_rocky", base_image="rockylinux:9", family="rpm"):
            project = load_project("proj_rocky")
            rendered = render_all_dockerfiles(project)

        assert "dnf install" in rendered["L0.Dockerfile"]

    def test_unknown_image_without_override_raises(self) -> None:
        """Unknown image with no family override raises BuildError."""
        import pytest
        from terok_executor import BuildError

        from terok.lib.core.projects import load_project
        from terok.lib.orchestration.image import render_all_dockerfiles

        with image_project_with("proj_unknown", base_image="rockylinux:9"):
            project = load_project("proj_unknown")
            with pytest.raises(BuildError, match="Cannot infer package family"):
                render_all_dockerfiles(project)

    def test_build_images_forwards_family_to_executor(self) -> None:
        """build_images passes project.family through to build_base_images."""
        with image_project_with("proj_rocky", base_image="rockylinux:9", family="rpm"):
            with (
                patch("subprocess.run", return_value=Mock(returncode=0)),
                patch("terok.lib.orchestration.image._image_exists", return_value=True),
                patch(
                    "terok.lib.orchestration.image.build_base_images",
                    return_value=_mock_base_images("rockylinux:9"),
                ) as mock_build,
                mock_git_config(),
            ):
                build_images("proj_rocky")

            mock_build.assert_called_once()
            assert mock_build.call_args.kwargs["family"] == "rpm"

    def test_build_images_forwards_none_family_when_unset(self) -> None:
        """An absent project.family flows through as ``None`` (auto-detect)."""
        with image_project_with("proj_ubuntu", base_image="ubuntu:24.04"):
            with (
                patch("subprocess.run", return_value=Mock(returncode=0)),
                patch("terok.lib.orchestration.image._image_exists", return_value=True),
                patch(
                    "terok.lib.orchestration.image.build_base_images",
                    return_value=_mock_base_images(),
                ) as mock_build,
                mock_git_config(),
            ):
                build_images("proj_ubuntu")

            assert mock_build.call_args.kwargs["family"] is None


# ---------- Build manifest ----------


class TestBuildManifest:
    """Verify manifest read/write round-tripping."""

    def test_write_and_read_manifest(self) -> None:
        """Manifest round-trips through JSON on disk."""
        from terok.lib.orchestration.image import (
            _write_build_manifest,
            read_build_manifest,
        )

        manifest = {
            "schema": 1,
            "base_image": "ubuntu:24.04",
            "l0": {"tag": "terok-l0:ubuntu-24-04", "content_hash": "aaa"},
            "l1": {"tag": "terok-l1-cli:ubuntu-24-04", "content_hash": "bbb"},
            "l2_cli": {"tag": "test:l2-cli", "content_hash": "ccc"},
            "combined_hash": "ddd",
        }
        with image_project("proj_manifest"):
            generate_dockerfiles("proj_manifest")
            _write_build_manifest("proj_manifest", manifest)
            loaded = read_build_manifest("proj_manifest")

        assert loaded == manifest

    def test_missing_manifest_returns_none(self) -> None:
        """read_build_manifest returns None when no manifest exists."""
        from terok.lib.orchestration.image import read_build_manifest

        with image_project("proj_no_manifest"):
            assert read_build_manifest("proj_no_manifest") is None

    def test_corrupt_manifest_returns_none(self) -> None:
        """Corrupt JSON manifest returns None."""
        from terok.lib.orchestration.image import _manifest_path, read_build_manifest

        with image_project("proj_corrupt"):
            generate_dockerfiles("proj_corrupt")
            path = _manifest_path("proj_corrupt")
            path.write_text("not json", encoding="utf-8")
            assert read_build_manifest("proj_corrupt") is None

    def test_wrong_schema_version_returns_none(self) -> None:
        """Manifest with wrong schema version returns None."""
        import json

        from terok.lib.orchestration.image import _manifest_path, read_build_manifest

        with image_project("proj_bad_schema"):
            generate_dockerfiles("proj_bad_schema")
            path = _manifest_path("proj_bad_schema")
            path.write_text(json.dumps({"schema": 999, "l0": {}}), encoding="utf-8")
            assert read_build_manifest("proj_bad_schema") is None

    def test_build_images_writes_manifest(self) -> None:
        """build_images writes a build manifest after successful build."""
        from terok.lib.orchestration.image import read_build_manifest

        with image_project("proj_build_manifest"):
            generate_dockerfiles("proj_build_manifest")
            build_commands("proj_build_manifest", refresh_agents=True)
            manifest = read_build_manifest("proj_build_manifest")

        assert manifest is not None
        assert manifest["schema"] == 1
        assert "l0" in manifest and "l1" in manifest and "l2_cli" in manifest
        assert all(manifest[k]["content_hash"] for k in ("l0", "l1"))

    def test_refresh_agents_writes_fresh_hashes(self) -> None:
        """refresh_agents=True records current hashes, not carried-forward ones."""
        from terok.lib.orchestration.image import (
            _write_build_manifest,
            read_build_manifest,
        )

        with image_project("proj_rebuild"):
            generate_dockerfiles("proj_rebuild")
            # Seed a manifest with stale hashes
            _write_build_manifest(
                "proj_rebuild",
                {
                    "schema": 1,
                    "base_image": "ubuntu:24.04",
                    "l0": {"tag": "terok-l0:ubuntu-24-04", "content_hash": "old-l0"},
                    "l1": {"tag": "terok-l1-cli:ubuntu-24-04", "content_hash": "old-l1"},
                    "l2_cli": {"tag": "proj_rebuild:l2-cli", "content_hash": "old-l2"},
                    "combined_hash": "old-combined",
                },
            )
            # Build with --agents → L0/L1 rebuilt → fresh hashes
            build_commands("proj_rebuild", refresh_agents=True)
            manifest = read_build_manifest("proj_rebuild")

        assert manifest is not None
        # Hashes must NOT be the old carried-forward values
        assert manifest["l0"]["content_hash"] != "old-l0"
        assert manifest["l1"]["content_hash"] != "old-l1"

    def test_skipped_base_carries_forward_manifest_hashes(self) -> None:
        """When L0/L1 are skipped, manifest preserves previous hashes."""
        from terok.lib.orchestration.image import (
            _write_build_manifest,
            read_build_manifest,
        )

        with image_project("proj_skip"):
            generate_dockerfiles("proj_skip")
            # Simulate a previous build with known L0/L1 hashes
            _write_build_manifest(
                "proj_skip",
                {
                    "schema": 1,
                    "base_image": "ubuntu:24.04",
                    "l0": {"tag": "terok-l0:ubuntu-24-04", "content_hash": "old-l0"},
                    "l1": {"tag": "terok-l1-cli:ubuntu-24-04", "content_hash": "old-l1"},
                    "l2_cli": {"tag": "proj_skip:l2-cli", "content_hash": "old-l2"},
                    "combined_hash": "old-combined",
                },
            )
            # Default build (refresh_agents=False) → L0/L1 skipped
            build_commands("proj_skip")
            manifest = read_build_manifest("proj_skip")

        assert manifest is not None
        # L0/L1 hashes carried forward from previous manifest
        assert manifest["l0"]["content_hash"] == "old-l0"
        assert manifest["l1"]["content_hash"] == "old-l1"
        # L2 hash is freshly computed (not "old-l2")
        assert manifest["l2_cli"]["content_hash"] != "old-l2"
