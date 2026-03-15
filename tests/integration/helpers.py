# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for terok integration tests."""

from __future__ import annotations

import json
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
PODMAN_TEST_IMAGE = "docker.io/library/alpine:latest"
PODMAN_CONTAINER_PREFIX = "terok-itest"
PODMAN_SLEEP_COMMAND = ("sleep", "300")

type ProjectScope = Literal["user", "system"]


@dataclass(frozen=True)
class TerokShieldIntegrationEnv:
    """Filesystem roots for a shield-focused integration test."""

    base_dir: Path
    task_dir: Path
    state_dir: Path


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
    def envs_base_dir(self) -> Path:
        """Return the isolated shared env mount base directory."""
        return self.state_root / "envs"

    @property
    def system_projects_root(self) -> Path:
        """Return the isolated system projects root."""
        return self.system_config_root / "projects"

    def _projects_root(self, scope: ProjectScope) -> Path:
        """Return the projects root for the requested scope."""
        return self.user_projects_root if scope == "user" else self.system_projects_root

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
        project_root = self._projects_root(scope) / project_id
        project_root.mkdir(parents=True, exist_ok=True)
        content = textwrap.dedent(yaml_text).strip() + "\n"
        (project_root / PROJECT_FILENAME).write_text(content, encoding="utf-8")
        return project_root

    def project_root(self, project_id: str, *, scope: ProjectScope = "user") -> Path:
        """Return the project root path for *project_id* in the requested scope."""
        return self._projects_root(scope) / project_id

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

    def gate_path(self, project_id: str) -> Path:
        """Return the host-side gate mirror path for ``project_id``."""
        return self.state_root / "gate" / f"{project_id}.git"


def _hook_diagnostics(extra_args: list[str]) -> str:
    """Gather OCI hook diagnostics from shield extra args."""
    try:
        hooks_index = extra_args.index("--hooks-dir")
        hooks_dir = Path(extra_args[hooks_index + 1])
        hook_json = hooks_dir / "terok-shield-createRuntime.json"
        if not hook_json.exists():
            return f"\n  [diag] hook JSON missing: {hook_json}"
        data = json.loads(hook_json.read_text(encoding="utf-8"))
        entrypoint = Path(data["hook"]["path"])
        parts = [f"entrypoint={entrypoint}", f"exists={entrypoint.exists()}"]
        if entrypoint.exists():
            parts.append(f"executable={os.access(entrypoint, os.X_OK)}")
            parts.append(f"content={entrypoint.read_text(encoding='utf-8').strip()!r}")
        return f"\n  [diag] {', '.join(parts)}"
    except ValueError:
        return "\n  [diag] --hooks-dir missing from podman extra args"
    except Exception as exc:  # pragma: no cover - diagnostic fallback
        return f"\n  [diag] error: {exc}"


def start_shielded_container(
    name: str,
    extra_args: list[str],
    image: str = PODMAN_TEST_IMAGE,
    *,
    timeout: int = 60,
) -> None:
    """Start a podman container with shield args and detailed failure output."""
    result = subprocess.run(
        ["podman", "run", "-d", "--name", name, *extra_args, image, *PODMAN_SLEEP_COMMAND],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        diagnostics = _hook_diagnostics(extra_args)
        raise RuntimeError(
            f"podman run failed (exit {result.returncode}):\n"
            f"  stderr: {result.stderr.strip()}\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  extra_args: {extra_args}{diagnostics}"
        )


def inspect_container_json(container: str, *, timeout: int = 30) -> dict[str, object]:
    """Return ``podman inspect`` output for ``container`` as a single JSON object."""
    result = subprocess.run(
        ["podman", "inspect", container, "--format", "json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=timeout,
    )
    data = json.loads(result.stdout)
    return data[0]


def exec_in_container(
    container: str, *cmd: str, timeout: int = 10
) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` inside ``container`` via ``podman exec``."""
    return subprocess.run(
        ["podman", "exec", container, *cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def wget(container: str, url: str, timeout: int = 5) -> subprocess.CompletedProcess[str]:
    """Attempt an outbound HTTP/HTTPS request from inside ``container``."""
    return exec_in_container(
        container,
        "wget",
        "-q",
        "-O",
        "/dev/null",
        f"--timeout={timeout}",
        url,
        timeout=timeout + 5,
    )


def _assert_container_running(container: str) -> None:
    """Assert that ``container`` is running to avoid false-positive assertions."""
    result = subprocess.run(
        ["podman", "inspect", "--format", "{{.State.Running}}", container],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout.strip() == "true", (
        f"Container {container} is not running — cannot assert network behavior: {result.stderr}"
    )


def is_reachable(result: subprocess.CompletedProcess[str]) -> bool:
    """Return whether a wget result proves the target was reachable."""
    if result.returncode == 0:
        return True
    return "bad address" in result.stderr


def assert_blocked(container: str, url: str, timeout: int = 10) -> None:
    """Assert that ``url`` is blocked from inside ``container``."""
    _assert_container_running(container)
    result = wget(container, url, timeout=timeout)
    assert result.returncode != 0, f"Expected {url} to be blocked, but it was reachable"


def assert_reachable(container: str, url: str, timeout: int = 10) -> None:
    """Assert that ``url`` is reachable from inside ``container``."""
    _assert_container_running(container)
    result = wget(container, url, timeout=timeout)
    assert is_reachable(result), (
        f"Expected {url} to be reachable, but it was blocked: {result.stderr}"
    )
