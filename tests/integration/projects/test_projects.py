# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for project listing and derivation workflows."""

from __future__ import annotations

import pytest
import yaml

pytestmark = pytest.mark.needs_host_features

SOURCE_PROJECT = """
project:
  id: alpha
  security_class: online
git:
  upstream_url: https://example.com/source.git
  default_branch: main
ssh:
  key_name: id_alpha
agent:
  provider: codex
"""


class TestProjects:
    """Verify project workflows through the real CLI."""

    def test_projects_lists_user_projects(self, terok_env) -> None:
        """``terokctl projects`` lists projects from the isolated user root."""
        terok_env.write_project("alpha", SOURCE_PROJECT)

        result = terok_env.run_cli("projects")

        assert "Known projects:" in result.stdout
        assert "- alpha [online]" in result.stdout
        assert "upstream=https://example.com/source.git" in result.stdout

    def test_project_derive_preserves_infra_and_clears_agent(self, terok_env) -> None:
        """``project-derive`` copies infra config but clears ``agent:``."""
        terok_env.write_project("alpha", SOURCE_PROJECT)

        result = terok_env.run_cli("project-derive", "alpha", "beta")

        target = terok_env.project_root("beta") / "project.yml"
        assert "Derived project 'beta' from 'alpha'" in result.stdout
        assert target.is_file()

        derived = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert derived["project"]["id"] == "beta"
        assert "agent" not in derived
        assert derived["git"]["upstream_url"] == "https://example.com/source.git"
        assert derived["ssh"]["key_name"] == "id_alpha"

        listed = terok_env.run_cli("projects")
        assert "- alpha [online]" in listed.stdout
        assert "- beta [online]" in listed.stdout
