# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from terok.lib.containers.project_state import get_project_state
from terok.lib.core.config import build_root, state_root
from terok.lib.core.projects import list_projects, load_project
from test_utils import project_env, write_project


class ProjectTests(unittest.TestCase):
    def test_load_project_gatekeeping_defaults(self) -> None:
        project_id = "proj1"
        yaml = f"""\
project:
  id: {project_id}
  security_class: gatekeeping
git:
  upstream_url: https://example.com/repo.git
"""
        with project_env(yaml, project_id=project_id):
            proj = load_project(project_id)
            self.assertEqual(proj.id, project_id)
            self.assertEqual(proj.security_class, "gatekeeping")
            self.assertEqual(proj.tasks_root, (state_root() / "tasks" / project_id).resolve())
            self.assertEqual(
                proj.gate_path, (state_root() / "gate" / f"{project_id}.git").resolve()
            )
            self.assertEqual(proj.staging_root, (build_root() / project_id).resolve())

    def test_list_projects_prefers_user(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            system_root = base / "system"
            user_root = base / "user"
            system_root.mkdir(parents=True, exist_ok=True)
            user_projects = user_root / "terok" / "projects"

            project_id = "proj2"
            write_project(
                system_root,
                project_id,
                f"""\nproject:\n  id: {project_id}\ngit:\n  upstream_url: https://system.example/repo.git\n""".lstrip(),
            )
            write_project(
                user_projects,
                project_id,
                f"""\nproject:\n  id: {project_id}\ngit:\n  upstream_url: https://user.example/repo.git\n""".lstrip(),
            )

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(system_root),
                    "XDG_CONFIG_HOME": str(user_root),
                },
            ):
                projects = list_projects()
                self.assertEqual(len(projects), 1)
                self.assertEqual(projects[0].upstream_url, "https://user.example/repo.git")
                self.assertEqual(projects[0].root, (user_projects / project_id).resolve())

    def test_load_project_malformed_yaml(self) -> None:
        """load_project raises SystemExit on malformed YAML (not an unhandled YAMLError)."""
        project_id = "bad-yaml"
        malformed = "project:\n  id: bad-yaml\n  foo: [invalid yaml\n"
        with project_env(malformed, project_id=project_id):
            with self.assertRaises(SystemExit) as ctx:
                load_project(project_id)
            self.assertIn("Failed to parse", str(ctx.exception))

    def test_list_projects_skips_malformed_yaml(self) -> None:
        """list_projects skips projects with malformed YAML instead of crashing."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_dir = base / "config"

            # One valid project
            write_project(
                config_dir,
                "good",
                "project:\n  id: good\ngit:\n  upstream_url: https://example.com/good.git\n",
            )
            # One malformed project
            write_project(config_dir, "bad", "project:\n  id: bad\n  foo: [invalid\n")

            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_dir), "XDG_CONFIG_HOME": str(base / "empty")},
            ):
                projects = list_projects()
                # The malformed project should be skipped, only the good one returned
                self.assertEqual(len(projects), 1)
                self.assertEqual(projects[0].id, "good")

    def test_get_project_state(self) -> None:
        project_id = "proj3"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
"""
        with project_env(yaml, project_id=project_id, with_config_file=True) as env:
            stage_dir = build_root() / project_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            for name in (
                "L0.Dockerfile",
                "L1.cli.Dockerfile",
                "L1.ui.Dockerfile",
                "L2.Dockerfile",
            ):
                (stage_dir / name).write_text("", encoding="utf-8")

            ssh_dir = env.envs_dir / f"_ssh-config-{project_id}"
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "config").write_text("", encoding="utf-8")

            gate_dir = state_root() / "gate" / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)

            with unittest.mock.patch(
                "terok.lib.containers.project_state.subprocess.run"
            ) as run_mock:
                run_mock.return_value.returncode = 0
                state = get_project_state(project_id, gate_commit_provider=lambda pid: None)

            self.assertEqual(
                state,
                {
                    "dockerfiles": True,
                    "dockerfiles_old": True,
                    "images": True,
                    "images_old": True,
                    "ssh": True,
                    "gate": True,
                    "gate_last_commit": None,
                },
            )
