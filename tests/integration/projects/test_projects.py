# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for project listing and derivation workflows."""

from __future__ import annotations

import pytest

from terok.lib.util.yaml import load as yaml_load
from tests.testnet import TEST_UPSTREAM_URL

from ..helpers import TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features

SOURCE_PROJECT = f"""
project:
  id: alpha
  security_class: online
git:
  upstream_url: {TEST_UPSTREAM_URL}
  default_branch: main
ssh:
  key_name: id_alpha
agent:
  provider: codex
"""


class TestProjects:
    """Verify project workflows through the real CLI."""

    def test_projects_lists_user_and_system_projects(self, terok_env: TerokIntegrationEnv) -> None:
        """``terokctl projects`` lists projects from both isolated config roots."""
        terok_env.write_project("alpha", SOURCE_PROJECT)
        terok_env.write_project(
            "sysalpha",
            SOURCE_PROJECT.replace("id: alpha", "id: sysalpha"),
            scope="system",
        )

        result = terok_env.run_cli("projects")

        assert "Known projects:" in result.stdout
        assert (
            f"- alpha [online] upstream={TEST_UPSTREAM_URL} "
            f"config_root={terok_env.project_root('alpha')}"
        ) in result.stdout
        assert (
            f"- sysalpha [online] upstream={TEST_UPSTREAM_URL} "
            f"config_root={terok_env.project_root('sysalpha', scope='system')}"
        ) in result.stdout

    def test_project_derive_preserves_infra_and_clears_agent(
        self, terok_env: TerokIntegrationEnv
    ) -> None:
        """``project-derive`` copies infra config but clears ``agent:``."""
        terok_env.write_project("alpha", SOURCE_PROJECT)

        result = terok_env.run_cli("project-derive", "alpha", "beta")

        target = terok_env.project_root("beta") / "project.yml"
        assert "Derived project 'beta' from 'alpha'" in result.stdout
        assert target.is_file()

        derived = yaml_load(target.read_text(encoding="utf-8"))
        assert derived["project"]["id"] == "beta"
        assert "agent" not in derived
        assert derived["git"]["upstream_url"] == TEST_UPSTREAM_URL
        assert derived["ssh"]["key_name"] == "id_alpha"

        listed = terok_env.run_cli("projects")
        assert "- alpha [online]" in listed.stdout
        assert "- beta [online]" in listed.stdout
