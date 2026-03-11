# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

import subprocess
import unittest
import unittest.mock

from terok.lib.core.projects import load_project
from terok.lib.security.git_gate import (
    GitGate,
    _get_gate_branch_head,
    _get_upstream_head,
    find_projects_sharing_gate,
    validate_gate_upstream_match,
)
from test_utils import project_env, write_project


class GitGateTests(unittest.TestCase):
    def test_sync_project_gate_ssh_requires_config(self) -> None:
        project_id = "proj6"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: git@github.com:org/repo.git
"""
        with (
            project_env(yaml, project_id=project_id, with_config_file=True),
            self.assertRaises(SystemExit),
        ):
            GitGate(load_project(project_id)).sync()

    def test_sync_project_gate_https_clone(self) -> None:
        project_id = "proj7"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
"""
        with project_env(yaml, project_id=project_id) as ctx:
            gate_dir = ctx.state_dir / "gate" / f"{project_id}.git"

            with unittest.mock.patch("terok.lib.security.git_gate.subprocess.run") as run_mock:
                config_result = unittest.mock.Mock()
                config_result.returncode = 1
                config_result.stdout = ""
                config_result.stderr = ""
                clone_result = unittest.mock.Mock()
                clone_result.returncode = 0
                sync_result = unittest.mock.Mock()
                sync_result.returncode = 0
                sync_result.stdout = "Fetching origin\n"
                sync_result.stderr = ""

                def _run_side_effect(*args: object, **kwargs: object) -> unittest.mock.Mock:
                    cmd = args[0]
                    if cmd[:3] == ["git", "clone", "--mirror"]:
                        gate_dir.mkdir(parents=True, exist_ok=True)
                        return clone_result
                    if cmd[:4] == ["git", "-C", str(gate_dir), "remote"]:
                        return sync_result
                    return config_result

                run_mock.side_effect = _run_side_effect
                result = GitGate(load_project(project_id)).sync()

            self.assertTrue(result["created"])
            self.assertTrue(result["success"])
            self.assertIn("path", result)
            self.assertEqual(result["upstream_url"], "https://example.com/repo.git")

            clone_call = None
            for call in run_mock.call_args_list:
                args, kwargs = call
                if args and args[0][:3] == ["git", "clone", "--mirror"]:
                    clone_call = call
                    break
            self.assertIsNotNone(clone_call)
            args, kwargs = clone_call
            self.assertIn("env", kwargs)

    def test_get_gate_last_commit_no_gate(self) -> None:
        """Test get_gate_last_commit when gate doesn't exist."""
        project_id = "proj8"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
"""
        with project_env(yaml, project_id=project_id):
            result = GitGate(load_project(project_id)).last_commit()
            self.assertIsNone(result)

    def test_get_gate_last_commit_with_gate(self) -> None:
        """Test get_gate_last_commit when gate exists."""
        project_id = "proj9"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            # Mock the git log command to return sample commit data
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = (
                "abc123def456\x002023-01-01 12:00:00 +0000\x00John Doe\x00Test commit message\n"
            )

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = GitGate(load_project(project_id)).last_commit()

            self.assertIsNotNone(result)
            self.assertEqual(result["commit_hash"], "abc123def456")
            self.assertEqual(result["commit_date"], "2023-01-01 12:00:00 +0000")
            self.assertEqual(result["commit_message"], "Test commit message")
            self.assertEqual(result["commit_author"], "John Doe")

    # Tests for get_upstream_head
    def test_get_upstream_head_success(self) -> None:
        """Test successful upstream head query."""
        project_id = "proj10"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            # Mock successful git ls-remote
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = "abc123def456789\trefs/heads/main\n"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = _get_upstream_head(load_project(project_id))

            self.assertIsNotNone(result)
            self.assertEqual(result["commit_hash"], "abc123def456789")
            self.assertEqual(result["ref_name"], "refs/heads/main")
            self.assertEqual(result["upstream_url"], "https://example.com/repo.git")

    def test_get_upstream_head_no_upstream_url(self) -> None:
        """Test get_upstream_head when project has no upstream URL."""
        project_id = "proj11"
        yaml = f"""\
project:
  id: {project_id}
"""
        with project_env(yaml, project_id=project_id):
            result = _get_upstream_head(load_project(project_id))
            self.assertIsNone(result)

    def test_get_upstream_head_network_failure(self) -> None:
        """Test get_upstream_head when network query fails."""
        project_id = "proj12"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            # Mock failed git ls-remote
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 128
            mock_result.stderr = "fatal: could not read from remote repository"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = _get_upstream_head(load_project(project_id))

            self.assertIsNone(result)

    def test_get_upstream_head_branch_not_found(self) -> None:
        """Test get_upstream_head when branch doesn't exist."""
        project_id = "proj13"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            # Mock empty output (branch not found)
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = ""

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = _get_upstream_head(load_project(project_id))

            self.assertIsNone(result)

    def test_get_upstream_head_timeout(self) -> None:
        """Test get_upstream_head when query times out."""
        project_id = "proj14"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            # Mock timeout
            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run",
                side_effect=subprocess.TimeoutExpired("git", 30),
            ):
                result = _get_upstream_head(load_project(project_id))

            self.assertIsNone(result)

    def test_get_upstream_head_custom_branch(self) -> None:
        """Test get_upstream_head with custom branch."""
        project_id = "proj15"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            # Mock successful git ls-remote for develop branch
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = "fedcba987654321\trefs/heads/develop\n"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = _get_upstream_head(load_project(project_id), branch="develop")

            self.assertIsNotNone(result)
            self.assertEqual(result["commit_hash"], "fedcba987654321")
            self.assertEqual(result["ref_name"], "refs/heads/develop")

    # Tests for get_gate_branch_head
    def test_get_gate_branch_head_success(self) -> None:
        """Test successful gate branch head query."""
        project_id = "proj16"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            # Mock git rev-parse
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = "abc123def456789\n"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = _get_gate_branch_head(load_project(project_id))

            self.assertEqual(result, "abc123def456789")

    def test_get_gate_branch_head_no_gate(self) -> None:
        """Test get_gate_branch_head when gate doesn't exist."""
        project_id = "proj17"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            result = _get_gate_branch_head(load_project(project_id))
            self.assertIsNone(result)

    def test_get_gate_branch_head_branch_not_found(self) -> None:
        """Test get_gate_branch_head when branch doesn't exist in gate."""
        project_id = "proj18"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            # Mock failed git rev-parse
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 128
            mock_result.stderr = "fatal: ref does not exist"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = _get_gate_branch_head(load_project(project_id), branch="nonexistent")

            self.assertIsNone(result)

    # Tests for compare_gate_vs_upstream
    def test_compare_gate_vs_upstream_in_sync(self) -> None:
        """Test compare when gate and upstream are in sync."""
        project_id = "proj19"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            commit_hash = "abc123def456789"

            # Mock get_gate_branch_head
            with unittest.mock.patch(
                "terok.lib.security.git_gate._get_gate_branch_head", return_value=commit_hash
            ):
                # Mock get_upstream_head
                with unittest.mock.patch(
                    "terok.lib.security.git_gate._get_upstream_head",
                    return_value={
                        "commit_hash": commit_hash,
                        "ref_name": "refs/heads/main",
                        "upstream_url": "https://example.com/repo.git",
                    },
                ):
                    result = GitGate(load_project(project_id)).compare_vs_upstream()

            self.assertEqual(result.branch, "main")
            self.assertEqual(result.gate_head, commit_hash)
            self.assertEqual(result.upstream_head, commit_hash)
            self.assertFalse(result.is_stale)
            self.assertEqual(result.commits_behind, 0)
            self.assertIsNone(result.error)

    def test_compare_gate_vs_upstream_stale(self) -> None:
        """Test compare when gate is stale."""
        project_id = "proj20"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            gate_hash = "old123"
            upstream_hash = "new456"

            # Mock get_gate_branch_head
            with unittest.mock.patch(
                "terok.lib.security.git_gate._get_gate_branch_head", return_value=gate_hash
            ):
                # Mock get_upstream_head
                with unittest.mock.patch(
                    "terok.lib.security.git_gate._get_upstream_head",
                    return_value={
                        "commit_hash": upstream_hash,
                        "ref_name": "refs/heads/main",
                        "upstream_url": "https://example.com/repo.git",
                    },
                ):
                    # Mock _count_commits_behind and _count_commits_ahead
                    with unittest.mock.patch(
                        "terok.lib.security.git_gate._count_commits_behind", return_value=5
                    ):
                        with unittest.mock.patch(
                            "terok.lib.security.git_gate._count_commits_ahead", return_value=0
                        ):
                            result = GitGate(load_project(project_id)).compare_vs_upstream()

            self.assertEqual(result.branch, "main")
            self.assertEqual(result.gate_head, gate_hash)
            self.assertEqual(result.upstream_head, upstream_hash)
            self.assertTrue(result.is_stale)
            self.assertEqual(result.commits_behind, 5)
            self.assertIsNone(result.error)

    def test_compare_gate_vs_upstream_gate_not_initialized(self) -> None:
        """Test compare when gate is not initialized."""
        project_id = "proj21"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            # Mock get_gate_branch_head to return None
            with unittest.mock.patch(
                "terok.lib.security.git_gate._get_gate_branch_head", return_value=None
            ):
                result = GitGate(load_project(project_id)).compare_vs_upstream()

            self.assertEqual(result.branch, "main")
            self.assertIsNone(result.gate_head)
            self.assertIsNone(result.upstream_head)
            self.assertFalse(result.is_stale)
            self.assertIsNone(result.commits_behind)
            self.assertEqual(result.error, "Gate not initialized")

    def test_compare_gate_vs_upstream_upstream_unreachable(self) -> None:
        """Test compare when upstream is unreachable."""
        project_id = "proj22"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            gate_hash = "abc123"

            # Mock get_gate_branch_head
            with unittest.mock.patch(
                "terok.lib.security.git_gate._get_gate_branch_head", return_value=gate_hash
            ):
                # Mock get_upstream_head to return None
                with unittest.mock.patch(
                    "terok.lib.security.git_gate._get_upstream_head", return_value=None
                ):
                    result = GitGate(load_project(project_id)).compare_vs_upstream()

            self.assertEqual(result.branch, "main")
            self.assertEqual(result.gate_head, gate_hash)
            self.assertIsNone(result.upstream_head)
            self.assertFalse(result.is_stale)
            self.assertIsNone(result.commits_behind)
            self.assertEqual(result.error, "Could not reach upstream")

    def test_compare_gate_vs_upstream_commits_behind_unavailable(self) -> None:
        """Test compare when commits behind cannot be determined."""
        project_id = "proj23"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            gate_hash = "old123"
            upstream_hash = "new456"

            # Mock get_gate_branch_head
            with unittest.mock.patch(
                "terok.lib.security.git_gate._get_gate_branch_head", return_value=gate_hash
            ):
                # Mock get_upstream_head
                with unittest.mock.patch(
                    "terok.lib.security.git_gate._get_upstream_head",
                    return_value={
                        "commit_hash": upstream_hash,
                        "ref_name": "refs/heads/main",
                        "upstream_url": "https://example.com/repo.git",
                    },
                ):
                    # Mock _count_commits_behind and _count_commits_ahead to return None
                    with unittest.mock.patch(
                        "terok.lib.security.git_gate._count_commits_behind", return_value=None
                    ):
                        with unittest.mock.patch(
                            "terok.lib.security.git_gate._count_commits_ahead", return_value=None
                        ):
                            result = GitGate(load_project(project_id)).compare_vs_upstream()

            self.assertTrue(result.is_stale)
            self.assertIsNone(result.commits_behind)

    # Tests for sync_gate_branches
    def test_sync_gate_branches_success(self) -> None:
        """Test successful sync of gate branches."""
        project_id = "proj24"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            # Mock successful git remote update
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = "Fetching origin\n"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = GitGate(load_project(project_id)).sync_branches()

            self.assertTrue(result["success"])
            self.assertEqual(result["updated_branches"], ["all"])
            self.assertEqual(result["errors"], [])

    def test_sync_gate_branches_gate_not_initialized(self) -> None:
        """Test sync when gate is not initialized."""
        project_id = "proj25"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id):
            result = GitGate(load_project(project_id)).sync_branches()

            self.assertFalse(result["success"])
            self.assertEqual(result["updated_branches"], [])
            self.assertEqual(result["errors"], ["Gate not initialized"])

    def test_sync_gate_branches_network_failure(self) -> None:
        """Test sync when network update fails."""
        project_id = "proj26"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            # Mock failed git remote update
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 1
            mock_result.stderr = "fatal: could not fetch origin"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = GitGate(load_project(project_id)).sync_branches()

            self.assertFalse(result["success"])
            self.assertIn("remote update failed", result["errors"][0])

    def test_sync_gate_branches_timeout(self) -> None:
        """Test sync when operation times out."""
        project_id = "proj27"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            # Mock timeout
            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run",
                side_effect=subprocess.TimeoutExpired("git", 120),
            ):
                result = GitGate(load_project(project_id)).sync_branches()

            self.assertFalse(result["success"])
            self.assertEqual(result["errors"], ["Sync timed out"])

    def test_sync_gate_branches_specific_branches(self) -> None:
        """Test sync with specific branches."""
        project_id = "proj28"
        yaml = f"""\
project:
  id: {project_id}
git:
  upstream_url: https://example.com/repo.git
  default_branch: main
"""
        with project_env(yaml, project_id=project_id, with_gate=True):
            # Mock successful git remote update
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = "Fetching origin\n"

            with unittest.mock.patch(
                "terok.lib.security.git_gate.subprocess.run", return_value=mock_result
            ):
                result = GitGate(load_project(project_id)).sync_branches(["main", "develop"])

            self.assertTrue(result["success"])
            self.assertEqual(result["updated_branches"], ["main", "develop"])
            self.assertEqual(result["errors"], [])

    def test_sync_gate_branches_rejects_mismatched_upstream(self) -> None:
        """Test sync_gate_branches refuses when another project uses gate with different upstream."""
        # Use project_env for env setup with the first project, then add the second
        yaml = """\
project:
  id: existing-proj
git:
  upstream_url: https://github.com/org/existing-repo.git
"""
        with project_env(yaml, project_id="existing-proj") as ctx:
            shared_gate = ctx.state_dir / "gate" / "sync-conflict.git"
            shared_gate.mkdir(parents=True, exist_ok=True)

            # Rewrite existing-proj with gate path
            write_project(
                ctx.config_root,
                "existing-proj",
                f"""\
project:
  id: existing-proj
git:
  upstream_url: https://github.com/org/existing-repo.git
gate:
  path: {shared_gate}
""",
            )

            # Create new project trying to use same gate with different upstream
            write_project(
                ctx.config_root,
                "new-proj",
                f"""\
project:
  id: new-proj
git:
  upstream_url: https://github.com/org/different-repo.git
gate:
  path: {shared_gate}
""",
            )

            with self.assertRaises(SystemExit) as exc_ctx:
                GitGate(load_project("new-proj")).sync_branches()

            error_msg = str(exc_ctx.exception)
            self.assertIn("Gate path conflict", error_msg)
            self.assertIn("existing-proj", error_msg)

    # Tests for gate sharing validation
    def test_find_projects_sharing_gate(self) -> None:
        """Test finding projects that share a gate path."""
        # Use project_env for env setup with the first project, then add the second
        yaml = """\
project:
  id: proj-a
git:
  upstream_url: https://github.com/org/repo.git
"""
        with project_env(yaml, project_id="proj-a") as ctx:
            shared_gate = ctx.state_dir / "gate" / "shared.git"

            # Rewrite proj-a with gate path
            write_project(
                ctx.config_root,
                "proj-a",
                f"""\
project:
  id: proj-a
git:
  upstream_url: https://github.com/org/repo.git
gate:
  path: {shared_gate}
""",
            )

            write_project(
                ctx.config_root,
                "proj-b",
                f"""\
project:
  id: proj-b
git:
  upstream_url: https://github.com/org/repo.git
gate:
  path: {shared_gate}
""",
            )

            # Find projects sharing the gate, excluding proj-a
            sharing = find_projects_sharing_gate(shared_gate, exclude_project="proj-a")

            self.assertEqual(len(sharing), 1)
            self.assertEqual(sharing[0][0], "proj-b")
            self.assertEqual(sharing[0][1], "https://github.com/org/repo.git")

    def test_validate_gate_upstream_match_same_url(self) -> None:
        """Test validation passes when projects share gate with same upstream."""
        yaml = """\
project:
  id: proj-same-a
git:
  upstream_url: https://github.com/org/repo.git
"""
        with project_env(yaml, project_id="proj-same-a") as ctx:
            shared_gate = ctx.state_dir / "gate" / "shared.git"

            # Rewrite proj-same-a with gate path
            write_project(
                ctx.config_root,
                "proj-same-a",
                f"""\
project:
  id: proj-same-a
git:
  upstream_url: https://github.com/org/repo.git
gate:
  path: {shared_gate}
""",
            )

            write_project(
                ctx.config_root,
                "proj-same-b",
                f"""\
project:
  id: proj-same-b
git:
  upstream_url: https://github.com/org/repo.git
gate:
  path: {shared_gate}
""",
            )

            # Should not raise - same upstream URL
            validate_gate_upstream_match("proj-same-a")
            validate_gate_upstream_match("proj-same-b")

    def test_validate_gate_upstream_match_different_url_fails(self) -> None:
        """Test validation fails when projects share gate with different upstreams."""
        yaml = """\
project:
  id: proj-conflict-a
git:
  upstream_url: https://github.com/org/repo-A.git
"""
        with project_env(yaml, project_id="proj-conflict-a") as ctx:
            shared_gate = ctx.state_dir / "gate" / "conflict.git"

            # Rewrite proj-conflict-a with gate path
            write_project(
                ctx.config_root,
                "proj-conflict-a",
                f"""\
project:
  id: proj-conflict-a
git:
  upstream_url: https://github.com/org/repo-A.git
gate:
  path: {shared_gate}
""",
            )

            write_project(
                ctx.config_root,
                "proj-conflict-b",
                f"""\
project:
  id: proj-conflict-b
git:
  upstream_url: https://github.com/org/repo-B.git
gate:
  path: {shared_gate}
""",
            )

            # Should raise SystemExit with helpful error message
            with self.assertRaises(SystemExit) as exc_ctx:
                validate_gate_upstream_match("proj-conflict-a")

            error_msg = str(exc_ctx.exception)
            self.assertIn("Gate path conflict detected", error_msg)
            self.assertIn("proj-conflict-a", error_msg)
            self.assertIn("proj-conflict-b", error_msg)
            self.assertIn("repo-A.git", error_msg)
            self.assertIn("repo-B.git", error_msg)

    def test_sync_project_gate_rejects_mismatched_upstream(self) -> None:
        """Test sync_project_gate refuses when another project uses gate with different upstream."""
        yaml = """\
project:
  id: existing-proj
git:
  upstream_url: https://github.com/org/existing-repo.git
"""
        with project_env(yaml, project_id="existing-proj") as ctx:
            shared_gate = ctx.state_dir / "gate" / "init-conflict.git"

            # Rewrite existing-proj with gate path
            write_project(
                ctx.config_root,
                "existing-proj",
                f"""\
project:
  id: existing-proj
git:
  upstream_url: https://github.com/org/existing-repo.git
gate:
  path: {shared_gate}
""",
            )

            # Create new project trying to use same gate with different upstream
            write_project(
                ctx.config_root,
                "new-proj",
                f"""\
project:
  id: new-proj
git:
  upstream_url: https://github.com/org/different-repo.git
gate:
  path: {shared_gate}
""",
            )

            with self.assertRaises(SystemExit) as exc_ctx:
                GitGate(load_project("new-proj")).sync()

            error_msg = str(exc_ctx.exception)
            self.assertIn("Gate path conflict", error_msg)
            self.assertIn("existing-proj", error_msg)
