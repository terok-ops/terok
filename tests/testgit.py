# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared git helpers for integration-style tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

TEST_GIT_EMAIL = "test@example.test"
"""Email used for synthetic commits in test repositories."""

TEST_GIT_NAME = "terok test"
"""Author name used for synthetic commits in test repositories."""


def clean_git_env(temp_dir: Path | None = None) -> dict[str, str]:
    """Return an environment with git-related user config isolated."""
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("GIT_"):
            del env[key]
    if temp_dir is not None:
        empty_config = temp_dir / ".gitconfig-empty"
        empty_config.touch()
        env["GIT_CONFIG_GLOBAL"] = str(empty_config)
        env["GIT_CONFIG_SYSTEM"] = str(empty_config)
    else:
        env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def run_git(
    *args: str,
    repo_path: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run ``git`` with the standard isolated test environment."""
    command = ["git", *([] if repo_path is None else ["-C", str(repo_path)]), *args]
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
        env=env if env is not None else clean_git_env(),
    )


def file_repo_url(path: Path) -> str:
    """Build a ``file://`` repository URL for ``path``."""
    return f"file://{path}"


def create_bare_repo_with_branches(
    repo_path: Path,
    default_branch: str,
    other_branches: list[str],
) -> None:
    """Create a bare git repository whose HEAD points to ``default_branch``."""
    with tempfile.TemporaryDirectory() as work_dir:
        work_path = Path(work_dir)
        git_env = clean_git_env(work_path)

        run_git("init", "-b", default_branch, str(work_path), env=git_env)
        run_git("config", "user.email", TEST_GIT_EMAIL, repo_path=work_path, env=git_env)
        run_git("config", "user.name", TEST_GIT_NAME, repo_path=work_path, env=git_env)

        (work_path / "README.md").write_text(f"# {default_branch}\n", encoding="utf-8")
        run_git("add", ".", repo_path=work_path, env=git_env)
        run_git(
            "commit",
            "-m",
            f"Initial commit on {default_branch}",
            repo_path=work_path,
            env=git_env,
        )

        for branch in other_branches:
            run_git("checkout", "-b", branch, repo_path=work_path, env=git_env)
            (work_path / "README.md").write_text(f"# {branch}\n", encoding="utf-8")
            run_git("add", ".", repo_path=work_path, env=git_env)
            run_git("commit", "-m", f"Commit on {branch}", repo_path=work_path, env=git_env)

        run_git("checkout", default_branch, repo_path=work_path, env=git_env)
        run_git("clone", "--bare", str(work_path), str(repo_path), env=git_env)
        run_git(
            "symbolic-ref",
            "HEAD",
            f"refs/heads/{default_branch}",
            repo_path=repo_path,
            env=git_env,
        )


def append_commit_to_bare_repo(
    repo_path: Path,
    branch: str,
    filename: str,
    content: str,
    message: str,
) -> str:
    """Append a commit to ``branch`` in a bare repo and return the new HEAD."""
    with tempfile.TemporaryDirectory() as work_dir:
        base_path = Path(work_dir)
        work_path = base_path / "work"
        git_env = clean_git_env(base_path)

        run_git("clone", str(repo_path), str(work_path), env=git_env)
        run_git("config", "user.email", TEST_GIT_EMAIL, repo_path=work_path, env=git_env)
        run_git("config", "user.name", TEST_GIT_NAME, repo_path=work_path, env=git_env)
        run_git("checkout", branch, repo_path=work_path, env=git_env)

        (work_path / filename).write_text(content, encoding="utf-8")
        run_git("add", filename, repo_path=work_path, env=git_env)
        run_git("commit", "-m", message, repo_path=work_path, env=git_env)
        run_git("push", "origin", branch, repo_path=work_path, env=git_env)
        return git_head(work_path)


def git_head(repo_path: Path, ref: str = "HEAD") -> str:
    """Return the commit hash for ``ref`` in ``repo_path``."""
    return run_git("rev-parse", ref, repo_path=repo_path).stdout.strip()


def current_branch(repo_path: Path) -> str:
    """Return the currently checked out branch name."""
    return run_git("rev-parse", "--abbrev-ref", "HEAD", repo_path=repo_path).stdout.strip()


def read_repo_file(repo_path: Path, filename: str) -> str:
    """Return text content of ``filename`` inside ``repo_path``."""
    return (repo_path / filename).read_text(encoding="utf-8").strip()


def remote_url(repo_path: Path, remote: str = "origin") -> str:
    """Return the configured URL for ``remote``."""
    return run_git("remote", "get-url", remote, repo_path=repo_path).stdout.strip()
