# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for in-container health check orchestration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from terok.lib.orchestration.container_doctor import (
    _exec_in_container,
    _git_identity_check,
    _git_remote_check,
    _read_desired_shield_state,
    run_container_doctor,
)
from tests.testfs import MOCK_BASE

MOCK_TASK_DIR = MOCK_BASE / "projects" / "proj" / "tasks" / "42"


class TestExecInContainer:
    """Low-level podman exec helper."""

    def test_calls_podman_exec(self) -> None:
        with patch("terok.lib.orchestration.container_doctor.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0, stdout="ok\n", stderr=""
            )
            result = _exec_in_container("proj-cli-42", ["echo", "hello"])
            assert result.returncode == 0
            cmd = mock_run.call_args[0][0]
            assert cmd == ["podman", "exec", "proj-cli-42", "echo", "hello"]


class TestGitIdentityCheck:
    """Git identity field verification."""

    def test_ok_when_matching(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        verdict = check.evaluate(0, "Dev User\n", "")
        assert verdict.severity == "ok"

    def test_warn_when_mismatched(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        verdict = check.evaluate(0, "Wrong Name\n", "")
        assert verdict.severity == "warn"
        assert verdict.fixable is True

    def test_warn_when_unset(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        verdict = check.evaluate(1, "", "")
        assert verdict.severity == "warn"

    def test_email_field(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "email")
        verdict = check.evaluate(0, "dev@example.com\n", "")
        assert verdict.severity == "ok"

    def test_email_mismatch(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "email")
        verdict = check.evaluate(0, "wrong@example.com\n", "")
        assert verdict.severity == "warn"
        assert verdict.fixable is True

    def test_has_fix_cmd(self) -> None:
        check = _git_identity_check("Dev User", "dev@example.com", "name")
        assert check.fix_cmd is not None
        assert "Dev User" in check.fix_cmd


class TestGitRemoteCheck:
    """Git remote URL verification."""

    def test_ok_for_gate_url(self) -> None:
        check = _git_remote_check("gatekeeping", 9418)
        verdict = check.evaluate(0, "http://abc123@host.containers.internal:9418/proj.git\n", "")
        assert verdict.severity == "ok"

    def test_error_when_gate_bypassed(self) -> None:
        check = _git_remote_check("gatekeeping", 9418)
        verdict = check.evaluate(0, "git@github.com:org/repo.git\n", "")
        assert verdict.severity == "error"

    def test_ok_for_online_any_url(self) -> None:
        check = _git_remote_check("online", None)
        verdict = check.evaluate(0, "git@github.com:org/repo.git\n", "")
        assert verdict.severity == "ok"

    def test_warn_when_no_remote(self) -> None:
        check = _git_remote_check("gatekeeping", 9418)
        verdict = check.evaluate(1, "", "fatal: no remote")
        assert verdict.severity == "warn"


class TestReadDesiredShieldState:
    """Shield desired state file reading."""

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        assert _read_desired_shield_state(tmp_path) is None

    def test_reads_state(self, tmp_path: Path) -> None:
        (tmp_path / "shield_desired_state").write_text("up\n")
        assert _read_desired_shield_state(tmp_path) == "up"

    def test_strips_whitespace(self, tmp_path: Path) -> None:
        (tmp_path / "shield_desired_state").write_text("  down_all  \n")
        assert _read_desired_shield_state(tmp_path) == "down_all"


class TestRunContainerDoctor:
    """Orchestrator integration tests."""

    @patch("terok.lib.orchestration.container_doctor.tasks_meta_dir")
    def test_returns_warn_for_missing_metadata(self, mock_meta_dir: MagicMock) -> None:
        mock_meta_dir.return_value = Path("/nonexistent")
        results = run_container_doctor("proj", "99")
        assert len(results) == 1
        assert results[0][0] == "warn"
        assert "metadata not found" in results[0][2]

    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.container_doctor.tasks_meta_dir")
    def test_returns_warn_for_never_started(
        self, mock_meta_dir: MagicMock, mock_load: MagicMock, tmp_path: Path
    ) -> None:
        (tmp_path / "42.yml").write_text("name: test\n")
        mock_meta_dir.return_value = tmp_path
        mock_load.return_value = ({}, tmp_path / "42.yml")
        results = run_container_doctor("proj", "42")
        assert results[0][0] == "warn"
        assert "never started" in results[0][2]

    @patch("terok.lib.orchestration.container_doctor.get_container_state")
    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.container_doctor.tasks_meta_dir")
    def test_skips_non_running(
        self,
        mock_meta_dir: MagicMock,
        mock_load: MagicMock,
        mock_state: MagicMock,
        tmp_path: Path,
    ) -> None:
        (tmp_path / "42.yml").write_text("mode: cli\nname: test\n")
        mock_meta_dir.return_value = tmp_path
        mock_load.return_value = ({"mode": "cli"}, tmp_path / "42.yml")
        mock_state.return_value = "exited"
        results = run_container_doctor("proj", "42")
        assert results[0][0] == "info"
        assert "not running" in results[0][2]
