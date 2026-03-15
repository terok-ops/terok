# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for git-gate sync workflows."""

from __future__ import annotations

import pytest

from tests.testgit import (
    append_commit_to_bare_repo,
    create_bare_repo_with_branches,
    file_repo_url,
    git_head,
    run_git,
)

from ..conftest import git_missing
from ..helpers import TerokIntegrationEnv

pytestmark = pytest.mark.needs_host_features

PROJECT_TEMPLATE = """
project:
  id: {project_id}
  security_class: gatekeeping
git:
  upstream_url: {upstream_url}
  default_branch: main
gate:
  path: {gate_path}
"""


class TestGateSync:
    """Verify gate sync workflows through the real CLI."""

    @git_missing
    def test_gate_sync_creates_bare_mirror_and_fetches_updates(
        self, terok_env: TerokIntegrationEnv
    ) -> None:
        """``gate-sync`` creates and then refreshes the host-side bare mirror."""
        upstream = terok_env.base_dir / "upstream.git"
        create_bare_repo_with_branches(upstream, default_branch="main", other_branches=["dev"])

        gate_path = terok_env.gate_path("demo")
        terok_env.write_project(
            "demo",
            PROJECT_TEMPLATE.format(
                project_id="demo",
                upstream_url=file_repo_url(upstream),
                gate_path=gate_path,
            ),
        )

        created = terok_env.run_cli("gate-sync", "demo")
        assert f"Gate ready at {gate_path}" in created.stdout
        assert (
            run_git("rev-parse", "--is-bare-repository", repo_path=gate_path).stdout.strip()
            == "true"
        )
        assert git_head(gate_path, "refs/heads/main") == git_head(upstream, "refs/heads/main")

        append_commit_to_bare_repo(upstream, "main", "CHANGELOG.md", "v2\n", "Update upstream")
        synced = terok_env.run_cli("gate-sync", "demo")

        assert "created: False" in synced.stdout
        assert git_head(gate_path, "refs/heads/main") == git_head(upstream, "refs/heads/main")

    @git_missing
    def test_gate_sync_rejects_conflicting_shared_gate(
        self, terok_env: TerokIntegrationEnv
    ) -> None:
        """Projects cannot share one gate path when their upstreams differ."""
        alpha_upstream = terok_env.base_dir / "alpha.git"
        beta_upstream = terok_env.base_dir / "beta.git"
        create_bare_repo_with_branches(alpha_upstream, default_branch="main", other_branches=[])
        create_bare_repo_with_branches(beta_upstream, default_branch="main", other_branches=[])

        shared_gate = terok_env.base_dir / "shared-gate.git"
        terok_env.write_project(
            "alpha",
            PROJECT_TEMPLATE.format(
                project_id="alpha",
                upstream_url=file_repo_url(alpha_upstream),
                gate_path=shared_gate,
            ),
        )

        terok_env.run_cli("gate-sync", "alpha")
        terok_env.write_project(
            "beta",
            PROJECT_TEMPLATE.format(
                project_id="beta",
                upstream_url=file_repo_url(beta_upstream),
                gate_path=shared_gate,
            ),
        )
        conflict = terok_env.run_cli("gate-sync", "beta", check=False)
        combined = f"{conflict.stdout}\n{conflict.stderr}"

        assert conflict.returncode != 0
        assert "Gate path conflict detected!" in combined
        assert str(shared_gate) in combined
