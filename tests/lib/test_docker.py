# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

import unittest
import unittest.mock

from terok.lib.containers.docker import build_images, generate_dockerfiles
from terok.lib.core.config import build_root, set_experimental
from terok.lib.core.images import base_dev_image
from test_utils import project_env


class DockerTests(unittest.TestCase):
    def setUp(self) -> None:
        set_experimental(True)

    def tearDown(self) -> None:
        set_experimental(False)

    def test_generate_dockerfiles_outputs(self) -> None:
        project_id = "proj4"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            out_dir = build_root() / project_id
            l0 = out_dir / "L0.Dockerfile"
            l1_cli = out_dir / "L1.cli.Dockerfile"
            l1_ui = out_dir / "L1.ui.Dockerfile"
            l2 = out_dir / "L2.Dockerfile"

            self.assertTrue(l0.is_file())
            self.assertTrue(l1_cli.is_file())
            self.assertTrue(l1_ui.is_file())
            self.assertTrue(l2.is_file())

            l0_content = l0.read_text(encoding="utf-8")
            self.assertIn('LANG="en_US.UTF-8"', l0_content)
            self.assertIn('LC_ALL="en_US.UTF-8"', l0_content)
            self.assertIn('LANGUAGE="en_US:en"', l0_content)
            self.assertIn("locales", l0_content)
            self.assertIn("locale-gen en_US.UTF-8", l0_content)

            content = l2.read_text(encoding="utf-8")
            self.assertIn(f'SSH_KEY_NAME="id_ed25519_{project_id}"', content)
            self.assertNotIn("{{DEFAULT_BRANCH}}", content)

            scripts_dir = out_dir / "scripts"
            self.assertTrue(scripts_dir.is_dir())
            script_files = [p for p in scripts_dir.iterdir() if p.is_file()]
            self.assertTrue(script_files)

            # For online projects, CODE_REPO should default to upstream URL
            self.assertIn('CODE_REPO="https://example.com/repo.git"', content)

    def test_generate_dockerfiles_gatekeeping_code_repo(self) -> None:
        """For gatekeeping projects, CODE_REPO_DEFAULT should be the git-gate path."""
        project_id = "proj_gated"
        yaml = f"""\
project:
  id: {project_id}
  security_class: gatekeeping
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            out_dir = build_root() / project_id
            l2 = out_dir / "L2.Dockerfile"

            content = l2.read_text(encoding="utf-8")
            # For gatekeeping projects, CODE_REPO should default to git-gate
            self.assertIn('CODE_REPO="file:///git-gate/gate.git"', content)
            # Should NOT contain the real upstream URL as CODE_REPO
            self.assertNotIn('CODE_REPO="https://example.com/repo.git"', content)

    def test_l1_cli_pipx_inject_has_env_vars(self) -> None:
        """Verify that PIPX environment variables are set globally and pipx commands use them."""
        project_id = "proj_pipx_test"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            out_dir = build_root() / project_id
            l1_cli = out_dir / "L1.cli.Dockerfile"

            content = l1_cli.read_text(encoding="utf-8")
            # Verify that PIPX_HOME and PIPX_BIN_DIR are set as ENV variables
            self.assertIn("PIPX_HOME=/opt/pipx", content)
            self.assertIn("PIPX_BIN_DIR=/usr/local/bin", content)
            # Verify that pipx commands use these environment variables (no inline vars)
            self.assertIn("pipx install mistral-vibe", content)
            self.assertIn("pipx inject mistral-vibe mistralai", content)

    def _run_build(
        self,
        project_id: str,
        *,
        image_exists: bool = True,
        **build_kwargs: object,
    ) -> list[list[str]]:
        """Run build_images with mocked subprocess and return captured build commands."""
        build_commands: list[list[str]] = []

        def mock_run(cmd: list[str], **kwargs: object) -> unittest.mock.Mock:
            if isinstance(cmd, list) and "podman" in cmd and "build" in cmd:
                build_commands.append(cmd)
            result = unittest.mock.Mock()
            result.returncode = 0
            return result

        with (
            unittest.mock.patch("subprocess.run", side_effect=mock_run),
            unittest.mock.patch("terok.lib.containers.docker._check_podman_available"),
            unittest.mock.patch(
                "terok.lib.containers.docker._image_exists",
                return_value=image_exists,
            ),
        ):
            build_images(project_id, **build_kwargs)

        return build_commands

    def test_build_images_l2_only_when_base_exists(self) -> None:
        """Default build with existing L0/L1 should only build L2."""
        project_id = "proj_build_test"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            cmds = self._run_build(project_id, image_exists=True)

            self.assertEqual(len(cmds), 2)
            for cmd in cmds:
                self.assertIn("L2.Dockerfile", " ".join(cmd))

    def test_build_images_auto_detects_missing_base(self) -> None:
        """Default build without existing L0/L1 should auto-build all layers."""
        project_id = "proj_build_auto"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            cmds = self._run_build(project_id, image_exists=False)

            # Should build all 5 images: L0, L1-cli, L1-ui, L2-cli, L2-ui
            self.assertEqual(len(cmds), 5)
            self.assertIn("L0.Dockerfile", " ".join(cmds[0]))
            self.assertIn("L1.cli.Dockerfile", " ".join(cmds[1]))
            self.assertIn("L1.ui.Dockerfile", " ".join(cmds[2]))
            self.assertIn("L2.Dockerfile", " ".join(cmds[3]))
            self.assertIn("L2.Dockerfile", " ".join(cmds[4]))

    def test_build_images_rebuild_agents(self) -> None:
        """rebuild_agents=True should build all layers regardless of existence."""
        project_id = "proj_build_agents"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            cmds = self._run_build(project_id, image_exists=True, rebuild_agents=True)

            self.assertEqual(len(cmds), 5)
            self.assertIn("L0.Dockerfile", " ".join(cmds[0]))
            self.assertIn("L1.cli.Dockerfile", " ".join(cmds[1]))
            self.assertIn("AGENT_CACHE_BUST", " ".join(cmds[1]))
            self.assertIn("L1.ui.Dockerfile", " ".join(cmds[2]))
            self.assertIn("L2.Dockerfile", " ".join(cmds[3]))
            self.assertIn("L2.Dockerfile", " ".join(cmds[4]))

    def test_build_images_full_rebuild(self) -> None:
        """full_rebuild=True should build all layers with --no-cache."""
        project_id = "proj_build_full"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            cmds = self._run_build(project_id, image_exists=True, full_rebuild=True)

            self.assertEqual(len(cmds), 5)
            # L0 should have --no-cache and --pull=always
            l0_cmd = " ".join(cmds[0])
            self.assertIn("--no-cache", l0_cmd)
            self.assertIn("--pull=always", l0_cmd)
            # All other commands should have --no-cache
            for cmd in cmds[1:]:
                self.assertIn("--no-cache", " ".join(cmd))

    def test_build_images_auto_detect_l1_missing_only(self) -> None:
        """When L0 exists but L1 is missing, should build all layers."""
        project_id = "proj_build_l1miss"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)

            build_commands: list[list[str]] = []

            def mock_run(cmd: list[str], **kwargs: object) -> unittest.mock.Mock:
                if isinstance(cmd, list) and "podman" in cmd and "build" in cmd:
                    build_commands.append(cmd)
                result = unittest.mock.Mock()
                result.returncode = 0
                return result

            l0_image = base_dev_image("ubuntu:24.04")

            def l0_exists_only(image: str) -> bool:
                # L0 exists, but L1 images do not
                return image == l0_image

            with (
                unittest.mock.patch("subprocess.run", side_effect=mock_run),
                unittest.mock.patch("terok.lib.containers.docker._check_podman_available"),
                unittest.mock.patch(
                    "terok.lib.containers.docker._image_exists",
                    side_effect=l0_exists_only,
                ),
            ):
                build_images(project_id)

            # Should build all 5 images since L1 is missing
            self.assertEqual(len(build_commands), 5)

    def test_generate_dockerfiles_no_ui_without_experimental(self) -> None:
        """Without experimental, L1.ui.Dockerfile should not be generated."""
        set_experimental(False)
        project_id = "proj_no_ui"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            out_dir = build_root() / project_id
            self.assertTrue((out_dir / "L0.Dockerfile").is_file())
            self.assertTrue((out_dir / "L1.cli.Dockerfile").is_file())
            self.assertFalse((out_dir / "L1.ui.Dockerfile").is_file())
            self.assertTrue((out_dir / "L2.Dockerfile").is_file())

    def test_build_images_skips_web_without_experimental(self) -> None:
        """Without experimental, build_images should only build CLI images (no web)."""
        set_experimental(False)
        project_id = "proj_build_noweb"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            generate_dockerfiles(project_id)
            cmds = self._run_build(project_id, image_exists=True)

            # Should only build 1 L2 image (CLI, no web)
            self.assertEqual(len(cmds), 1)
            self.assertIn("L2.Dockerfile", " ".join(cmds[0]))
