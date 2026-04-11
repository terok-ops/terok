# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for git-gate syncing, comparison, and gate-sharing validation."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from terok_sandbox.gate.mirror import _get_gate_branch_head, _get_upstream_head

from terok.lib.core.projects import load_project
from terok.lib.domain.project import (
    find_projects_sharing_gate,
    make_git_gate,
    validate_gate_upstream_match,
)
from tests.test_utils import project_env, write_project

TEST_UPSTREAM_URL = "https://example.com/repo.git"


def git_project_yaml(
    project_id: str,
    *,
    upstream_url: str | None = TEST_UPSTREAM_URL,
    default_branch: str | None = "main",
    gate_path: Path | None = None,
) -> str:
    """Build a project config for git-gate-related tests."""
    lines = [f"project:\n  id: {project_id}\n"]
    if upstream_url is not None or default_branch is not None:
        lines.append("git:\n")
        if upstream_url is not None:
            lines.append(f"  upstream_url: {upstream_url}\n")
        if default_branch is not None:
            lines.append(f"  default_branch: {default_branch}\n")
    if gate_path is not None:
        lines.append("gate:\n")
        lines.append(f"  path: {gate_path}\n")
    return "".join(lines)


def git_result(*, returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    """Build a ``subprocess.run`` result mock."""
    return MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)


def configure_shared_gate(
    ctx,
    project_id: str,
    upstream_url: str,
    gate_name: str,
) -> Path:
    """Rewrite a project to use a shared gate path and return that path."""
    shared_gate = ctx.state_dir / "gate" / gate_name
    shared_gate.mkdir(parents=True, exist_ok=True)
    write_project(
        ctx.config_root,
        project_id,
        git_project_yaml(
            project_id, upstream_url=upstream_url, default_branch=None, gate_path=shared_gate
        ),
    )
    return shared_gate


def test_sync_project_gate_ssh_requires_config() -> None:
    """SSH upstreams require project SSH config before syncing the gate."""
    project_id = "proj6"
    with (
        project_env(
            git_project_yaml(
                project_id, upstream_url="git@github.com:org/repo.git", default_branch=None
            ),
            project_id=project_id,
            with_config_file=True,
        ),
        pytest.raises(SystemExit),
    ):
        make_git_gate(load_project(project_id)).sync()


def test_sync_project_gate_https_clone() -> None:
    """Initial gate sync performs a mirror clone with git env overrides."""
    project_id = "proj7"
    with project_env(
        git_project_yaml(project_id, default_branch=None), project_id=project_id
    ) as ctx:
        gate_dir = ctx.base / "sandbox-state" / "gate" / f"{project_id}.git"

        def run_side_effect(cmd: list[str], **kwargs: object) -> MagicMock:
            if cmd[:3] == ["git", "clone", "--mirror"]:
                gate_dir.mkdir(parents=True, exist_ok=True)
                return git_result()
            if cmd[:4] == ["git", "-C", str(gate_dir), "remote"]:
                return git_result(stdout="Fetching origin\n")
            return git_result(returncode=1)

        with patch(
            "terok_sandbox.gate.mirror.subprocess.run", side_effect=run_side_effect
        ) as run_mock:
            result = make_git_gate(load_project(project_id)).sync()

    assert result == {
        "path": str(gate_dir),
        "upstream_url": TEST_UPSTREAM_URL,
        "created": True,
        "success": True,
        "updated_branches": ["all"],
        "errors": [],
        "cache_refreshed": True,
    }
    clone_call = next(
        call for call in run_mock.call_args_list if call.args[0][:3] == ["git", "clone", "--mirror"]
    )
    assert "env" in clone_call.kwargs


@pytest.mark.parametrize(
    ("with_gate", "run_result", "expected"),
    [
        pytest.param(False, None, None, id="no-gate"),
        pytest.param(
            True,
            git_result(
                stdout="abc123def456\x002023-01-01 12:00:00 +0000\x00John Doe\x00Test commit message\n"
            ),
            {
                "commit_hash": "abc123def456",
                "commit_date": "2023-01-01 12:00:00 +0000",
                "commit_author": "John Doe",
                "commit_message": "Test commit message",
            },
            id="with-gate",
        ),
    ],
)
def test_last_commit(with_gate: bool, run_result: MagicMock | None, expected: dict | None) -> None:
    """Last-commit lookup returns commit metadata when the gate exists."""
    project_id = "proj-last-commit"
    with project_env(
        git_project_yaml(project_id, default_branch=None),
        project_id=project_id,
        with_gate=with_gate,
    ):
        gate = make_git_gate(load_project(project_id))
        if run_result is None:
            assert gate.last_commit() is None
        else:
            with patch("terok_sandbox.gate.mirror.subprocess.run", return_value=run_result):
                assert gate.last_commit() == expected


@pytest.mark.parametrize(
    ("project_id", "yaml_text", "branch", "run_behavior", "expected"),
    [
        pytest.param(
            "proj-upstream-ok",
            git_project_yaml("proj-upstream-ok"),
            None,
            {"return_value": git_result(stdout="abc123def456789\trefs/heads/main\n")},
            {
                "commit_hash": "abc123def456789",
                "ref_name": "refs/heads/main",
                "upstream_url": TEST_UPSTREAM_URL,
            },
            id="success",
        ),
        pytest.param(
            "proj-upstream-none",
            "project:\n  id: proj-upstream-none\n",
            None,
            None,
            None,
            id="no-upstream-url",
        ),
        pytest.param(
            "proj-upstream-fail",
            git_project_yaml("proj-upstream-fail"),
            None,
            {
                "return_value": git_result(
                    returncode=128, stderr="fatal: could not read from remote repository"
                )
            },
            None,
            id="network-failure",
        ),
        pytest.param(
            "proj-upstream-empty",
            git_project_yaml("proj-upstream-empty"),
            None,
            {"return_value": git_result(stdout="")},
            None,
            id="branch-not-found",
        ),
        pytest.param(
            "proj-upstream-timeout",
            git_project_yaml("proj-upstream-timeout"),
            None,
            {"side_effect": subprocess.TimeoutExpired("git", 30)},
            None,
            id="timeout",
        ),
        pytest.param(
            "proj-upstream-custom",
            git_project_yaml("proj-upstream-custom"),
            "develop",
            {"return_value": git_result(stdout="fedcba987654321\trefs/heads/develop\n")},
            {
                "commit_hash": "fedcba987654321",
                "ref_name": "refs/heads/develop",
                "upstream_url": TEST_UPSTREAM_URL,
            },
            id="custom-branch",
        ),
    ],
)
def test_get_upstream_head(
    project_id: str,
    yaml_text: str,
    branch: str | None,
    run_behavior: dict[str, object] | None,
    expected: dict | None,
) -> None:
    """Upstream HEAD lookup handles success, missing config, and network failures."""
    with project_env(yaml_text, project_id=project_id):
        project = load_project(project_id)
        if project.upstream_url is None or run_behavior is None:
            assert expected is None
        else:
            effective_branch = branch or project.default_branch
            with patch("terok_sandbox.gate.mirror.subprocess.run", **run_behavior):
                assert _get_upstream_head(project.upstream_url, effective_branch, {}) == expected


@pytest.mark.parametrize(
    ("with_gate", "branch", "run_result", "expected"),
    [
        pytest.param(
            True, None, git_result(stdout="abc123def456789\n"), "abc123def456789", id="success"
        ),
        pytest.param(False, None, None, None, id="no-gate"),
        pytest.param(
            True,
            "nonexistent",
            git_result(returncode=128, stderr="fatal: ref does not exist"),
            None,
            id="branch-not-found",
        ),
    ],
)
def test_get_gate_branch_head(
    with_gate: bool,
    branch: str | None,
    run_result: MagicMock | None,
    expected: str | None,
) -> None:
    """Gate branch HEAD lookup returns the branch commit or ``None``."""
    project_id = "proj-gate-head"
    with project_env(git_project_yaml(project_id), project_id=project_id, with_gate=with_gate):
        project = load_project(project_id)
        effective_branch = branch or project.default_branch
        if run_result is None:
            assert _get_gate_branch_head(project.gate_path, effective_branch, {}) is None
        else:
            with patch("terok_sandbox.gate.mirror.subprocess.run", return_value=run_result):
                assert _get_gate_branch_head(project.gate_path, effective_branch, {}) == expected


@pytest.mark.parametrize(
    ("with_gate", "gate_head", "upstream_info", "count_behind", "count_ahead", "expected"),
    [
        pytest.param(
            True,
            "abc123def456789",
            {
                "commit_hash": "abc123def456789",
                "ref_name": "refs/heads/main",
                "upstream_url": TEST_UPSTREAM_URL,
            },
            None,
            None,
            {
                "gate_head": "abc123def456789",
                "upstream_head": "abc123def456789",
                "is_stale": False,
                "commits_behind": 0,
                "commits_ahead": 0,
                "error": None,
            },
            id="in-sync",
        ),
        pytest.param(
            True,
            "old123",
            {
                "commit_hash": "new456",
                "ref_name": "refs/heads/main",
                "upstream_url": TEST_UPSTREAM_URL,
            },
            5,
            0,
            {
                "gate_head": "old123",
                "upstream_head": "new456",
                "is_stale": True,
                "commits_behind": 5,
                "commits_ahead": 0,
                "error": None,
            },
            id="stale",
        ),
        pytest.param(
            False,
            None,
            None,
            None,
            None,
            {
                "gate_head": None,
                "upstream_head": None,
                "is_stale": False,
                "commits_behind": None,
                "commits_ahead": None,
                "error": "Gate not initialized",
            },
            id="gate-not-initialized",
        ),
        pytest.param(
            True,
            "abc123",
            None,
            None,
            None,
            {
                "gate_head": "abc123",
                "upstream_head": None,
                "is_stale": False,
                "commits_behind": None,
                "commits_ahead": None,
                "error": "Could not reach upstream",
            },
            id="upstream-unreachable",
        ),
        pytest.param(
            True,
            "old123",
            {
                "commit_hash": "new456",
                "ref_name": "refs/heads/main",
                "upstream_url": TEST_UPSTREAM_URL,
            },
            None,
            None,
            {
                "gate_head": "old123",
                "upstream_head": "new456",
                "is_stale": True,
                "commits_behind": None,
                "commits_ahead": None,
                "error": None,
            },
            id="stale-count-unavailable",
        ),
    ],
)
def test_compare_gate_vs_upstream(
    with_gate: bool,
    gate_head: str | None,
    upstream_info: dict | None,
    count_behind: int | None,
    count_ahead: int | None,
    expected: dict[str, object],
) -> None:
    """Gate staleness comparison reports sync, stale, and error states."""
    project_id = "proj-compare"

    def _range_side_effect(_gate_dir, from_ref, to_ref, _env):
        """Return behind or ahead count depending on ref order."""
        if gate_head and upstream_info:
            if from_ref == gate_head and to_ref == upstream_info["commit_hash"]:
                return count_behind
            if from_ref == upstream_info["commit_hash"] and to_ref == gate_head:
                return count_ahead
        return None

    with project_env(git_project_yaml(project_id), project_id=project_id, with_gate=with_gate):
        with (
            patch("terok_sandbox.gate.mirror._get_gate_branch_head", return_value=gate_head),
            patch("terok_sandbox.gate.mirror._get_upstream_head", return_value=upstream_info),
            patch(
                "terok_sandbox.gate.mirror._count_commits_range",
                side_effect=_range_side_effect,
            ),
        ):
            result = make_git_gate(load_project(project_id)).compare_vs_upstream()

    assert result.branch == "main"
    for key, value in expected.items():
        assert getattr(result, key) == value


@pytest.mark.parametrize(
    ("with_gate", "run_behavior", "branches", "expected"),
    [
        pytest.param(
            True,
            {"return_value": git_result(stdout="Fetching origin\n")},
            None,
            {"success": True, "updated_branches": ["all"], "errors": []},
            id="success",
        ),
        pytest.param(
            False,
            None,
            None,
            {"success": False, "updated_branches": [], "errors": ["Gate not initialized"]},
            id="gate-not-initialized",
        ),
        pytest.param(
            True,
            {"return_value": git_result(returncode=1, stderr="fatal: could not fetch origin")},
            None,
            {
                "success": False,
                "updated_branches": [],
                "errors": ["remote update failed: fatal: could not fetch origin"],
            },
            id="network-failure",
        ),
        pytest.param(
            True,
            {"side_effect": subprocess.TimeoutExpired("git", 120)},
            None,
            {"success": False, "updated_branches": [], "errors": ["Sync timed out"]},
            id="timeout",
        ),
        pytest.param(
            True,
            {"return_value": git_result(stdout="Fetching origin\n")},
            ["main", "develop"],
            {"success": True, "updated_branches": ["main", "develop"], "errors": []},
            id="specific-branches",
        ),
    ],
)
def test_sync_branches(
    with_gate: bool,
    run_behavior: dict[str, object] | None,
    branches: list[str] | None,
    expected: dict[str, object],
) -> None:
    """Branch sync reports success, initialization problems, and fetch failures."""
    project_id = "proj-sync-branches"
    with project_env(git_project_yaml(project_id), project_id=project_id, with_gate=with_gate):
        gate = make_git_gate(load_project(project_id))
        if run_behavior is None:
            assert gate.sync_branches(branches) == expected
        else:
            with patch("terok_sandbox.gate.mirror.subprocess.run", **run_behavior):
                assert gate.sync_branches(branches) == expected


def test_sync_branches_rejects_mismatched_upstream() -> None:
    """Branch sync refuses a shared gate that points at a different upstream URL."""
    with project_env(
        git_project_yaml(
            "existing-proj",
            upstream_url="https://github.com/org/existing-repo.git",
            default_branch=None,
        ),
        project_id="existing-proj",
    ) as ctx:
        shared_gate = configure_shared_gate(
            ctx, "existing-proj", "https://github.com/org/existing-repo.git", "sync-conflict.git"
        )
        write_project(
            ctx.config_root,
            "new-proj",
            git_project_yaml(
                "new-proj",
                upstream_url="https://github.com/org/different-repo.git",
                default_branch=None,
                gate_path=shared_gate,
            ),
        )

        with pytest.raises(SystemExit, match="Gate path conflict"):
            make_git_gate(load_project("new-proj")).sync_branches()


def test_find_projects_sharing_gate() -> None:
    """Projects using the same gate path can be discovered while excluding one project."""
    with project_env(
        git_project_yaml(
            "proj-a", upstream_url="https://github.com/org/repo.git", default_branch=None
        ),
        project_id="proj-a",
    ) as ctx:
        shared_gate = configure_shared_gate(
            ctx, "proj-a", "https://github.com/org/repo.git", "shared.git"
        )
        write_project(
            ctx.config_root,
            "proj-b",
            git_project_yaml(
                "proj-b",
                upstream_url="https://github.com/org/repo.git",
                default_branch=None,
                gate_path=shared_gate,
            ),
        )

        assert find_projects_sharing_gate(shared_gate, exclude_project="proj-a") == [
            ("proj-b", "https://github.com/org/repo.git")
        ]


def test_validate_gate_upstream_match_same_url() -> None:
    """Projects sharing a gate are allowed when they share the same upstream URL."""
    with project_env(
        git_project_yaml(
            "proj-same-a", upstream_url="https://github.com/org/repo.git", default_branch=None
        ),
        project_id="proj-same-a",
    ) as ctx:
        shared_gate = configure_shared_gate(
            ctx, "proj-same-a", "https://github.com/org/repo.git", "shared.git"
        )
        write_project(
            ctx.config_root,
            "proj-same-b",
            git_project_yaml(
                "proj-same-b",
                upstream_url="https://github.com/org/repo.git",
                default_branch=None,
                gate_path=shared_gate,
            ),
        )

        validate_gate_upstream_match("proj-same-a")
        validate_gate_upstream_match("proj-same-b")


def test_validate_gate_upstream_match_different_url_fails() -> None:
    """Projects sharing a gate but pointing upstream elsewhere raise a helpful error."""
    with project_env(
        git_project_yaml(
            "proj-conflict-a", upstream_url="https://github.com/org/repo-A.git", default_branch=None
        ),
        project_id="proj-conflict-a",
    ) as ctx:
        shared_gate = configure_shared_gate(
            ctx, "proj-conflict-a", "https://github.com/org/repo-A.git", "conflict.git"
        )
        write_project(
            ctx.config_root,
            "proj-conflict-b",
            git_project_yaml(
                "proj-conflict-b",
                upstream_url="https://github.com/org/repo-B.git",
                default_branch=None,
                gate_path=shared_gate,
            ),
        )

        with pytest.raises(SystemExit) as exc_info:
            validate_gate_upstream_match("proj-conflict-a")

    error = str(exc_info.value)
    assert "Gate path conflict detected" in error
    assert "proj-conflict-a" in error
    assert "proj-conflict-b" in error
    assert "repo-A.git" in error
    assert "repo-B.git" in error


def test_sync_project_gate_rejects_mismatched_upstream() -> None:
    """Full gate sync also refuses shared gates with different upstream URLs."""
    with project_env(
        git_project_yaml(
            "existing-proj",
            upstream_url="https://github.com/org/existing-repo.git",
            default_branch=None,
        ),
        project_id="existing-proj",
    ) as ctx:
        shared_gate = configure_shared_gate(
            ctx, "existing-proj", "https://github.com/org/existing-repo.git", "init-conflict.git"
        )
        write_project(
            ctx.config_root,
            "new-proj",
            git_project_yaml(
                "new-proj",
                upstream_url="https://github.com/org/different-repo.git",
                default_branch=None,
                gate_path=shared_gate,
            ),
        )

        with pytest.raises(SystemExit, match="Gate path conflict"):
            make_git_gate(load_project("new-proj")).sync()
