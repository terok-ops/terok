# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for environment.py gate-server integration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from terok.lib.core.projects import ProjectConfig, load_project
from terok.lib.orchestration.environment import _security_mode_env_and_volumes
from tests.test_utils import mock_git_config, project_env
from tests.testnet import GATE_PORT, gate_repo_url

_GATEKEEPING_YAML = """\
project:
  id: gk-proj
  security_class: gatekeeping
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""

_ONLINE_YAML = """\
project:
  id: online-proj
  security_class: online
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""


def gate_mounts(volumes: list[str]) -> list[str]:
    """Return any gate-related volume mounts from the generated volume list."""
    return [volume for volume in volumes if "git-gate" in volume or "gate" in volume.split(":")[0]]


def resolve_security_env(
    yaml_text: str,
    *,
    project_id: str,
    with_gate: bool,
    token: str | None = None,
    ensure_side_effect: BaseException | None = None,
) -> tuple[ProjectConfig, dict[str, str], list[str]]:
    """Load a project and evaluate gate-related env/volume settings."""
    with (
        mock_git_config(),
        project_env(yaml_text, project_id=project_id, with_gate=with_gate) as ctx,
        patch(
            "terok.lib.orchestration.environment.ensure_server_reachable",
            side_effect=ensure_side_effect,
        ),
        patch("terok.lib.orchestration.environment.get_gate_server_port", return_value=GATE_PORT),
        patch(
            "terok.lib.orchestration.environment.get_gate_base_path",
            return_value=ctx.base / "sandbox-state" / "gate",
        ),
        patch("terok.lib.orchestration.environment.create_token", return_value=token),
    ):
        project = load_project(project_id)
        env, volumes = _security_mode_env_and_volumes(project, "1")
    return project, env, volumes


@pytest.mark.parametrize(
    ("yaml_text", "project_id", "token", "env_key"),
    [
        pytest.param(_GATEKEEPING_YAML, "gk-proj", "deadbeef" * 4, "CODE_REPO", id="gatekeeping"),
        pytest.param(_ONLINE_YAML, "online-proj", "cafebabe" * 4, "CLONE_FROM", id="online"),
    ],
)
def test_gate_projects_use_http_urls_with_tokens(
    yaml_text: str,
    project_id: str,
    token: str,
    env_key: str,
) -> None:
    """Gate-backed project modes generate token-authenticated HTTP URLs."""
    project, env, volumes = resolve_security_env(
        yaml_text,
        project_id=project_id,
        with_gate=True,
        token=token,
    )

    assert env[env_key] == gate_repo_url(project_id, token)
    assert gate_mounts(volumes) == []

    if project.security_class == "gatekeeping":
        assert env["GIT_BRANCH"] == "main"
    else:
        assert env["CODE_REPO"] == "https://example.com/repo.git"


def test_gatekeeping_missing_gate_raises() -> None:
    """Gatekeeping mode requires a synced gate mirror before task startup."""
    with mock_git_config(), project_env(_GATEKEEPING_YAML, project_id="gk-proj", with_gate=False):
        project = load_project("gk-proj")
        with pytest.raises(SystemExit, match="gate-sync"):
            _security_mode_env_and_volumes(project, "1")


def test_gatekeeping_server_not_running_raises() -> None:
    """Gatekeeping mode fails when the gate server cannot be reached."""
    with pytest.raises(SystemExit, match="Gate server"):
        resolve_security_env(
            _GATEKEEPING_YAML,
            project_id="gk-proj",
            with_gate=True,
            ensure_side_effect=SystemExit("Gate server unavailable"),
        )


@pytest.mark.parametrize(
    "server_reachable",
    [pytest.param(True, id="server-up"), pytest.param(False, id="server-down")],
)
def test_online_gate_server_fallback(server_reachable: bool) -> None:
    """Online mode uses CLONE_FROM only when the gate server is reachable."""
    _project, env, volumes = resolve_security_env(
        _ONLINE_YAML,
        project_id="online-proj",
        with_gate=True,
        token="cafebabe" * 4,
        ensure_side_effect=None if server_reachable else SystemExit("server down"),
    )

    if server_reachable:
        assert env["CLONE_FROM"] == gate_repo_url("online-proj", "cafebabe" * 4)
    else:
        assert "CLONE_FROM" not in env
    assert env["CODE_REPO"] == "https://example.com/repo.git"
    assert gate_mounts(volumes) == []


def test_online_without_gate_has_no_clone_from() -> None:
    """Online mode without a gate mirror clones directly from upstream only."""
    _project, env, volumes = resolve_security_env(
        _ONLINE_YAML,
        project_id="online-proj",
        with_gate=False,
    )
    assert "CLONE_FROM" not in env
    assert env["CODE_REPO"] == "https://example.com/repo.git"
    assert gate_mounts(volumes) == []
