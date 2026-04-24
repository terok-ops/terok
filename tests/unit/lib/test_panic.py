# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the emergency panic module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from terok.lib.domain.panic import (
    PanicResult,
    clear_panic_lock,
    execute_panic,
    format_panic_report,
    is_panicked,
    panic_stop_containers,
)
from tests.testfs import FAKE_PROJECT_TASKS_ROOT

_SHIELD = "terok.lib.domain.panic._raise_shield"
_VAULT = "terok.lib.domain.panic._stop_vault"
_GATE = "terok.lib.domain.panic._stop_gate"
_BYPASS = "terok.lib.domain.panic.get_shield_bypass_firewall_no_protection"
_DISCOVER = "terok.lib.domain.panic._discover_targets"
_LOCK = "terok.lib.domain.panic._write_panic_lock"
_STOP = "terok.lib.domain.panic._stop_containers"


def _target(project_id="proj1", task_id="1", mode="cli"):
    """Build a target tuple for testing."""
    cname = f"{project_id}-{mode}-{task_id}"
    return (project_id, task_id, mode, cname, FAKE_PROJECT_TASKS_ROOT / task_id)


def _task_meta(task_id, mode="cli"):
    """Build a fake TaskMeta."""
    m = MagicMock()
    m.task_id, m.mode = task_id, mode
    return m


class TestDiscovery:
    """Tests for _discover_targets."""

    @patch("terok.lib.domain.panic.list_projects")
    @patch("terok.lib.domain.panic.get_tasks")
    @patch("terok.lib.domain.panic.get_all_task_states")
    def test_finds_running_skips_exited(self, mock_states, mock_tasks, mock_projects):
        """Only running/paused containers are discovered."""
        cfg = MagicMock(id="proj1", tasks_root=FAKE_PROJECT_TASKS_ROOT)
        mock_projects.return_value = [cfg]
        mock_tasks.return_value = [_task_meta("1"), _task_meta("2", "web"), _task_meta("3", None)]
        mock_states.return_value = {"1": "running", "2": "exited", "3": None}

        from terok.lib.domain.panic import _discover_targets

        result = _discover_targets()
        assert len(result) == 1
        assert result[0][1] == "1"

    @patch("terok.lib.domain.panic.list_projects")
    @patch("terok.lib.domain.panic.get_tasks")
    def test_skips_broken_projects(self, mock_tasks, mock_projects):
        """Projects where get_tasks raises are skipped."""
        mock_projects.return_value = [MagicMock(id="broken")]
        mock_tasks.side_effect = Exception("boom")

        from terok.lib.domain.panic import _discover_targets

        assert _discover_targets() == []

    @patch("terok.lib.domain.panic.list_projects")
    @patch("terok.lib.domain.panic.get_tasks")
    def test_skips_projects_with_no_tasks(self, mock_tasks, mock_projects):
        """Projects with empty task list are skipped."""
        mock_projects.return_value = [MagicMock(id="empty")]
        mock_tasks.return_value = []

        from terok.lib.domain.panic import _discover_targets

        assert _discover_targets() == []

    @patch("terok.lib.domain.panic.list_projects")
    @patch("terok.lib.domain.panic.get_tasks")
    @patch("terok.lib.domain.panic.get_all_task_states")
    def test_includes_paused_containers(self, mock_states, mock_tasks, mock_projects):
        """Paused containers are also discovered."""
        cfg = MagicMock(id="proj1", tasks_root=FAKE_PROJECT_TASKS_ROOT)
        mock_projects.return_value = [cfg]
        mock_tasks.return_value = [_task_meta("1")]
        mock_states.return_value = {"1": "paused"}

        from terok.lib.domain.panic import _discover_targets

        assert len(_discover_targets()) == 1

    @patch("terok.lib.domain.panic.list_projects")
    @patch("terok.lib.domain.panic.get_tasks")
    @patch("terok.lib.domain.panic.get_all_task_states")
    def test_skips_broken_task_states(self, mock_states, mock_tasks, mock_projects):
        """Projects where get_all_task_states raises are skipped."""
        cfg = MagicMock(id="proj1", tasks_root=FAKE_PROJECT_TASKS_ROOT)
        mock_projects.return_value = [cfg]
        mock_tasks.return_value = [_task_meta("1")]
        mock_states.side_effect = Exception("state lookup failed")

        from terok.lib.domain.panic import _discover_targets

        assert _discover_targets() == []


class TestExecutePanic:
    """Tests for execute_panic."""

    @patch(_LOCK)
    @patch(_BYPASS, return_value=False)
    @patch(_DISCOVER)
    @patch(_SHIELD)
    @patch(_VAULT, return_value=(True, None))
    @patch(_GATE, return_value=(True, None))
    def test_success(self, _g, _p, mock_shield, mock_discover, _b, _l):
        """All operations succeed."""
        t = _target()
        mock_discover.return_value = [t]
        mock_shield.return_value = (t[3], None)

        r = execute_panic()

        assert r.shields_raised == [t[3]]
        assert r.vault_stopped and r.gate_stopped
        assert not r.has_errors

    @patch(_LOCK)
    @patch(_BYPASS, return_value=True)
    @patch(_DISCOVER, return_value=[_target()])
    @patch(_SHIELD)
    @patch(_VAULT, return_value=(True, None))
    @patch(_GATE, return_value=(True, None))
    def test_shield_bypass(self, _g, _p, mock_shield, _d, _b, _l):
        """Shield ops skipped when bypass active."""
        r = execute_panic()
        assert r.shield_bypassed and r.shields_raised == []
        mock_shield.assert_not_called()

    @patch(_LOCK)
    @patch(_BYPASS, return_value=False)
    @patch(_DISCOVER)
    @patch(_SHIELD)
    @patch(_VAULT, return_value=(True, None))
    @patch(_GATE, return_value=(False, "not running"))
    def test_partial_failure(self, _g, _p, mock_shield, mock_discover, _b, _l):
        """Some ops fail, others still succeed."""
        t1, t2 = _target(task_id="1"), _target(task_id="2")
        mock_discover.return_value = [t1, t2]
        mock_shield.side_effect = [(t1[3], None), (t2[3], "nftables failed")]

        r = execute_panic()

        assert len(r.shields_raised) == 1
        assert len(r.shield_errors) == 1
        assert r.has_errors

    @patch(_STOP, return_value=(["c1"], []))
    @patch(_LOCK)
    @patch(_BYPASS, return_value=False)
    @patch(_DISCOVER)
    @patch(_SHIELD)
    @patch(_VAULT, return_value=(True, None))
    @patch(_GATE, return_value=(True, None))
    def test_phase2_stop(self, _g, _p, mock_shield, mock_discover, _b, _l, _s):
        """Phase 2 container stop works when requested."""
        t = _target()
        mock_discover.return_value = [t]
        mock_shield.return_value = (t[3], None)

        r = execute_panic(stop_containers=True)
        assert r.containers_stopped == ["c1"]

    @patch(_STOP)
    @patch(_LOCK)
    @patch(_BYPASS, return_value=False)
    @patch(_DISCOVER, return_value=[])
    @patch(_VAULT, return_value=(True, None))
    @patch(_GATE, return_value=(True, None))
    def test_phase2_skipped_when_no_targets(self, _g, _p, _d, _b, _l, mock_stop):
        """Phase 2 skipped when no running containers."""
        r = execute_panic(stop_containers=True)
        mock_stop.assert_not_called()
        assert r.containers_stopped == []

    @patch(_LOCK)
    @patch(_BYPASS, return_value=False)
    @patch(_DISCOVER)
    @patch(_SHIELD, side_effect=Exception("thread crashed"))
    @patch(_VAULT, return_value=(True, None))
    @patch(_GATE, return_value=(True, None))
    def test_shield_future_exception(self, _g, _p, _shield, mock_discover, _b, _l):
        """Shield future raising an exception is captured as error."""
        t = _target()
        mock_discover.return_value = [t]

        r = execute_panic()
        assert len(r.shield_errors) == 1
        assert "thread crashed" in r.shield_errors[0][1]


class TestPanicStopContainers:
    """Tests for panic_stop_containers (standalone Phase 2)."""

    @patch(_STOP, return_value=(["c1", "c2"], []))
    @patch(_DISCOVER)
    def test_standalone_stop(self, mock_discover, _s):
        """Standalone stop discovers and stops containers."""
        mock_discover.return_value = [_target()]
        stopped, errors = panic_stop_containers()
        assert stopped == ["c1", "c2"]
        assert not errors

    @patch(_STOP, return_value=([], [("c1", "rm failed")]))
    @patch(_DISCOVER)
    def test_standalone_stop_errors(self, mock_discover, _s):
        """Errors from stop are propagated."""
        mock_discover.return_value = [_target()]
        stopped, errors = panic_stop_containers()
        assert not stopped
        assert errors[0][1] == "rm failed"


class TestStopContainers:
    """Tests for _stop_containers internals."""

    @patch("terok.lib.core.runtime.get_runtime")
    def test_stops_all_modes(self, mock_get_runtime):
        """All container modes are generated for each target."""
        from terok.lib.domain.panic import _stop_containers

        mock_get_runtime.return_value.force_remove.return_value = []
        t = _target()
        stopped, errors = _stop_containers([t])
        assert not errors
        # force_remove called once with a list of container handles
        mock_get_runtime.return_value.force_remove.assert_called_once()
        handles = mock_get_runtime.return_value.force_remove.call_args[0][0]
        assert len(handles) > 0

    @patch("terok.lib.core.runtime.get_runtime")
    def test_stop_exception(self, mock_get_runtime):
        """Exception from force_remove yields errors."""
        from terok.lib.domain.panic import _stop_containers

        mock_get_runtime.return_value.force_remove.side_effect = Exception("podman died")
        stopped, errors = _stop_containers([_target()])
        assert not stopped
        assert all("podman died" in e for _, e in errors)

    def test_empty_targets(self):
        """Empty target list returns empty results."""
        from terok.lib.domain.panic import _stop_containers

        assert _stop_containers([]) == ([], [])


class TestRaiseShield:
    """Tests for _raise_shield."""

    @patch("terok_sandbox.block")
    def test_success(self, mock_block):
        """Shield block succeeds."""
        from terok.lib.domain.panic import _raise_shield

        cname, err = _raise_shield(_target())
        assert err is None
        mock_block.assert_called_once()

    @patch("terok_sandbox.block", side_effect=Exception("nft failed"))
    def test_failure(self, _):
        """Shield block failure returns error string."""
        from terok.lib.domain.panic import _raise_shield

        cname, err = _raise_shield(_target())
        assert "nft failed" in err


class TestStopVault:
    """Tests for _stop_vault."""

    @patch("terok_sandbox.stop_vault")
    def test_success(self, mock_stop: MagicMock) -> None:
        """Vault stop succeeds."""
        from terok.lib.domain.panic import _stop_vault

        ok, err = _stop_vault()
        assert ok and err is None

    @patch("terok_sandbox.stop_vault", side_effect=Exception("no vault"))
    def test_failure(self, mock_stop: MagicMock) -> None:
        """Vault stop failure returns error."""
        from terok.lib.domain.panic import _stop_vault

        ok, err = _stop_vault()
        assert not ok and "no vault" in err


class TestStopGate:
    """Tests for _stop_gate."""

    @patch("terok_sandbox.stop_daemon")
    def test_success(self, mock_stop):
        """Gate stop succeeds."""
        from terok.lib.domain.panic import _stop_gate

        ok, err = _stop_gate()
        assert ok and err is None

    @patch("terok_sandbox.stop_daemon", side_effect=Exception("no gate"))
    def test_failure(self, _):
        """Gate stop failure returns error."""
        from terok.lib.domain.panic import _stop_gate

        ok, err = _stop_gate()
        assert not ok and "no gate" in err


class TestPanicLock:
    """Tests for panic lock file lifecycle."""

    @patch("terok.lib.domain.panic.core_state_dir")
    def test_lock_lifecycle(self, mock_state, tmp_path):
        """Lock can be written, checked, and cleared."""
        mock_state.return_value = tmp_path
        assert not is_panicked()
        from terok.lib.domain.panic import _write_panic_lock

        _write_panic_lock()
        assert is_panicked()
        clear_panic_lock()
        assert not is_panicked()

    @patch("terok.lib.domain.panic.core_state_dir")
    def test_clear_idempotent(self, mock_state, tmp_path):
        """Clearing when no lock exists is a no-op."""
        mock_state.return_value = tmp_path
        clear_panic_lock()  # should not raise
        assert not is_panicked()


class TestFormatReport:
    """Tests for format_panic_report."""

    def test_clean(self):
        """No errors."""
        r = PanicResult(
            shields_raised=["c1"], vault_stopped=True, gate_stopped=True, total_running=1
        )
        assert "FAILED" not in format_panic_report(r)

    def test_errors(self):
        """Failures shown."""
        r = PanicResult(shield_errors=[("c1", "fail")], vault_error="down", total_running=1)
        report = format_panic_report(r)
        assert "FAILED" in report and "fail" in report

    def test_bypass(self):
        """Bypass flagged."""
        r = PanicResult(shield_bypassed=True, vault_stopped=True, gate_stopped=True)
        assert "BYPASSED" in format_panic_report(r)

    def test_container_stop_errors(self):
        """Container stop errors appear in report."""
        r = PanicResult(
            vault_stopped=True,
            gate_stopped=True,
            container_stop_errors=[("c1", "timeout")],
            total_running=1,
        )
        report = format_panic_report(r)
        assert "stop c1: timeout" in report

    def test_gate_error_in_report(self):
        """Gate error appears in error section."""
        r = PanicResult(vault_stopped=True, gate_error="port in use", total_running=0)
        report = format_panic_report(r)
        assert "gate: port in use" in report

    def test_containers_stopped_count(self):
        """Stopped container count shown."""
        r = PanicResult(
            shields_raised=["c1"],
            vault_stopped=True,
            gate_stopped=True,
            containers_stopped=["c1", "c2"],
            total_running=2,
        )
        assert "Containers stopped: 2" in format_panic_report(r)
