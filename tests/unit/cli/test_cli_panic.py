# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the panic CLI command."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from terok.cli.commands.panic import _cmd_clear, _cmd_panic, _stop_remaining, dispatch
from terok.lib.domain.panic import PanicResult


class TestDispatch:
    """Tests for panic dispatch routing."""

    def test_ignores_other_commands(self):
        """Dispatch returns False for non-panic commands."""
        from argparse import Namespace

        assert dispatch(Namespace(cmd="task")) is False

    def test_routes_to_clear(self):
        """Dispatch routes --clear to _cmd_clear."""
        from argparse import Namespace

        with patch("terok.cli.commands.panic._cmd_clear") as mock_clear:
            dispatch(Namespace(cmd="panic", clear=True, stop=False))
            mock_clear.assert_called_once()

    def test_routes_to_panic(self):
        """Dispatch routes default to _cmd_panic."""
        from argparse import Namespace

        with patch("terok.cli.commands.panic._cmd_panic") as mock_panic:
            dispatch(Namespace(cmd="panic", clear=False, stop=True))
            mock_panic.assert_called_once_with(stop=True)


class TestCmdClear:
    """Tests for _cmd_clear."""

    @patch("terok.cli.commands.panic.is_panicked", return_value=True)
    @patch("terok.cli.commands.panic.clear_panic_lock")
    def test_clears_when_panicked(self, mock_clear, _is, capsys):
        """Clears lock and prints instructions when panicked."""
        _cmd_clear()
        mock_clear.assert_called_once()
        out = capsys.readouterr().out
        assert "Panic state cleared" in out
        assert "terok credential-proxy start" in out

    @patch("terok.cli.commands.panic.is_panicked", return_value=False)
    def test_noop_when_not_panicked(self, _is, capsys):
        """Prints info when no panic state exists."""
        _cmd_clear()
        assert "No panic state" in capsys.readouterr().out


class TestCmdPanic:
    """Tests for _cmd_panic."""

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("terok.cli.commands.panic.format_panic_report", return_value="report")
    def test_success_no_running(self, _fmt, mock_exec, capsys):
        """Clean panic with no running containers exits 0."""
        mock_exec.return_value = PanicResult(proxy_stopped=True, gate_stopped=True)
        _cmd_panic(stop=False)
        out = capsys.readouterr().out
        assert "report" in out

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("terok.cli.commands.panic.format_panic_report", return_value="report")
    def test_exits_1_on_errors(self, _fmt, mock_exec):
        """Exits 1 when has_errors is True."""
        mock_exec.return_value = PanicResult(proxy_error="fail")
        with pytest.raises(SystemExit, match="1"):
            _cmd_panic(stop=False)

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("terok.cli.commands.panic.format_panic_report", return_value="report")
    @patch("builtins.input", return_value="y")
    @patch("terok.cli.commands.panic._stop_remaining")
    def test_prompts_stop_on_running(self, mock_stop, _inp, _fmt, mock_exec, capsys):
        """Prompts to stop containers when some are running."""
        mock_exec.return_value = PanicResult(proxy_stopped=True, gate_stopped=True, total_running=2)
        _cmd_panic(stop=False)
        mock_stop.assert_called_once()

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("terok.cli.commands.panic.format_panic_report", return_value="report")
    @patch("builtins.input", return_value="n")
    @patch("terok.cli.commands.panic._stop_remaining")
    def test_skips_stop_on_decline(self, mock_stop, _inp, _fmt, mock_exec, capsys):
        """Does not stop containers when user declines."""
        mock_exec.return_value = PanicResult(proxy_stopped=True, gate_stopped=True, total_running=2)
        _cmd_panic(stop=False)
        mock_stop.assert_not_called()

    @patch("terok.cli.commands.panic.execute_panic")
    @patch("terok.cli.commands.panic.format_panic_report", return_value="report")
    @patch("builtins.input", side_effect=EOFError)
    @patch("terok.cli.commands.panic._stop_remaining")
    def test_handles_eof_on_prompt(self, mock_stop, _inp, _fmt, mock_exec, capsys):
        """Handles EOF on stop prompt gracefully."""
        mock_exec.return_value = PanicResult(proxy_stopped=True, gate_stopped=True, total_running=1)
        _cmd_panic(stop=False)
        mock_stop.assert_not_called()


class TestStopRemaining:
    """Tests for _stop_remaining."""

    @patch("terok.lib.domain.panic.panic_stop_containers", return_value=(["c1", "c2"], []))
    def test_stops_successfully(self, _s, capsys):
        """Prints success count."""
        result = PanicResult()
        _stop_remaining(result)
        assert result.containers_stopped == ["c1", "c2"]
        assert "Stopped 2" in capsys.readouterr().out

    @patch("terok.lib.domain.panic.panic_stop_containers", return_value=([], [("c1", "fail")]))
    def test_reports_errors(self, _s, capsys):
        """Prints failure details."""
        result = PanicResult()
        _stop_remaining(result)
        assert "FAILED" in capsys.readouterr().out

    @patch("terok.lib.domain.panic.panic_stop_containers", return_value=([], []))
    def test_no_containers(self, _s, capsys):
        """Prints nothing-to-stop when empty."""
        result = PanicResult()
        _stop_remaining(result)
        assert "No running containers" in capsys.readouterr().out
