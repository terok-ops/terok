# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""E2E tests for git identity inside real terok containers.

Builds real images (L0 + shell-init wiring), starts one shared container,
then verifies that ``BASH_ENV``, ``_terok_apply_git_identity``, and all four
authorship modes work correctly — including actual ``git commit`` metadata.
"""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Iterator

import pytest

pytestmark = pytest.mark.needs_podman

# ── Constants ────────────────────────────────────────────────────────────────

CONTAINER_PREFIX = "terok-itest-git"

HUMAN_NAME = "Alice Human"
HUMAN_EMAIL = "alice@example.com"
AGENT_NAME = "Claude"
AGENT_EMAIL = "noreply@anthropic.com"

# Expected (author_name, author_email, committer_name, committer_email) per mode.
EXPECTED_IDENTITY: dict[str, tuple[str, str, str, str]] = {
    "agent-human": (AGENT_NAME, AGENT_EMAIL, HUMAN_NAME, HUMAN_EMAIL),
    "human-agent": (HUMAN_NAME, HUMAN_EMAIL, AGENT_NAME, AGENT_EMAIL),
    "agent": (AGENT_NAME, AGENT_EMAIL, AGENT_NAME, AGENT_EMAIL),
    "human": (HUMAN_NAME, HUMAN_EMAIL, HUMAN_NAME, HUMAN_EMAIL),
}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _exec(
    container: str,
    *cmd: str,
    env: dict[str, str] | None = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Run a command inside *container*, optionally injecting env overrides."""
    env_args: list[str] = []
    for k, v in (env or {}).items():
        env_args.extend(["-e", f"{k}={v}"])
    return subprocess.run(
        ["podman", "exec", *env_args, container, *cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _bash(
    container: str,
    script: str,
    *,
    env: dict[str, str] | None = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Run a bash script inside *container* (sources ``BASH_ENV`` automatically)."""
    return _exec(container, "bash", "-c", script, env=env, timeout=timeout)


def _git_commit_and_log(container: str, mode: str) -> str:
    """Create an empty git commit inside *container* and return the identity log line.

    Returns ``author_name|author_email|committer_name|committer_email``.
    """
    script = (
        f'_terok_apply_git_identity "{AGENT_NAME}" "{AGENT_EMAIL}"\n'
        "dir=$(mktemp -d)\n"
        'cd "$dir"\n'
        "git init -q\n"
        "git commit --allow-empty -q -m test\n"
        "git log -1 --format='%an|%ae|%cn|%ce'\n"
    )
    result = _bash(container, script, env={"TEROK_GIT_AUTHORSHIP": mode})
    assert result.returncode == 0, (
        f"git commit failed for mode={mode}:\n  stdout: {result.stdout}\n  stderr: {result.stderr}"
    )
    return result.stdout.strip()


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="class")
def container(shell_test_image: str) -> Iterator[str]:
    """Start a long-lived container from the shell-init test image.

    Shared across all tests in the class; removed in the finalizer.
    """
    name = f"{CONTAINER_PREFIX}-{uuid.uuid4().hex[:8]}"
    # Clean up any leftover from a previous interrupted run.
    subprocess.run(["podman", "rm", "-f", name], capture_output=True, timeout=30)
    subprocess.run(
        [
            "podman",
            "run",
            "-d",
            "--name",
            name,
            "-e",
            f"HUMAN_GIT_NAME={HUMAN_NAME}",
            "-e",
            f"HUMAN_GIT_EMAIL={HUMAN_EMAIL}",
            "-e",
            "TEROK_GIT_AUTHORSHIP=agent-human",
            shell_test_image,
            "sleep",
            "300",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    yield name
    subprocess.run(["podman", "rm", "-f", name], capture_output=True, timeout=30)


# ── Tests ────────────────────────────────────────────────────────────────────


class TestGitIdentityContainer:
    """Verify git identity shell wiring works end-to-end inside a real container."""

    # -- Shell environment wiring --

    def test_bash_env_sources_terok_env(self, container: str) -> None:
        """Non-interactive ``bash -c`` loads terok-env.sh via BASH_ENV."""
        result = _bash(container, "echo ${_TEROK_ENV_LOADED:-}")
        assert result.returncode == 0
        assert result.stdout.strip() == "1"

    def test_identity_function_available_noninteractive(self, container: str) -> None:
        """``_terok_apply_git_identity`` is available in non-interactive bash."""
        result = _bash(container, "type _terok_apply_git_identity")
        assert result.returncode == 0
        assert "function" in result.stdout.lower()

    def test_human_env_vars_present(self, container: str) -> None:
        """``HUMAN_GIT_NAME`` and ``HUMAN_GIT_EMAIL`` are visible inside the container."""
        result = _bash(container, "echo $HUMAN_GIT_NAME/$HUMAN_GIT_EMAIL")
        assert result.returncode == 0
        assert result.stdout.strip() == f"{HUMAN_NAME}/{HUMAN_EMAIL}"

    # -- Authorship env var modes --

    @pytest.mark.parametrize("mode", ["agent-human", "human-agent", "agent", "human"])
    def test_authorship_modes(self, container: str, mode: str) -> None:
        """``_terok_apply_git_identity`` sets correct GIT_* vars for each mode."""
        script = (
            f'_terok_apply_git_identity "{AGENT_NAME}" "{AGENT_EMAIL}"\n'
            "echo $GIT_AUTHOR_NAME|$GIT_AUTHOR_EMAIL|$GIT_COMMITTER_NAME|$GIT_COMMITTER_EMAIL"
        )
        result = _bash(container, script, env={"TEROK_GIT_AUTHORSHIP": mode})
        assert result.returncode == 0

        an, ae, cn, ce = result.stdout.strip().split("|")
        expected = EXPECTED_IDENTITY[mode]
        assert (an, ae, cn, ce) == expected, f"mode={mode}: got {(an, ae, cn, ce)}"

    # -- Actual git commit identity --

    @pytest.mark.parametrize("mode", ["agent-human", "human-agent", "agent", "human"])
    def test_git_commit_identity(self, container: str, mode: str) -> None:
        """``git log`` shows the correct author/committer for each authorship mode."""
        log_line = _git_commit_and_log(container, mode)
        an, ae, cn, ce = log_line.split("|")
        expected = EXPECTED_IDENTITY[mode]
        assert (an, ae, cn, ce) == expected, f"mode={mode}: got {(an, ae, cn, ce)}"
