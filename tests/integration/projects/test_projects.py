# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for project listing and derivation workflows."""

from __future__ import annotations

import re

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

# Source config with comments on every section — used to verify that
# derive_project round-trips comments through ruamel.yaml.
COMMENTED_PROJECT = f"""
# === Project identity ===
project:
  id: alpha
  security_class: online  # keep this online for dev

# --- Git configuration ---
git:
  upstream_url: {TEST_UPSTREAM_URL}
  default_branch: main  # pinned to main

# SSH keys for container access
ssh:
  key_name: id_alpha  # custom key

# Agent section (will be cleared on derive)
agent:
  provider: codex  # default provider
"""


class TestProjects:
    """Verify project workflows through the real CLI."""

    def test_projects_lists_user_and_system_projects(self, terok_env: TerokIntegrationEnv) -> None:
        """``terok projects`` lists projects from both isolated config roots."""
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
        """``project-derive`` pins shared gate+SSH paths and clears ``agent:``."""
        terok_env.write_project("alpha", SOURCE_PROJECT)

        result = terok_env.run_cli("project-derive", "alpha", "beta")

        target = terok_env.project_root("beta") / "project.yml"
        assert "Derived project 'beta' from 'alpha'" in result.stdout
        assert "shares git gate and SSH key with source" in result.stdout
        assert target.is_file()

        derived = yaml_load(target.read_text(encoding="utf-8"))
        assert derived["project"]["id"] == "beta"
        assert "agent" not in derived
        assert derived["git"]["upstream_url"] == TEST_UPSTREAM_URL

        # Shared infra is pinned to source's resolved paths so the derived
        # project points at the same gate mirror and SSH keypair.
        assert derived["gate"]["path"] == str(terok_env.gate_path("alpha"))
        expected_ssh_dir = terok_env.sandbox_state_root / "ssh-keys" / "alpha"
        assert derived["ssh"]["host_dir"] == str(expected_ssh_dir)
        assert derived["ssh"]["key_name"] == "id_alpha"

        listed = terok_env.run_cli("projects")
        assert "- alpha [online]" in listed.stdout
        assert "- beta [online]" in listed.stdout

    def test_project_derive_copies_instructions_md(self, terok_env: TerokIntegrationEnv) -> None:
        """``project-derive`` copies source ``instructions.md`` into the new project.

        Absent on the source, the file must remain absent on the target — the
        copy is best-effort, not a required invariant of every derivation.
        """
        source_root = terok_env.write_project("alpha", SOURCE_PROJECT)
        instructions = "# Alpha house rules\n\nAlways run `make lint` first.\n"
        (source_root / "instructions.md").write_text(instructions, encoding="utf-8")

        terok_env.run_cli("project-derive", "alpha", "beta")

        derived_instructions = terok_env.project_root("beta") / "instructions.md"
        assert derived_instructions.is_file()
        assert derived_instructions.read_text(encoding="utf-8") == instructions

        # Sibling without source instructions.md stays clean.
        terok_env.write_project("gamma", SOURCE_PROJECT.replace("id: alpha", "id: gamma"))
        terok_env.run_cli("project-derive", "gamma", "delta")
        assert not (terok_env.project_root("delta") / "instructions.md").exists()

    def test_project_derive_preserves_yaml_comments(self, terok_env: TerokIntegrationEnv) -> None:
        """``project-derive`` preserves user comments via ruamel.yaml round-trip.

        Creates a source project.yml with inline and block comments on every
        section, derives a new project, then inspects the raw output file to
        confirm comments survived the load → modify → dump cycle.
        """
        terok_env.write_project("commented", COMMENTED_PROJECT)

        terok_env.run_cli("project-derive", "commented", "derived")

        target = terok_env.project_root("derived") / "project.yml"
        raw = target.read_text(encoding="utf-8")
        derived = yaml_load(raw)

        # ── Structural correctness (same as the non-commented test) ──
        assert derived["project"]["id"] == "derived"
        assert derived["git"]["upstream_url"] == TEST_UPSTREAM_URL
        assert derived["ssh"]["key_name"] == "id_alpha"
        assert "agent" not in derived  # cleared by derive

        # ── Comment preservation (the raison d'être of ruamel.yaml) ──
        # Block comments above sections
        assert "# === Project identity ===" in raw
        assert "# --- Git configuration ---" in raw
        assert "# SSH keys for container access" in raw

        # Inline comments on values
        assert "# keep this online for dev" in raw
        assert "# pinned to main" in raw
        assert "# custom key" in raw

        # The agent section and its associated comments should be gone
        assert re.search(r"(?m)^\s*agent\s*:", raw) is None
        assert re.search(r"(?m)^\s*#.*\bagent\b", raw) is None

        # Original section order is preserved (not alphabetically sorted).
        # ``gate`` is appended by derive to pin the shared gate mirror path.
        keys = [m.group(1) for m in re.finditer(r"(?m)^(\w+)\s*:", raw)]
        assert keys == ["project", "git", "ssh", "gate"]
