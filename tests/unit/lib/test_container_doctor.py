# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for in-container health check orchestration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from terok_sandbox import ExecResult
from terok_sandbox.doctor import CheckVerdict, DoctorCheck

from terok.lib.orchestration.container_doctor import (
    _exec_in_container,
    _git_identity_check,
    _git_remote_check,
    _port_drift_check,
    _read_desired_shield_state,
    run_container_doctor,
)
from tests.testfs import MOCK_BASE

MOCK_TASK_DIR = MOCK_BASE / "projects" / "proj" / "tasks" / "42"


class TestExecInContainer:
    """Low-level sandbox exec helper."""

    def test_delegates_to_runtime_exec(self, mock_runtime) -> None:
        mock_runtime.exec.return_value = ExecResult(exit_code=0, stdout="ok\n", stderr="")
        result = _exec_in_container("proj-cli-42", ["echo", "hello"])
        assert result.exit_code == 0
        mock_runtime.container.assert_any_call("proj-cli-42")
        mock_runtime.exec.assert_called_once()
        args, kwargs = mock_runtime.exec.call_args
        assert args[1] == ["echo", "hello"]
        assert kwargs == {"timeout": 10}


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

    def test_error_when_port_mismatch(self) -> None:
        check = _git_remote_check("gatekeeping", 9418)
        verdict = check.evaluate(0, "http://abc@host.containers.internal:5555/proj.git\n", "")
        assert verdict.severity == "error"
        assert "5555" in verdict.detail
        assert "re-allocated" in verdict.detail

    def test_ok_when_gate_port_none(self) -> None:
        check = _git_remote_check("gatekeeping", None)
        verdict = check.evaluate(0, "http://abc@host.containers.internal:5555/proj.git\n", "")
        assert verdict.severity == "ok"


class TestPortDriftCheck:
    """Port drift detection for re-allocated ports."""

    def test_ok_when_ports_match(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        assert check.evaluate(0, "18700\n", "").severity == "ok"

    def test_error_when_ports_differ(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        verdict = check.evaluate(0, "18731\n", "")
        assert verdict.severity == "error"
        assert "re-allocated" in verdict.detail

    def test_ok_when_env_not_set(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        assert check.evaluate(1, "", "").severity == "ok"

    def test_warn_when_env_not_numeric(self) -> None:
        check = _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Proxy", 18700)
        assert check.evaluate(0, "not-a-number\n", "").severity == "warn"


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
        mock_meta_dir.return_value = MOCK_BASE / "nonexistent"
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

    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.container_doctor.tasks_meta_dir")
    def test_skips_non_running(
        self,
        mock_meta_dir: MagicMock,
        mock_load: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        (tmp_path / "42.yml").write_text("mode: cli\nname: test\n")
        mock_meta_dir.return_value = tmp_path
        mock_load.return_value = ({"mode": "cli"}, tmp_path / "42.yml")
        mock_runtime.container.return_value.state = "exited"
        results = run_container_doctor("proj", "42")
        assert results[0][0] == "info"
        assert "not running" in results[0][2]

    @patch("terok.lib.orchestration.container_doctor._exec_in_container")
    @patch("terok.lib.orchestration.container_doctor._terok_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.agent_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.sandbox_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.get_roster")
    @patch("terok.lib.orchestration.container_doctor._read_desired_shield_state")
    @patch("terok.lib.orchestration.container_doctor.load_project")
    @patch("terok.lib.orchestration.container_doctor.get_ssh_signer_port")
    @patch("terok.lib.orchestration.container_doctor.get_token_broker_port")
    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.container_doctor.tasks_meta_dir")
    def test_running_container_executes_probes(
        self,
        mock_meta_dir: MagicMock,
        mock_load_meta: MagicMock,
        mock_sandbox_cfg: MagicMock,
        mock_broker_port: MagicMock,
        mock_ssh_port: MagicMock,
        mock_load_project: MagicMock,
        mock_shield_state: MagicMock,
        mock_roster: MagicMock,
        mock_sandbox_checks: MagicMock,
        mock_agent_checks: MagicMock,
        mock_terok_checks: MagicMock,
        mock_exec: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        # Arrange: task metadata exists and container is running
        (tmp_path / "42.yml").write_text("mode: cli\n")
        mock_meta_dir.return_value = tmp_path
        mock_load_meta.return_value = ({"mode": "cli"}, tmp_path / "42.yml")
        mock_runtime.container.return_value.state = "running"
        mock_sandbox_cfg.return_value = MagicMock()
        mock_broker_port.return_value = 8080
        mock_ssh_port.return_value = 2222
        mock_shield_state.return_value = None
        mock_roster.return_value = MagicMock()

        fake_project = MagicMock()
        fake_project.tasks_root = MOCK_BASE / "projects" / "proj" / "tasks"
        mock_load_project.return_value = fake_project

        # Create simple container-side checks
        ok_check = DoctorCheck(
            category="network",
            label="Test TCP",
            probe_cmd=["echo", "ok"],
            evaluate=lambda rc, out, err: CheckVerdict("ok", "reachable"),
        )
        mock_sandbox_checks.return_value = [ok_check]
        mock_agent_checks.return_value = []
        mock_terok_checks.return_value = []

        mock_exec.return_value = ExecResult(exit_code=0, stdout="ok\n", stderr="")

        # Act
        results = run_container_doctor("proj", "42")

        # Assert — probe was executed and result collected
        assert len(results) >= 1
        assert results[0] == ("ok", "Test TCP", "reachable")
        mock_exec.assert_called_once()

    @patch("terok.lib.orchestration.container_doctor._exec_in_container")
    @patch("terok.lib.orchestration.container_doctor._terok_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.agent_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.sandbox_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.get_roster")
    @patch("terok.lib.orchestration.container_doctor._read_desired_shield_state")
    @patch("terok.lib.orchestration.container_doctor.load_project")
    @patch("terok.lib.orchestration.container_doctor.get_ssh_signer_port")
    @patch("terok.lib.orchestration.container_doctor.get_token_broker_port")
    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.container_doctor.tasks_meta_dir")
    def test_fix_application(
        self,
        mock_meta_dir: MagicMock,
        mock_load_meta: MagicMock,
        mock_sandbox_cfg: MagicMock,
        mock_broker_port: MagicMock,
        mock_ssh_port: MagicMock,
        mock_load_project: MagicMock,
        mock_shield_state: MagicMock,
        mock_roster: MagicMock,
        mock_sandbox_checks: MagicMock,
        mock_agent_checks: MagicMock,
        mock_terok_checks: MagicMock,
        mock_exec: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        # Arrange
        (tmp_path / "42.yml").write_text("mode: cli\n")
        mock_meta_dir.return_value = tmp_path
        mock_load_meta.return_value = ({"mode": "cli"}, tmp_path / "42.yml")
        mock_runtime.container.return_value.state = "running"
        mock_sandbox_cfg.return_value = MagicMock()
        mock_broker_port.return_value = 8080
        mock_ssh_port.return_value = 2222
        mock_shield_state.return_value = None
        mock_roster.return_value = MagicMock()

        fake_project = MagicMock()
        fake_project.tasks_root = MOCK_BASE / "projects" / "proj" / "tasks"
        mock_load_project.return_value = fake_project

        # A check that fails but is fixable
        fixable_check = DoctorCheck(
            category="git",
            label="Git user.name",
            probe_cmd=["git", "config", "user.name"],
            evaluate=lambda rc, out, err: CheckVerdict(
                "warn", "git user.name: wrong", fixable=True
            ),
            fix_cmd=["git", "config", "user.name", "Correct"],
            fix_description="Set git user.name to 'Correct'.",
        )
        mock_sandbox_checks.return_value = []
        mock_agent_checks.return_value = []
        mock_terok_checks.return_value = [fixable_check]

        # First call is probe (returns mismatch), second is fix (succeeds)
        mock_exec.side_effect = [
            ExecResult(exit_code=0, stdout="Wrong\n", stderr=""),
            ExecResult(exit_code=0, stdout="", stderr=""),
        ]

        # Act
        results = run_container_doctor("proj", "42", fix=True)

        # Assert — probe result + fix result
        assert len(results) == 2
        assert results[0][0] == "warn"
        assert results[1][0] == "ok"
        assert "fix:" in results[1][1]
        assert mock_exec.call_count == 2

    @patch("terok.lib.orchestration.container_doctor._exec_in_container")
    @patch("terok.lib.orchestration.container_doctor._terok_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.agent_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.sandbox_doctor_checks")
    @patch("terok.lib.orchestration.container_doctor.get_roster")
    @patch("terok.lib.orchestration.container_doctor._read_desired_shield_state")
    @patch("terok.lib.orchestration.container_doctor.load_project")
    @patch("terok.lib.orchestration.container_doctor.get_ssh_signer_port")
    @patch("terok.lib.orchestration.container_doctor.get_token_broker_port")
    @patch("terok.lib.orchestration.container_doctor.make_sandbox_config")
    @patch("terok.lib.orchestration.container_doctor.load_task_meta")
    @patch("terok.lib.orchestration.container_doctor.tasks_meta_dir")
    def test_host_side_unknown_check_skipped(
        self,
        mock_meta_dir: MagicMock,
        mock_load_meta: MagicMock,
        mock_sandbox_cfg: MagicMock,
        mock_broker_port: MagicMock,
        mock_ssh_port: MagicMock,
        mock_load_project: MagicMock,
        mock_shield_state: MagicMock,
        mock_roster: MagicMock,
        mock_sandbox_checks: MagicMock,
        mock_agent_checks: MagicMock,
        mock_terok_checks: MagicMock,
        mock_exec: MagicMock,
        tmp_path: Path,
        mock_runtime,
    ) -> None:
        # Arrange
        (tmp_path / "42.yml").write_text("mode: cli\n")
        mock_meta_dir.return_value = tmp_path
        mock_load_meta.return_value = ({"mode": "cli"}, tmp_path / "42.yml")
        mock_runtime.container.return_value.state = "running"
        mock_sandbox_cfg.return_value = MagicMock()
        mock_broker_port.return_value = 8080
        mock_ssh_port.return_value = 2222
        mock_shield_state.return_value = None
        mock_roster.return_value = MagicMock()

        fake_project = MagicMock()
        fake_project.tasks_root = MOCK_BASE / "projects" / "proj" / "tasks"
        mock_load_project.return_value = fake_project

        # An unknown host-side check
        unknown_host_check = DoctorCheck(
            category="future",
            label="Future check",
            probe_cmd=[],
            evaluate=lambda rc, out, err: CheckVerdict("ok", "unused"),
            host_side=True,
        )
        mock_sandbox_checks.return_value = [unknown_host_check]
        mock_agent_checks.return_value = []
        mock_terok_checks.return_value = []

        # Act
        results = run_container_doctor("proj", "42")

        # Assert — unknown host-side check is skipped with warning
        assert len(results) == 1
        assert results[0][0] == "warn"
        assert "unknown host-side check" in results[0][2]
        mock_exec.assert_not_called()


class TestStreamingGrouping:
    """Verify that the streaming path partitions checks by heading correctly."""

    def test_group_key_maps_credentials_and_tokens(self) -> None:
        """Labels inside known prefixes collapse to their heading; others pass through."""
        from terok.lib.orchestration.container_doctor import _group_key

        cred = DoctorCheck(
            category="mount",
            label="Credential file (claude)",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        phantom = DoctorCheck(
            category="env",
            label="Phantom token (GH_TOKEN)",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        base_url = DoctorCheck(
            category="env",
            label="Base URL (OPENAI_BASE_URL)",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        shield = DoctorCheck(
            category="shield",
            label="Shield state",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
        )
        assert _group_key(cred)[0] == "Credential files"
        assert _group_key(phantom)[0] == "Phantom tokens"
        assert _group_key(base_url)[0] == "Base URLs"
        # Shield has no mapping — streams individually
        assert _group_key(shield)[0] is None

    def test_network_category_collapses_disjoint_contributors(
        self,
        mock_runtime,
        tmp_path: Path,
    ) -> None:
        """Network checks from two layers (sandbox + terok) share one heading.

        Without the grouping that partitions *then* emits, a category
        contributed to by non-consecutive layers would produce two
        separate "Port drift" heading lines.
        """
        from io import StringIO

        from terok.lib.orchestration.container_doctor import (
            run_container_doctor,
        )
        from terok.lib.util.check_reporter import CheckReporter

        (tmp_path / "42.yml").write_text("mode: cli\n")

        net_a = DoctorCheck(
            category="network",
            label="Token broker (TCP)",
            probe_cmd=["true"],
            evaluate=lambda *a: CheckVerdict("ok", "reachable"),
        )
        shield_check = DoctorCheck(
            category="shield",
            label="Shield state",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
            host_side=True,
        )
        net_b = DoctorCheck(
            category="network",
            label="Token broker port drift",
            probe_cmd=["true"],
            evaluate=lambda *a: CheckVerdict("ok", "matches"),
        )

        buf = StringIO()
        reporter = CheckReporter(stream=buf)

        with (
            patch(
                "terok.lib.orchestration.container_doctor.sandbox_doctor_checks",
                return_value=[net_a, shield_check],
            ),
            patch(
                "terok.lib.orchestration.container_doctor.agent_doctor_checks",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor._terok_doctor_checks",
                return_value=[net_b],
            ),
            patch(
                "terok.lib.orchestration.container_doctor.tasks_meta_dir",
                return_value=tmp_path,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_task_meta",
                return_value=({"mode": "cli"}, tmp_path / "42.yml"),
            ),
            patch(
                "terok.lib.orchestration.container_doctor.make_sandbox_config",
                return_value=MagicMock(),
            ),
            patch(
                "terok.lib.orchestration.container_doctor.get_token_broker_port",
                return_value=8080,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.get_ssh_signer_port",
                return_value=2222,
            ),
            patch(
                "terok.lib.orchestration.container_doctor._read_desired_shield_state",
                return_value=None,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_project",
                return_value=MagicMock(tasks_root=tmp_path),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_shield_state",
                return_value=("ok", "Shield state", "not managed"),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._exec_in_container",
                return_value=ExecResult(exit_code=0, stdout="", stderr=""),
            ),
        ):
            mock_runtime.container.return_value.state = "running"
            run_container_doctor("proj", "42", reporter=reporter)

        out = buf.getvalue()
        # Single "Port drift" heading, both network checks counted under it.
        assert out.count("Port drift") == 1
        assert "ok (2 checks)" in out
        # Shield state streams individually between the group members (it's
        # the second check), and must still appear with its own line.
        assert "Shield state" in out

    def test_legacy_callers_still_receive_list(
        self,
        mock_runtime,
        tmp_path: Path,
    ) -> None:
        """Calling without a reporter keeps the historical return shape."""
        from terok.lib.orchestration.container_doctor import (
            run_container_doctor,
        )

        (tmp_path / "42.yml").write_text("mode: cli\n")

        only_check = DoctorCheck(
            category="shield",
            label="Shield state",
            probe_cmd=[],
            evaluate=lambda *a: CheckVerdict("ok", ""),
            host_side=True,
        )
        with (
            patch(
                "terok.lib.orchestration.container_doctor.sandbox_doctor_checks",
                return_value=[only_check],
            ),
            patch(
                "terok.lib.orchestration.container_doctor.agent_doctor_checks",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor._terok_doctor_checks",
                return_value=[],
            ),
            patch(
                "terok.lib.orchestration.container_doctor.tasks_meta_dir",
                return_value=tmp_path,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_task_meta",
                return_value=({"mode": "cli"}, tmp_path / "42.yml"),
            ),
            patch(
                "terok.lib.orchestration.container_doctor.make_sandbox_config",
                return_value=MagicMock(),
            ),
            patch(
                "terok.lib.orchestration.container_doctor.get_token_broker_port",
                return_value=8080,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.get_ssh_signer_port",
                return_value=2222,
            ),
            patch(
                "terok.lib.orchestration.container_doctor._read_desired_shield_state",
                return_value=None,
            ),
            patch(
                "terok.lib.orchestration.container_doctor.load_project",
                return_value=MagicMock(tasks_root=tmp_path),
            ),
            patch(
                "terok.lib.orchestration.container_doctor._check_shield_state",
                return_value=("ok", "Shield state", "not managed"),
            ),
        ):
            mock_runtime.container.return_value.state = "running"
            results = run_container_doctor("proj", "42")

        # Legacy path returns the accumulated list; streaming path would
        # have returned an empty list.
        assert results == [("ok", "Shield state", "not managed")]
