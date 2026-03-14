# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for terok integration tests."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT / "src"
PROJECT_FILENAME = "project.yml"
WORKSPACE_DIRNAME = "workspace-dangerous"
NEW_TASK_MARKER = ".new-task-marker"

type ProjectScope = Literal["user", "system"]


@dataclass(frozen=True)
class TerokIntegrationEnv:
    """Filesystem roots and helpers for a single integration test."""

    base_dir: Path
    home_dir: Path
    xdg_config_home: Path
    system_config_root: Path
    state_root: Path

    @property
    def user_projects_root(self) -> Path:
        """Return the isolated user projects root."""
        return self.xdg_config_home / "terok" / "projects"

    @property
    def global_presets_root(self) -> Path:
        """Return the isolated global presets root."""
        return self.xdg_config_home / "terok" / "presets"

    @property
    def cli_env(self) -> dict[str, str]:
        """Return environment variables for a real ``python -m terok.cli`` run."""
        env = os.environ.copy()
        pythonpath = os.environ.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{pythonpath}" if pythonpath else str(SRC_DIR)
        env.update(
            {
                "HOME": str(self.home_dir),
                "XDG_CONFIG_HOME": str(self.xdg_config_home),
                "TEROK_CONFIG_DIR": str(self.system_config_root),
                "TEROK_STATE_DIR": str(self.state_root),
            }
        )
        return env

    def run_cli(
        self,
        *args: str,
        input_text: str | None = None,
        check: bool = True,
        timeout: int = 30,
    ) -> subprocess.CompletedProcess[str]:
        """Run the terok CLI in a subprocess and capture the result."""
        result = subprocess.run(
            [sys.executable, "-m", "terok.cli", "--no-emoji", *args],
            input=input_text,
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=self.cli_env,
            timeout=timeout,
        )
        if check and result.returncode != 0:
            raise AssertionError(
                "CLI command failed:\n"
                f"  command: terokctl {' '.join(args)}\n"
                f"  exit: {result.returncode}\n"
                f"  stdout:\n{result.stdout}\n"
                f"  stderr:\n{result.stderr}"
            )
        return result

    def write_project(
        self,
        project_id: str,
        yaml_text: str,
        *,
        scope: ProjectScope = "user",
    ) -> Path:
        """Write ``project.yml`` for *project_id* under the requested scope."""
        root = self.user_projects_root if scope == "user" else self.system_config_root
        project_root = root / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        content = textwrap.dedent(yaml_text).strip() + "\n"
        (project_root / PROJECT_FILENAME).write_text(content, encoding="utf-8")
        return project_root

    def project_root(self, project_id: str, *, scope: ProjectScope = "user") -> Path:
        """Return the project root path for *project_id* in the requested scope."""
        root = self.user_projects_root if scope == "user" else self.system_config_root
        return root / project_id

    def tasks_root(self, project_id: str) -> Path:
        """Return the live task workspace root for *project_id*."""
        return self.state_root / "tasks" / project_id

    def task_dir(self, project_id: str, task_id: str) -> Path:
        """Return the task directory for *task_id*."""
        return self.tasks_root(project_id) / task_id

    def task_workspace(self, project_id: str, task_id: str) -> Path:
        """Return the task workspace directory for *task_id*."""
        return self.task_dir(project_id, task_id) / WORKSPACE_DIRNAME

    def task_meta_dir(self, project_id: str) -> Path:
        """Return the metadata directory for project tasks."""
        return self.state_root / "projects" / project_id / "tasks"

    def task_meta_path(self, project_id: str, task_id: str) -> Path:
        """Return the metadata YAML path for *task_id*."""
        return self.task_meta_dir(project_id) / f"{task_id}.yml"

    def task_archive_root(self, project_id: str) -> Path:
        """Return the archive root for deleted tasks."""
        return self.state_root / "projects" / project_id / "archive"
