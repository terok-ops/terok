# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Tests for init-ssh-and-repo.sh branch selection behavior.

These tests verify that the init script correctly checks out the branch
specified in GIT_BRANCH rather than the remote's default HEAD.

This is an integration-style test that creates real git repos to test the
actual shell script behavior.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


def get_init_script_path() -> Path:
    """Get the path to init-ssh-and-repo.sh."""
    return (
        Path(__file__).parent.parent.parent
        / "src"
        / "terok"
        / "resources"
        / "scripts"
        / "init-ssh-and-repo.sh"
    )


def get_clean_git_env(temp_dir: Path | None = None) -> dict:
    """Get an environment dict with git-related variables cleaned.

    This prevents interference from the workspace git config when running tests.

    Args:
        temp_dir: Optional temp dir for git config. If not provided, uses empty config.
    """
    env = os.environ.copy()
    # Remove any git-related env vars that could cause interference
    for key in list(env.keys()):
        if key.startswith("GIT_"):
            del env[key]
    # Disable global/system config by pointing to empty files
    if temp_dir:
        empty_config = temp_dir / ".gitconfig-empty"
        empty_config.touch()
        env["GIT_CONFIG_GLOBAL"] = str(empty_config)
        env["GIT_CONFIG_SYSTEM"] = str(empty_config)
    else:
        # Use HOME to isolate git config
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def create_bare_repo_with_branches(
    repo_path: Path, default_branch: str, other_branches: list[str]
) -> None:
    """Create a bare git repo with multiple branches.

    Args:
        repo_path: Path where to create the bare repo
        default_branch: The branch that HEAD will point to (remote's default)
        other_branches: Additional branches to create
    """
    git_env = get_clean_git_env()

    # Create a temp working repo first
    with tempfile.TemporaryDirectory() as work_dir:
        work_path = Path(work_dir)

        # Initialize with default branch
        subprocess.run(
            ["git", "init", "-b", default_branch, str(work_path)],
            check=True,
            capture_output=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(work_path), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
            env=git_env,
        )
        subprocess.run(
            ["git", "-C", str(work_path), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
            env=git_env,
        )

        # Create initial commit on default branch
        (work_path / "README.md").write_text(f"# {default_branch}\n")
        subprocess.run(
            ["git", "-C", str(work_path), "add", "."], check=True, capture_output=True, env=git_env
        )
        subprocess.run(
            ["git", "-C", str(work_path), "commit", "-m", f"Initial commit on {default_branch}"],
            check=True,
            capture_output=True,
            env=git_env,
        )

        # Create other branches with unique commits
        for branch in other_branches:
            subprocess.run(
                ["git", "-C", str(work_path), "checkout", "-b", branch],
                check=True,
                capture_output=True,
                env=git_env,
            )
            (work_path / "README.md").write_text(f"# {branch}\n")
            subprocess.run(
                ["git", "-C", str(work_path), "add", "."],
                check=True,
                capture_output=True,
                env=git_env,
            )
            subprocess.run(
                ["git", "-C", str(work_path), "commit", "-m", f"Commit on {branch}"],
                check=True,
                capture_output=True,
                env=git_env,
            )

        # Switch back to default branch (so HEAD points to it in the bare clone)
        subprocess.run(
            ["git", "-C", str(work_path), "checkout", default_branch],
            check=True,
            capture_output=True,
            env=git_env,
        )

        # Clone to bare repo
        subprocess.run(
            ["git", "clone", "--bare", str(work_path), str(repo_path)],
            check=True,
            capture_output=True,
            env=git_env,
        )

        # Explicitly set HEAD to the default branch in the bare repo
        # (git clone --bare doesn't always do this correctly)
        subprocess.run(
            ["git", "-C", str(repo_path), "symbolic-ref", "HEAD", f"refs/heads/{default_branch}"],
            check=True,
            capture_output=True,
            env=git_env,
        )


def get_current_branch(repo_path: Path) -> str:
    """Get the current branch of a git repo."""
    result = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
        env=get_clean_git_env(),
    )
    return result.stdout.strip()


def get_file_content(repo_path: Path, filename: str) -> str:
    """Get content of a file in the repo."""
    return (repo_path / filename).read_text().strip()


def run_init_script(
    init_script: Path, base_path: Path, extra_env: dict
) -> subprocess.CompletedProcess:
    """Run the init script with a clean environment."""
    # Start with minimal environment to avoid pollution from test runner
    env = {
        "HOME": str(base_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": os.environ.get("TERM", "xterm"),
        # Disable git's global/system config
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    # Add the extra env vars from the test
    env.update(extra_env)
    # Ensure CLONE_FROM is not set unless explicitly provided
    if "CLONE_FROM" not in extra_env:
        env.pop("CLONE_FROM", None)

    return subprocess.run(
        ["bash", str(init_script)],
        env=env,
        capture_output=True,
        text=True,
        cwd=str(base_path),
    )


class InitScriptBranchSelectionTests(unittest.TestCase):
    """Test that init-ssh-and-repo.sh respects GIT_BRANCH setting."""

    def setUp(self) -> None:
        self.init_script = get_init_script_path()
        if not self.init_script.exists():
            self.skipTest(f"Init script not found at {self.init_script}")

    def test_initial_clone_uses_git_branch_not_remote_default(self) -> None:
        """Test that initial clone checks out GIT_BRANCH, not the remote's default HEAD.

        This is the core bug fix test. Before the fix, cloning from a remote
        (especially file:// URLs in gatekeeping mode) would leave the workspace
        on whatever branch HEAD pointed to in the remote, ignoring GIT_BRANCH.
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()

            # Create gate with 'master' as default HEAD but 'dev' as target branch
            create_bare_repo_with_branches(
                gate_path,
                default_branch="master",  # Remote's HEAD points here
                other_branches=["dev", "feature"],  # We want to checkout 'dev'
            )

            # Run init script with GIT_BRANCH=dev (simulates gatekeeping mode)
            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",
                    "GIT_BRANCH": "dev",  # Should checkout this, NOT master
                    "REPO_ROOT": str(workspace_path),
                },
            )

            # Script should succeed
            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            # Workspace should be on 'dev', not 'master'
            current_branch = get_current_branch(workspace_path)
            self.assertEqual(
                current_branch,
                "dev",
                f"Expected branch 'dev' but got '{current_branch}'. "
                f"Script should use GIT_BRANCH, not remote's default HEAD.\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}",
            )

            # Verify we have the dev branch content
            content = get_file_content(workspace_path, "README.md")
            self.assertEqual(content, "# dev")

    def test_initial_clone_falls_back_to_cloned_default_if_branch_missing(self) -> None:
        """Test fallback to cloned default branch when GIT_BRANCH doesn't exist."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()

            # Create gate with only 'main' branch
            create_bare_repo_with_branches(gate_path, default_branch="main", other_branches=[])

            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",
                    "GIT_BRANCH": "nonexistent",  # Branch doesn't exist
                    "REPO_ROOT": str(workspace_path),
                },
            )

            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            # Should warn about missing branch
            self.assertIn("WARNING", result.stdout)
            self.assertIn("nonexistent", result.stdout)

            # Should stay on the cloned default branch (main) since nonexistent doesn't exist
            current_branch = get_current_branch(workspace_path)
            self.assertEqual(current_branch, "main")

    def test_initial_clone_fails_if_workspace_not_empty(self) -> None:
        """Initial clone should fail fast when workspace is pre-populated."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()
            (workspace_path / "stray.txt").write_text("unexpected")

            create_bare_repo_with_branches(gate_path, default_branch="master", other_branches=[])

            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",
                    "GIT_BRANCH": "master",
                    "REPO_ROOT": str(workspace_path),
                },
            )

            self.assertNotEqual(result.returncode, 0, "Script should fail on non-empty workspace")
            combined = f"{result.stdout}\n{result.stderr}"
            self.assertIn("is not empty before initial clone", combined)
            self.assertNotIn("branch master not found", combined)

    def test_initial_clone_removes_marker_and_succeeds(self) -> None:
        """Initial clone should remove the new-task marker using normal init flow."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()
            marker_path = workspace_path / ".new-task-marker"
            marker_path.write_text("marker")

            create_bare_repo_with_branches(gate_path, default_branch="master", other_branches=[])

            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",
                    "GIT_BRANCH": "master",
                    "REPO_ROOT": str(workspace_path),
                },
            )

            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")
            self.assertFalse(marker_path.exists(), "Marker should be removed by init script")
            # Clone should succeed and workspace should become a git checkout.
            self.assertTrue((workspace_path / ".git").exists())

    def test_initial_clone_uses_remote_default_if_git_branch_unset(self) -> None:
        """Test that remote's default branch is used when GIT_BRANCH is not set."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()

            # Create gate with 'master' as default and 'main' also exists
            create_bare_repo_with_branches(
                gate_path,
                default_branch="master",  # Remote HEAD
                other_branches=["main"],
            )

            # GIT_BRANCH not set - should clone remote's default HEAD
            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",
                    "REPO_ROOT": str(workspace_path),
                },
            )

            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            # Should be on 'master' (the remote's default HEAD)
            current_branch = get_current_branch(workspace_path)
            self.assertEqual(current_branch, "master")

    def test_online_mode_with_clone_from_uses_git_branch(self) -> None:
        """Test online mode: clone from gate, repoint to upstream, use GIT_BRANCH.

        In online mode with a gate, CLONE_FROM is set to the gate and CODE_REPO
        is the upstream URL. The script should repoint origin and then checkout
        the branch specified in GIT_BRANCH.
        """
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            upstream_path = base / "upstream.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()

            # Create gate (stale, on master)
            create_bare_repo_with_branches(
                gate_path, default_branch="master", other_branches=["dev"]
            )

            # Create upstream (has same branches)
            create_bare_repo_with_branches(
                upstream_path, default_branch="master", other_branches=["dev"]
            )

            result = run_init_script(
                self.init_script,
                base,
                {
                    "CLONE_FROM": f"file://{gate_path}",
                    "CODE_REPO": f"file://{upstream_path}",
                    "GIT_BRANCH": "dev",
                    "REPO_ROOT": str(workspace_path),
                },
            )

            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            # Should be on 'dev'
            current_branch = get_current_branch(workspace_path)
            self.assertEqual(current_branch, "dev")

            # Origin should point to upstream, not gate
            origin_url = subprocess.run(
                ["git", "-C", str(workspace_path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=True,
                env=get_clean_git_env(),
            ).stdout.strip()
            self.assertEqual(origin_url, f"file://{upstream_path}")

    def test_new_task_marker_resets_to_git_branch(self) -> None:
        """Test that new task marker triggers reset to GIT_BRANCH."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()

            # Create gate with multiple branches
            create_bare_repo_with_branches(
                gate_path, default_branch="master", other_branches=["dev", "feature"]
            )

            # First, clone normally (will be on master since that's gate's HEAD)
            subprocess.run(
                ["git", "clone", str(gate_path), str(workspace_path)],
                check=True,
                capture_output=True,
                env=get_clean_git_env(),
            )

            # Verify we're on master initially
            self.assertEqual(get_current_branch(workspace_path), "master")

            # Create the new task marker (simulates terokctl task new)
            marker_path = workspace_path / ".new-task-marker"
            marker_path.write_text("marker")

            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",
                    "GIT_BRANCH": "dev",  # Task should reset to this
                    "REPO_ROOT": str(workspace_path),
                },
            )

            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            # Should now be on 'dev' after reset
            current_branch = get_current_branch(workspace_path)
            self.assertEqual(current_branch, "dev")

            # Marker should be removed
            self.assertFalse(marker_path.exists())

    def test_restarted_task_preserves_local_branch(self) -> None:
        """Test that restarted task (no marker) preserves local state."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()

            git_env = get_clean_git_env()

            # Create gate
            create_bare_repo_with_branches(
                gate_path, default_branch="master", other_branches=["dev"]
            )

            # Clone and switch to dev manually
            subprocess.run(
                ["git", "clone", str(gate_path), str(workspace_path)],
                check=True,
                capture_output=True,
                env=git_env,
            )
            subprocess.run(
                ["git", "-C", str(workspace_path), "checkout", "dev"],
                check=True,
                capture_output=True,
                env=git_env,
            )

            # Make a local change
            (workspace_path / "local.txt").write_text("local change")
            subprocess.run(
                ["git", "-C", str(workspace_path), "add", "."],
                check=True,
                capture_output=True,
                env=git_env,
            )

            # No marker - this is a restart
            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",
                    "GIT_BRANCH": "master",  # Different from current branch
                    "REPO_ROOT": str(workspace_path),
                    "GIT_RESET_MODE": "none",  # Default
                },
            )

            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            # Should still be on 'dev' (preserved)
            current_branch = get_current_branch(workspace_path)
            self.assertEqual(current_branch, "dev")

            # Local file should still exist
            self.assertTrue((workspace_path / "local.txt").exists())


class GatekeepingModeOriginTests(unittest.TestCase):
    """Test that gatekeeping mode correctly sets origin to gate."""

    def setUp(self) -> None:
        self.init_script = get_init_script_path()
        if not self.init_script.exists():
            self.skipTest(f"Init script not found at {self.init_script}")

    def test_gatekeeping_mode_fixes_origin_to_gate(self) -> None:
        """Test that origin is always set to gate in gatekeeping mode (file:// URL)."""
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            gate_path = base / "gate.git"
            upstream_path = base / "upstream.git"
            workspace_path = base / "workspace"
            workspace_path.mkdir()

            git_env = get_clean_git_env()

            # Create both gate and upstream
            create_bare_repo_with_branches(gate_path, "main", [])
            create_bare_repo_with_branches(upstream_path, "main", [])

            # Clone from upstream first (simulates workspace that was in online mode)
            subprocess.run(
                ["git", "clone", str(upstream_path), str(workspace_path)],
                check=True,
                capture_output=True,
                env=git_env,
            )

            # Verify origin points to upstream
            origin_before = subprocess.run(
                ["git", "-C", str(workspace_path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=True,
                env=git_env,
            ).stdout.strip()
            self.assertIn("upstream", origin_before)

            # Run init in gatekeeping mode (file:// URL = gatekeeping)
            result = run_init_script(
                self.init_script,
                base,
                {
                    "CODE_REPO": f"file://{gate_path}",  # file:// triggers gatekeeping mode
                    "GIT_BRANCH": "main",
                    "REPO_ROOT": str(workspace_path),
                },
            )

            self.assertEqual(result.returncode, 0, f"Script failed: {result.stderr}")

            # Origin should now point to gate
            origin_after = subprocess.run(
                ["git", "-C", str(workspace_path), "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                check=True,
                env=git_env,
            ).stdout.strip()
            self.assertEqual(origin_after, f"file://{gate_path}")


if __name__ == "__main__":
    unittest.main()
