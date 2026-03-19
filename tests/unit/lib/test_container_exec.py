# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for container-based command execution."""

import subprocess
import unittest.mock

from terok.lib.containers.container_exec import container_git_diff


class TestContainerGitDiff:
    """Tests for container_git_diff."""

    def test_running_container_returns_diff(self) -> None:
        """Diff output returned when the container is already running."""
        expected = "diff --git a/f.txt b/f.txt\n+hello\n"
        with (
            unittest.mock.patch(
                "terok.lib.containers.container_exec.get_container_state",
                return_value="running",
            ),
            unittest.mock.patch("terok.lib.containers.container_exec.subprocess.run") as mock_run,
        ):
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 0
            mock_result.stdout = expected
            mock_run.return_value = mock_result

            result = container_git_diff("proj", "1", "cli", "HEAD")
            assert result == expected

            # Should NOT have called podman start
            calls = [c[0][0] for c in mock_run.call_args_list]
            assert all("start" not in cmd for cmd in calls)

    def test_stopped_container_restarts_and_stops(self) -> None:
        """Stopped CLI container is started, exec'd, and stopped again."""
        expected = " 1 file changed\n"
        with (
            unittest.mock.patch(
                "terok.lib.containers.container_exec.get_container_state",
                return_value="exited",
            ),
            unittest.mock.patch("terok.lib.containers.container_exec.subprocess.run") as mock_run,
        ):
            # First call: podman start (success)
            # Second call: podman exec git diff (success)
            # Third call: podman stop (cleanup)
            start_result = unittest.mock.Mock()
            start_result.returncode = 0
            exec_result = unittest.mock.Mock()
            exec_result.returncode = 0
            exec_result.stdout = expected
            stop_result = unittest.mock.Mock()
            mock_run.side_effect = [start_result, exec_result, stop_result]

            result = container_git_diff("proj", "2", "cli", "--stat", "HEAD@{1}..HEAD")
            assert result == expected
            assert mock_run.call_count == 3

            # Verify start was called
            start_cmd = mock_run.call_args_list[0][0][0]
            assert start_cmd == ["podman", "start", "proj-cli-2"]

            # Verify exec was called with correct git args
            exec_cmd = mock_run.call_args_list[1][0][0]
            assert exec_cmd == [
                "podman",
                "exec",
                "proj-cli-2",
                "git",
                "-C",
                "/workspace",
                "diff",
                "--stat",
                "HEAD@{1}..HEAD",
            ]

            # Verify stop was called with correct container name
            stop_cmd = mock_run.call_args_list[2][0][0]
            assert stop_cmd[:2] == ["podman", "stop"]
            assert "proj-cli-2" in stop_cmd

    def test_exited_headless_container_not_restarted(self) -> None:
        """Exited headless (run mode) containers must not be restarted."""
        with unittest.mock.patch(
            "terok.lib.containers.container_exec.get_container_state",
            return_value="exited",
        ):
            result = container_git_diff("proj", "1", "run", "--stat", "HEAD@{1}..HEAD")
            assert result is None

    def test_no_container_returns_none(self) -> None:
        """Return None when the container does not exist."""
        with unittest.mock.patch(
            "terok.lib.containers.container_exec.get_container_state",
            return_value=None,
        ):
            result = container_git_diff("proj", "99", "cli")
            assert result is None

    def test_podman_exec_failure_returns_none(self) -> None:
        """Return None when git diff fails inside the container."""
        with (
            unittest.mock.patch(
                "terok.lib.containers.container_exec.get_container_state",
                return_value="running",
            ),
            unittest.mock.patch("terok.lib.containers.container_exec.subprocess.run") as mock_run,
        ):
            mock_result = unittest.mock.Mock()
            mock_result.returncode = 128
            mock_run.return_value = mock_result

            result = container_git_diff("proj", "1", "cli", "HEAD")
            assert result is None

    def test_start_failure_returns_none(self) -> None:
        """Return None when podman start fails on a stopped container."""
        with (
            unittest.mock.patch(
                "terok.lib.containers.container_exec.get_container_state",
                return_value="exited",
            ),
            unittest.mock.patch(
                "terok.lib.containers.container_exec.subprocess.run",
                side_effect=subprocess.CalledProcessError(1, "podman start"),
            ),
        ):
            result = container_git_diff("proj", "1", "run")
            assert result is None

    def test_timeout_returns_none(self) -> None:
        """Return None on subprocess timeout."""
        with (
            unittest.mock.patch(
                "terok.lib.containers.container_exec.get_container_state",
                return_value="running",
            ),
            unittest.mock.patch(
                "terok.lib.containers.container_exec.subprocess.run",
                side_effect=subprocess.TimeoutExpired("podman", 30),
            ),
        ):
            result = container_git_diff("proj", "1", "cli", "HEAD")
            assert result is None
