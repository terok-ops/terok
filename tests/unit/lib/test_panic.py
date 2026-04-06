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
)
from tests.testfs import FAKE_PROJECT_TASKS_ROOT

_SHIELD = "terok.lib.domain.panic._raise_shield"
_PROXY = "terok.lib.domain.panic._stop_proxy"
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


class TestExecutePanic:
    """Tests for execute_panic."""

    @patch(_LOCK)
    @patch(_BYPASS, return_value=False)
    @patch(_DISCOVER)
    @patch(_SHIELD)
    @patch(_PROXY, return_value=(True, None))
    @patch(_GATE, return_value=(True, None))
    def test_success(self, _g, _p, mock_shield, mock_discover, _b, _l):
        """All operations succeed."""
        t = _target()
        mock_discover.return_value = [t]
        mock_shield.return_value = (t[3], None)

        r = execute_panic()

        assert r.shields_raised == [t[3]]
        assert r.proxy_stopped and r.gate_stopped
        assert not r.has_errors

    @patch(_LOCK)
    @patch(_BYPASS, return_value=True)
    @patch(_DISCOVER, return_value=[_target()])
    @patch(_SHIELD)
    @patch(_PROXY, return_value=(True, None))
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
    @patch(_PROXY, return_value=(True, None))
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
    @patch(_PROXY, return_value=(True, None))
    @patch(_GATE, return_value=(True, None))
    def test_phase2_stop(self, _g, _p, mock_shield, mock_discover, _b, _l, _s):
        """Phase 2 container stop works when requested."""
        t = _target()
        mock_discover.return_value = [t]
        mock_shield.return_value = (t[3], None)

        r = execute_panic(stop_containers=True)
        assert r.containers_stopped == ["c1"]


class TestPanicLock:
    """Tests for panic lock file lifecycle."""

    @patch("terok.lib.domain.panic.state_root")
    def test_lock_lifecycle(self, mock_state, tmp_path):
        """Lock can be written, checked, and cleared."""
        mock_state.return_value = tmp_path
        assert not is_panicked()
        from terok.lib.domain.panic import _write_panic_lock

        _write_panic_lock()
        assert is_panicked()
        clear_panic_lock()
        assert not is_panicked()


class TestFormatReport:
    """Tests for format_panic_report."""

    def test_clean(self):
        """No errors."""
        r = PanicResult(
            shields_raised=["c1"], proxy_stopped=True, gate_stopped=True, total_running=1
        )
        assert "FAILED" not in format_panic_report(r)

    def test_errors(self):
        """Failures shown."""
        r = PanicResult(shield_errors=[("c1", "fail")], proxy_error="down", total_running=1)
        report = format_panic_report(r)
        assert "FAILED" in report and "fail" in report

    def test_bypass(self):
        """Bypass flagged."""
        r = PanicResult(shield_bypassed=True, proxy_stopped=True, gate_stopped=True)
        assert "BYPASSED" in format_panic_report(r)
