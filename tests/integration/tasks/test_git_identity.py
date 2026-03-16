# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for git identity in task containers.

Two layers are tested:

1. **Runner layer** (``TestConfigStackIdentity``): the real
   ``load_project`` → ``build_task_env_and_volumes`` path that bakes
   ``HUMAN_GIT_NAME``, ``HUMAN_GIT_EMAIL``, and ``TEROK_GIT_AUTHORSHIP``
   into the container environment.  This is what production runners do.

2. **Runtime layer** (``TestAuthorshipModes``, ``TestGitCommitIdentity``):
   ``resolve_git_identity`` applied on top of the runner env, simulating
   what the shell wrapper functions do at agent invocation time inside the
   container.  ``git commit`` tests verify the env vars are picked up.
"""

from __future__ import annotations

import os
import subprocess
import textwrap
import unittest.mock
from contextlib import AbstractContextManager

import pytest

from tests.testnet import EXAMPLE_UPSTREAM_URL

from ..helpers import TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features


# ── Project YAML templates ────────────────────────────────────────────────────

_BASE_PROJECT = f"""\
project:
  id: git-id-test
  security_class: online
git:
  upstream_url: {EXAMPLE_UPSTREAM_URL}
"""


def _project_with_identity(
    *,
    human_name: str | None = None,
    human_email: str | None = None,
    authorship: str | None = None,
) -> str:
    """Build a project.yml with optional git identity fields."""
    lines = [_BASE_PROJECT.rstrip()]
    git_extra: list[str] = []
    if human_name is not None:
        git_extra.append(f"  human_name: {human_name}")
    if human_email is not None:
        git_extra.append(f"  human_email: {human_email}")
    if authorship is not None:
        git_extra.append(f"  authorship: {authorship}")
    if git_extra:
        lines.extend(git_extra)
    return "\n".join(lines) + "\n"


# ── Helpers ───────────────────────────────────────────────────────────────────

_GIT_ENV_KEYS = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)


def _build_runner_env(
    terok_env: TerokIntegrationEnv,
    project_id: str = "git-id-test",
) -> dict[str, str]:
    """Build the container env through the same path the production runners use.

    Calls ``load_project`` → ``build_task_env_and_volumes`` — the exact
    sequence in ``task_run_cli``, ``task_run_toad``, and ``task_run_headless``.
    Returns the env dict containing ``HUMAN_GIT_NAME``, ``HUMAN_GIT_EMAIL``,
    ``TEROK_GIT_AUTHORSHIP``, and all other container env vars.
    """
    from terok.lib.containers.environment import build_task_env_and_volumes
    from terok.lib.core.projects import load_project

    project = load_project(project_id)

    envs_base = terok_env.state_root / "envs"
    envs_base.mkdir(parents=True, exist_ok=True)
    with unittest.mock.patch(
        "terok.lib.containers.environment.get_envs_base_dir", return_value=envs_base
    ):
        env, _volumes = build_task_env_and_volumes(project, "1")
    return env


def _apply_agent_identity(
    env: dict[str, str],
    agent_name: str = "Claude",
    agent_email: str = "noreply@anthropic.com",
) -> dict[str, str]:
    """Apply resolve_git_identity on top of a runner env, as wrappers do at runtime.

    Returns just the four ``GIT_*`` vars.
    """
    from terok.lib.containers.environment import resolve_git_identity

    identity = resolve_git_identity(
        agent_name=agent_name,
        agent_email=agent_email,
        human_name=env["HUMAN_GIT_NAME"],
        human_email=env["HUMAN_GIT_EMAIL"],
        authorship=env.get("TEROK_GIT_AUTHORSHIP", "agent-human"),
    )
    return identity


def _git_subprocess_env(terok_env: TerokIntegrationEnv, git_env: dict[str, str]) -> dict[str, str]:
    """Build the subprocess env for git commands from the isolated test env.

    Inherits HOME, XDG_CONFIG_HOME, and PATH from the isolated ``terok_env``
    so git doesn't pick up the host user's global config.
    """
    return {
        "HOME": str(terok_env.home_dir),
        "XDG_CONFIG_HOME": str(terok_env.xdg_config_home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GIT_CONFIG_NOSYSTEM": "1",
        **git_env,
    }


def _write_global_config(terok_env: TerokIntegrationEnv, yaml_text: str) -> None:
    """Write the global terok config.yml in the isolated XDG config root."""
    config_dir = terok_env.xdg_config_home / "terok"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yml").write_text(
        textwrap.dedent(yaml_text).strip() + "\n", encoding="utf-8"
    )


def _mock_host_git_config(
    name: str | None = None, email: str | None = None
) -> AbstractContextManager[unittest.mock._patch]:
    """Mock _get_global_git_config to return specific host git identity values."""

    def _fake_config(key: str) -> str | None:
        if key == "user.name":
            return name
        if key == "user.email":
            return email
        return None

    return unittest.mock.patch(
        "terok.lib.core.projects._get_global_git_config", side_effect=_fake_config
    )


def _mock_no_host_git() -> AbstractContextManager[unittest.mock._patch]:
    """Mock _get_global_git_config to simulate no host git config."""
    return unittest.mock.patch("terok.lib.core.projects._get_global_git_config", return_value=None)


# ── Tests: authorship modes (runtime layer) ──────────────────────────────────


class TestAuthorshipModes:
    """Verify resolve_git_identity produces correct GIT_* vars for all four modes.

    Uses the real runner env (with config-stack-resolved human identity)
    as input to resolve_git_identity — simulating what shell wrappers do.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, terok_env: TerokIntegrationEnv) -> None:
        self.terok_env = terok_env

    def _identity_for_mode(self, mode: str) -> dict[str, str]:
        """Write a project with given authorship mode and resolve git identity."""
        self.terok_env.write_project(
            "git-id-test",
            _project_with_identity(
                human_name="Alice Human",
                human_email="alice@example.com",
                authorship=mode,
            ),
        )
        with _mock_no_host_git():
            env = _build_runner_env(self.terok_env)
        return _apply_agent_identity(env)

    def test_agent_human_mode(self) -> None:
        """agent-human: agent is author, human is committer (default)."""
        git = self._identity_for_mode("agent-human")
        assert git["GIT_AUTHOR_NAME"] == "Claude"
        assert git["GIT_AUTHOR_EMAIL"] == "noreply@anthropic.com"
        assert git["GIT_COMMITTER_NAME"] == "Alice Human"
        assert git["GIT_COMMITTER_EMAIL"] == "alice@example.com"

    def test_human_agent_mode(self) -> None:
        """human-agent: human is author, agent is committer."""
        git = self._identity_for_mode("human-agent")
        assert git["GIT_AUTHOR_NAME"] == "Alice Human"
        assert git["GIT_AUTHOR_EMAIL"] == "alice@example.com"
        assert git["GIT_COMMITTER_NAME"] == "Claude"
        assert git["GIT_COMMITTER_EMAIL"] == "noreply@anthropic.com"

    def test_agent_mode(self) -> None:
        """agent: agent is both author and committer."""
        git = self._identity_for_mode("agent")
        assert git["GIT_AUTHOR_NAME"] == "Claude"
        assert git["GIT_AUTHOR_EMAIL"] == "noreply@anthropic.com"
        assert git["GIT_COMMITTER_NAME"] == "Claude"
        assert git["GIT_COMMITTER_EMAIL"] == "noreply@anthropic.com"

    def test_human_mode(self) -> None:
        """human: human is both author and committer."""
        git = self._identity_for_mode("human")
        assert git["GIT_AUTHOR_NAME"] == "Alice Human"
        assert git["GIT_AUTHOR_EMAIL"] == "alice@example.com"
        assert git["GIT_COMMITTER_NAME"] == "Alice Human"
        assert git["GIT_COMMITTER_EMAIL"] == "alice@example.com"


# ── Tests: git commit picks up env vars (runtime layer) ──────────────────────


class TestGitCommitIdentity:
    """Verify that ``git commit`` uses the resolved GIT_* env vars.

    Runs a real ``git init`` + ``git commit`` in a temp dir with GIT_*
    env vars set as they would be after a shell wrapper applies identity,
    then inspects the resulting commit metadata.
    """

    @pytest.mark.parametrize("mode", ["agent-human", "human-agent", "agent", "human"])
    def test_git_commit_respects_all_modes(self, terok_env: TerokIntegrationEnv, mode: str) -> None:
        """Git commit metadata matches the authorship mode for all four variants."""
        terok_env.write_project(
            "git-id-test",
            _project_with_identity(
                human_name="Bob Builder",
                human_email="bob@example.com",
                authorship=mode,
            ),
        )
        with _mock_no_host_git():
            env = _build_runner_env(terok_env)
        git_env = _apply_agent_identity(env)

        workspace = terok_env.task_workspace("git-id-test", "1")
        workspace.mkdir(parents=True, exist_ok=True)

        run_opts = {
            "cwd": workspace,
            "env": _git_subprocess_env(terok_env, git_env),
            "check": True,
            "capture_output": True,
            "text": True,
            "timeout": 10,
        }
        subprocess.run(["git", "init"], **run_opts)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "test"], **run_opts)

        log = subprocess.run(["git", "log", "-1", "--format=%an|%ae|%cn|%ce"], **run_opts)
        an, ae, cn, ce = log.stdout.strip().split("|")

        expected = {
            "agent-human": ("Claude", "noreply@anthropic.com", "Bob Builder", "bob@example.com"),
            "human-agent": ("Bob Builder", "bob@example.com", "Claude", "noreply@anthropic.com"),
            "agent": ("Claude", "noreply@anthropic.com", "Claude", "noreply@anthropic.com"),
            "human": ("Bob Builder", "bob@example.com", "Bob Builder", "bob@example.com"),
        }
        assert (an, ae, cn, ce) == expected[mode]


# ── Tests: config stack identity loading (runner layer) ───────────────────────


class TestConfigStackIdentity:
    """Verify the identity resolution stack: git global → terok global → project.yml.

    These tests exercise ``build_task_env_and_volumes`` — the same call the
    production runners make — and assert on the ``HUMAN_GIT_*`` and
    ``TEROK_GIT_AUTHORSHIP`` env vars that are baked into the container env.
    """

    @pytest.fixture(autouse=True)
    def _setup(self, terok_env: TerokIntegrationEnv) -> None:
        self.terok_env = terok_env

    def test_no_host_git_no_global_config_uses_project_yml(self) -> None:
        """With no host git config and no global config, project.yml identity is used."""
        self.terok_env.write_project(
            "git-id-test",
            _project_with_identity(human_name="Project User", human_email="proj@example.com"),
        )
        with _mock_no_host_git():
            env = _build_runner_env(self.terok_env)

        assert env["HUMAN_GIT_NAME"] == "Project User"
        assert env["HUMAN_GIT_EMAIL"] == "proj@example.com"

    def test_host_git_config_autoloaded_as_fallback(self) -> None:
        """Host git config provides human identity when project.yml omits it."""
        self.terok_env.write_project("git-id-test", _BASE_PROJECT)
        with _mock_host_git_config("Host Git User", "hostgit@example.com"):
            env = _build_runner_env(self.terok_env)

        assert env["HUMAN_GIT_NAME"] == "Host Git User"
        assert env["HUMAN_GIT_EMAIL"] == "hostgit@example.com"

    def test_project_yml_overrides_host_git_config(self) -> None:
        """Project.yml human_name/email takes precedence over host git config."""
        self.terok_env.write_project(
            "git-id-test",
            _project_with_identity(
                human_name="Project Override", human_email="override@example.com"
            ),
        )
        with _mock_host_git_config("Host Git User", "hostgit@example.com"):
            env = _build_runner_env(self.terok_env)

        assert env["HUMAN_GIT_NAME"] == "Project Override"
        assert env["HUMAN_GIT_EMAIL"] == "override@example.com"

    def test_global_config_overrides_host_git(self) -> None:
        """terok-config.yml git section overrides host git config."""
        self.terok_env.write_project("git-id-test", _BASE_PROJECT)
        _write_global_config(
            self.terok_env,
            """\
            git:
              human_name: Global Terok User
              human_email: global@terok.dev
        """,
        )
        with _mock_host_git_config("Host Git User", "hostgit@example.com"):
            env = _build_runner_env(self.terok_env)

        assert env["HUMAN_GIT_NAME"] == "Global Terok User"
        assert env["HUMAN_GIT_EMAIL"] == "global@terok.dev"

    def test_project_yml_overrides_global_config(self) -> None:
        """Project.yml takes precedence over terok-config.yml global config."""
        self.terok_env.write_project(
            "git-id-test",
            _project_with_identity(human_name="Project Wins", human_email="project@example.com"),
        )
        _write_global_config(
            self.terok_env,
            """\
            git:
              human_name: Global Terok User
              human_email: global@terok.dev
        """,
        )
        with _mock_host_git_config("Host Git User", "hostgit@example.com"):
            env = _build_runner_env(self.terok_env)

        assert env["HUMAN_GIT_NAME"] == "Project Wins"
        assert env["HUMAN_GIT_EMAIL"] == "project@example.com"

    def test_no_identity_anywhere_falls_back_to_nobody(self) -> None:
        """With no identity configured anywhere, falls back to Nobody/nobody@localhost."""
        self.terok_env.write_project("git-id-test", _BASE_PROJECT)
        with _mock_no_host_git():
            env = _build_runner_env(self.terok_env)

        assert env["HUMAN_GIT_NAME"] == "Nobody"
        assert env["HUMAN_GIT_EMAIL"] == "nobody@localhost"

    def test_global_authorship_mode_applied(self) -> None:
        """Authorship mode from terok-config.yml is applied when project.yml omits it."""
        self.terok_env.write_project(
            "git-id-test",
            _project_with_identity(human_name="Alice", human_email="alice@example.com"),
        )
        _write_global_config(
            self.terok_env,
            """\
            git:
              authorship: human-agent
        """,
        )
        with _mock_no_host_git():
            env = _build_runner_env(self.terok_env)

        assert env["TEROK_GIT_AUTHORSHIP"] == "human-agent"

    def test_partial_host_git_name_only(self) -> None:
        """Host git config with only user.name and no email still propagates name."""
        self.terok_env.write_project("git-id-test", _BASE_PROJECT)
        with _mock_host_git_config("Just A Name", None):
            env = _build_runner_env(self.terok_env)

        assert env["HUMAN_GIT_NAME"] == "Just A Name"
        assert env["HUMAN_GIT_EMAIL"] == "nobody@localhost"
