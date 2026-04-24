# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for project deletion helpers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

from terok.lib.core.config import build_dir as cfg_build_dir
from terok.lib.core.paths import core_state_dir as cfg_state_dir
from terok.lib.core.projects import load_project
from terok.lib.domain.facade import delete_project
from tests.test_utils import project_env, write_project

EnvSetup = Callable[[SimpleNamespace, str], Path]


def project_yaml(project_id: str, *, upstream_url: str = "https://example.com/repo.git") -> str:
    """Build a minimal project config for deletion tests."""
    return f"project:\n  id: {project_id}\ngit:\n  upstream_url: {upstream_url}\n"


def project_root(_env: SimpleNamespace, project_id: str) -> Path:
    """Return the config-root directory for a loaded project."""
    return load_project(project_id).root


def build_dir(_env: SimpleNamespace, project_id: str) -> Path:
    """Create and return the project's build dir."""
    target = cfg_build_dir() / project_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "L2.Dockerfile").write_text("FROM scratch", encoding="utf-8")
    return target


def task_state_dir(_env: SimpleNamespace, project_id: str) -> Path:
    """Create and return the project's state metadata dir."""
    target = cfg_state_dir() / "projects" / project_id
    tasks_dir = target / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    (tasks_dir / "1.yml").write_text("task_id: '1'\n", encoding="utf-8")
    return target


def gate_dir(env: SimpleNamespace, _project_id: str) -> Path:
    """Return the gate mirror directory from ``project_env``."""
    assert env.gate_dir is not None
    return env.gate_dir


def task_archive_subdir(_env: SimpleNamespace, project_id: str) -> Path:
    """Create and return the project's task archive dir (under namespace archive)."""
    from terok.lib.core.config import archive_dir as cfg_archive_dir

    target = cfg_archive_dir() / project_id / "tasks"
    target.mkdir(parents=True, exist_ok=True)
    entry = target / "20260101T000000Z_1_old-task"
    entry.mkdir()
    (entry / "task.yml").write_text("task_id: '1'\n", encoding="utf-8")
    return target.parent  # archive/<project_id>/ — should be removed


@pytest.mark.parametrize(
    ("project_id", "env_kwargs", "setup_target"),
    [
        ("del-proj", {"with_config_file": True}, project_root),
        ("del-build", {"with_config_file": True}, build_dir),
        ("del-meta", {}, task_state_dir),
        ("del-gate", {"with_gate": True}, gate_dir),
        ("del-tarch", {}, task_archive_subdir),
    ],
    ids=["config-dir", "build-dir", "task-metadata-dir", "gate-dir", "task-archive-dir"],
)
def test_delete_project_removes_managed_directories(
    project_id: str,
    env_kwargs: dict[str, bool],
    setup_target: EnvSetup,
) -> None:
    with project_env(project_yaml(project_id), project_id=project_id, **env_kwargs) as env:
        target = setup_target(env, project_id)
        assert target.is_dir()
        delete_project(project_id)
        assert not target.exists()


def test_delete_project_skips_shared_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config_base = tmp_path / "config"
    projects_root = config_base / "projects"
    state_dir = tmp_path / "state"
    envs_dir = tmp_path / "envs"
    sandbox_state = tmp_path / "sandbox-state"
    gate_path = sandbox_state / "gate" / "shared.git"
    config_file = tmp_path / "config.yml"
    gate_path.mkdir(parents=True, exist_ok=True)
    envs_dir.mkdir(parents=True, exist_ok=True)
    projects_root.mkdir(parents=True, exist_ok=True)
    config_file.write_text(
        f"paths:\n  build_dir: {state_dir / 'build'}\ncredentials:\n  dir: {envs_dir}\n",
        encoding="utf-8",
    )

    for project_id, upstream in (("proj-a", "a"), ("proj-b", "b")):
        write_project(
            projects_root,
            project_id,
            project_yaml(project_id, upstream_url=f"https://example.com/{upstream}.git")
            + f"gate:\n  path: {gate_path}\n",
        )

    monkeypatch.setenv("TEROK_CONFIG_DIR", str(config_base))
    monkeypatch.setenv("TEROK_STATE_DIR", str(state_dir))
    monkeypatch.setenv("TEROK_SANDBOX_STATE_DIR", str(sandbox_state))
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(config_file))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "empty"))

    result = delete_project("proj-a")
    assert gate_path.is_dir()
    assert any("proj-b" in entry for entry in result["skipped"])


def test_delete_project_returns_deleted_paths() -> None:
    project_id = "del-ret"
    with project_env(project_yaml(project_id), project_id=project_id):
        result = delete_project(project_id)
        assert isinstance(result["deleted"], list)
        assert isinstance(result["skipped"], list)
        assert result["archive"] is not None
        assert Path(result["archive"]).is_file()
        assert any(project_id in path for path in result["deleted"])


# ---------- _is_under_terok_root safety guard ----------


class TestManagedRootGuard:
    """Verify _is_under_terok_root recognizes all managed directories."""

    def test_state_dir_is_managed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Paths under state_dir are recognized as managed."""
        from terok.lib.domain.project import _is_under_terok_root

        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("TEROK_VAULT_DIR", str(tmp_path / "creds"))
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "empty.yml"))
        assert _is_under_terok_root(tmp_path / "state" / "ssh-keys" / "proj")

    def test_vault_dir_is_managed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Paths under vault_dir are recognized as managed."""
        from terok.lib.domain.project import _is_under_terok_root

        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("TEROK_VAULT_DIR", str(tmp_path / "creds"))
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "empty.yml"))
        assert _is_under_terok_root(tmp_path / "creds" / "envs" / "proj")

    def test_user_projects_dir_is_managed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Paths under user_projects_dir are recognized as managed."""
        from terok.lib.domain.project import _is_under_terok_root

        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("TEROK_VAULT_DIR", str(tmp_path / "creds"))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "empty.yml"))
        assert _is_under_terok_root(tmp_path / "xdg" / "terok" / "projects" / "my-proj")

    def test_projects_dir_is_managed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Paths under projects_dir are recognized as managed."""
        from terok.lib.domain.project import _is_under_terok_root

        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("TEROK_VAULT_DIR", str(tmp_path / "creds"))
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "empty.yml"))
        assert _is_under_terok_root(tmp_path / "cfg" / "projects" / "my-proj")

    def test_build_dir_is_managed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Paths under build_dir are recognized as managed."""
        from terok.lib.domain.project import _is_under_terok_root

        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("TEROK_VAULT_DIR", str(tmp_path / "creds"))
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "empty.yml"))
        assert _is_under_terok_root(tmp_path / "state" / "build" / "some-image")

    def test_archive_dir_is_managed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Paths under archive_dir are recognized as managed."""
        from terok.lib.domain.project import _is_under_terok_root

        monkeypatch.setenv("TEROK_ROOT", str(tmp_path))
        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("TEROK_VAULT_DIR", str(tmp_path / "creds"))
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "empty.yml"))
        assert _is_under_terok_root(tmp_path / "archive" / "myproj" / "tasks")

    def test_external_path_rejected(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Paths outside all managed roots are rejected."""
        from terok.lib.domain.project import _is_under_terok_root

        monkeypatch.setenv("TEROK_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TEROK_CONFIG_DIR", str(tmp_path / "cfg"))
        monkeypatch.setenv("TEROK_VAULT_DIR", str(tmp_path / "creds"))
        monkeypatch.setenv("TEROK_CONFIG_FILE", str(tmp_path / "empty.yml"))
        assert not _is_under_terok_root(Path("/home/user/.ssh"))
