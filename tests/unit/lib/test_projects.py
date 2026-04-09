# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project loading and listing helpers."""

from __future__ import annotations

import os
import tempfile
import unittest.mock
from pathlib import Path

import pytest

from terok.lib.core.config import build_dir, make_sandbox_config, sandbox_live_dir
from terok.lib.core.projects import list_projects, load_project
from terok.lib.domain.project_state import get_project_state
from tests.test_utils import project_env, write_project


def project_yaml(
    project_id: str,
    *,
    security_class: str | None = None,
    authorship: str | None = None,
    shield_drop_on_task_run: bool | None = None,
    shield_on_task_restart: str | None = None,
) -> str:
    """Build project YAML for tests with optional sections."""
    lines = ["project:", f"  id: {project_id}"]
    if security_class is not None:
        lines.append(f"  security_class: {security_class}")
    lines += ["git:", "  upstream_url: https://example.com/repo.git"]
    if authorship is not None:
        lines.append(f"  authorship: {authorship}")
    shield_lines: list[str] = []
    if shield_drop_on_task_run is not None:
        shield_lines.append(f"  drop_on_task_run: {str(shield_drop_on_task_run).lower()}")
    if shield_on_task_restart is not None:
        shield_lines.append(f"  on_task_restart: {shield_on_task_restart}")
    if shield_lines:
        lines += ["shield:", *shield_lines]
    return "\n".join(lines) + "\n"


class TestProject:
    """Tests for project loading/listing."""

    def test_load_project_gatekeeping_defaults(self) -> None:
        project_id = "proj1"
        with project_env(
            project_yaml(project_id, security_class="gatekeeping"),
            project_id=project_id,
        ):
            project = load_project(project_id)
            assert project.id == project_id
            assert project.security_class == "gatekeeping"
            assert project.tasks_root == (sandbox_live_dir() / "tasks" / project_id).resolve()
            assert (
                project.gate_path
                == (make_sandbox_config().gate_base_path / f"{project_id}.git").resolve()
            )
            assert project.staging_root == (build_dir() / project_id).resolve()
            assert project.git_authorship == "agent-human"

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "config_text", "expected"),
        [
            (
                "proj-authorship",
                project_yaml("proj-authorship", authorship="human-agent"),
                None,
                "human-agent",
            ),
            (
                "proj-global-authorship",
                project_yaml("proj-global-authorship"),
                "git:\n  authorship: human\n",
                "human",
            ),
        ],
        ids=["project-authorship", "global-authorship"],
    )
    def test_git_authorship_resolution(
        self,
        project_id: str,
        yaml_text: str,
        config_text: str | None,
        expected: str,
    ) -> None:
        with project_env(yaml_text, project_id=project_id) as ctx:
            if config_text is None:
                project = load_project(project_id)
            else:
                config_file = ctx.base / "config.yml"
                config_file.write_text(config_text, encoding="utf-8")
                with unittest.mock.patch.dict(os.environ, {"TEROK_CONFIG_FILE": str(config_file)}):
                    project = load_project(project_id)
        assert project.git_authorship == expected

    def test_load_project_invalid_git_authorship_raises(self) -> None:
        with project_env(
            project_yaml("proj-bad-authorship", authorship="mystery-mode"),
            project_id="proj-bad-authorship",
        ):
            with pytest.raises(SystemExit, match="git.authorship"):
                load_project("proj-bad-authorship")

    def test_list_projects_prefers_user(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            system_config = base / "system"
            system_projects = system_config / "projects"
            user_projects = base / "user" / "terok" / "projects"
            system_projects.mkdir(parents=True, exist_ok=True)
            user_projects.mkdir(parents=True, exist_ok=True)

            write_project(
                system_projects,
                "proj2",
                project_yaml("proj2").replace("example.com", "system.example"),
            )
            write_project(
                user_projects, "proj2", project_yaml("proj2").replace("example.com", "user.example")
            )

            with unittest.mock.patch.dict(
                os.environ,
                {
                    "TEROK_CONFIG_DIR": str(system_config),
                    "XDG_CONFIG_HOME": str(base / "user"),
                },
            ):
                projects = list_projects()
        assert len(projects) == 1
        assert projects[0].upstream_url == "https://user.example/repo.git"
        assert projects[0].root == (user_projects / "proj2").resolve()

    def test_list_projects_skips_malformed_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            config_base = base / "config"
            projects_root = config_base / "projects"
            write_project(
                projects_root,
                "good",
                "project:\n  id: good\ngit:\n  upstream_url: https://example.com/good.git\n",
            )
            write_project(projects_root, "bad", "project:\n  id: bad\n  foo: [invalid\n")
            with unittest.mock.patch.dict(
                os.environ,
                {"TEROK_CONFIG_DIR": str(config_base), "XDG_CONFIG_HOME": str(base / "empty")},
            ):
                projects = list_projects()
        assert len(projects) == 1
        assert projects[0].id == "good"

    def test_load_project_malformed_yaml(self) -> None:
        malformed = "project:\n  id: bad-yaml\n  foo: [invalid yaml\n"
        with project_env(malformed, project_id="bad-yaml"):
            with pytest.raises(SystemExit, match="Failed to read"):
                load_project("bad-yaml")

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "expected"),
        [
            ("proj-shield-default", project_yaml("proj-shield-default"), True),
            (
                "proj-shield-drop",
                project_yaml("proj-shield-drop", shield_drop_on_task_run=True),
                True,
            ),
            (
                "proj-shield-no-drop",
                project_yaml("proj-shield-no-drop", shield_drop_on_task_run=False),
                False,
            ),
        ],
        ids=["default", "enabled", "disabled"],
    )
    def test_shield_drop_on_task_run(
        self,
        project_id: str,
        yaml_text: str,
        expected: bool,
    ) -> None:
        """Project-level drop_on_task_run overrides global default."""
        with project_env(yaml_text, project_id=project_id):
            assert load_project(project_id).shield_drop_on_task_run is expected

    @pytest.mark.parametrize(
        ("project_id", "yaml_text", "expected"),
        [
            ("proj-restart-default", project_yaml("proj-restart-default"), "retain"),
            (
                "proj-restart-up",
                project_yaml("proj-restart-up", shield_on_task_restart="up"),
                "up",
            ),
        ],
        ids=["default-retain", "explicit-up"],
    )
    def test_shield_on_task_restart(
        self,
        project_id: str,
        yaml_text: str,
        expected: str,
    ) -> None:
        """Project-level on_task_restart overrides global default."""
        with project_env(yaml_text, project_id=project_id):
            assert load_project(project_id).shield_on_task_restart == expected

    def test_shared_dir_true_resolves_to_tasks_root(self) -> None:
        """``shared_dir: true`` resolves to tasks_root/_shared."""
        yaml_text = project_yaml("proj-shared") + "shared_dir: true\n"
        with project_env(yaml_text, project_id="proj-shared"):
            project = load_project("proj-shared")
        assert project.shared_dir is not None
        assert project.shared_dir.name == "_shared"
        assert project.shared_dir.parent == project.tasks_root

    def test_shared_dir_path_resolves_absolute(self) -> None:
        """``shared_dir: /path`` resolves to an absolute Path."""
        yaml_text = project_yaml("proj-shared-path") + "shared_dir: /tmp/terok-testing/custom\n"
        with project_env(yaml_text, project_id="proj-shared-path"):
            project = load_project("proj-shared-path")
        assert project.shared_dir == Path("/tmp/terok-testing/custom")

    def test_shared_dir_relative_path_rejected(self) -> None:
        """Relative path in shared_dir raises SystemExit."""
        yaml_text = project_yaml("proj-shared-rel") + "shared_dir: relative/path\n"
        with project_env(yaml_text, project_id="proj-shared-rel"):
            with pytest.raises(SystemExit, match="absolute path"):
                load_project("proj-shared-rel")

    def test_shared_dir_omitted_is_none(self) -> None:
        """Omitting ``shared_dir`` leaves it None (disabled)."""
        with project_env(project_yaml("proj-no-shared"), project_id="proj-no-shared"):
            project = load_project("proj-no-shared")
        assert project.shared_dir is None

    def test_get_project_state(self) -> None:
        project_id = "proj3"
        with project_env(
            project_yaml(project_id), project_id=project_id, with_config_file=True
        ) as env:
            stage_dir = build_dir() / project_id
            stage_dir.mkdir(parents=True, exist_ok=True)
            for name in ("L0.Dockerfile", "L1.cli.Dockerfile", "L1.ui.Dockerfile", "L2.Dockerfile"):
                (stage_dir / name).write_text("", encoding="utf-8")

            # Create SSH keys in the managed ssh-keys store (matches SandboxConfig().ssh_keys_dir)
            sandbox_state = env.base / "sandbox-state"
            ssh_dir = sandbox_state / "ssh-keys" / project_id
            ssh_dir.mkdir(parents=True, exist_ok=True)
            (ssh_dir / "config").write_text("", encoding="utf-8")

            gate_dir = make_sandbox_config().gate_base_path / f"{project_id}.git"
            gate_dir.mkdir(parents=True, exist_ok=True)

            mock_sandbox_cfg = unittest.mock.MagicMock()
            mock_sandbox_cfg.ssh_keys_dir = sandbox_state / "ssh-keys"

            with (
                unittest.mock.patch("terok.lib.domain.project_state.subprocess.run") as run_mock,
                unittest.mock.patch(
                    "terok.lib.core.projects._get_global_git_config", return_value=None
                ),
                unittest.mock.patch(
                    "terok.lib.domain.project_state.make_sandbox_config",
                    return_value=mock_sandbox_cfg,
                ),
            ):
                run_mock.return_value.returncode = 0
                run_mock.return_value.stdout = "2024-01-01T00:00:00Z\t<no value>"
                state = get_project_state(project_id, gate_commit_provider=lambda _pid: None)

        assert state == {
            "dockerfiles": True,
            "dockerfiles_old": True,
            "images": True,
            "images_old": True,
            "ssh": True,
            "gate": True,
            "gate_last_commit": None,
        }
