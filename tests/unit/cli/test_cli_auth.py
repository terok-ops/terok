# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the top-level ``terok auth`` command.

Three invocation shapes to verify:

- ``terok auth``                           → interactive menu (no provider).
- ``terok auth <p>``                       → host-wide auth (no project_id).
- ``terok auth <p> <id>``                  → legacy positional project.
- ``terok auth <p> --project <id>``        → named project flag.
"""

from __future__ import annotations

import argparse
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from terok.cli.commands.auth import (
    _parse_provider_selection,
    _run_interactive,
    _run_one,
    dispatch,
    register,
)


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register(parser.add_subparsers(dest="cmd"))
    return parser


# ── argparse registration ──────────────────────────────────────────────


def test_auth_parses_positional_provider_and_project() -> None:
    """``auth claude myproj`` keeps parsing via the legacy two-positional form."""
    args = _make_parser().parse_args(["auth", "claude", "myproj"])
    assert args.cmd == "auth"
    assert args.provider == "claude"
    assert args.project_id == "myproj"
    assert args.project_flag is None


def test_auth_parses_provider_only() -> None:
    """``auth claude`` (no project) sets project_id to None — host-wide shape."""
    args = _make_parser().parse_args(["auth", "claude"])
    assert args.provider == "claude"
    assert args.project_id is None
    assert args.project_flag is None


def test_auth_parses_no_arguments() -> None:
    """``auth`` on its own leaves provider None — interactive shape."""
    args = _make_parser().parse_args(["auth"])
    assert args.provider is None
    assert args.project_id is None
    assert args.project_flag is None


def test_auth_parses_project_flag() -> None:
    """``auth claude --project p`` populates project_flag; positional stays None."""
    args = _make_parser().parse_args(["auth", "claude", "--project", "p"])
    assert args.provider == "claude"
    assert args.project_flag == "p"
    assert args.project_id is None


def test_auth_rejects_unknown_provider() -> None:
    """argparse's choices validation still fires for unknown provider names."""
    with pytest.raises(SystemExit) as exc:
        _make_parser().parse_args(["auth", "not-a-provider"])
    assert exc.value.code == 2


# ── dispatch wiring ────────────────────────────────────────────────────


def test_dispatch_ignores_other_commands() -> None:
    """Dispatch returns False for unrelated namespaces."""
    assert dispatch(argparse.Namespace(cmd="task")) is False


def test_dispatch_host_wide_skips_project_loading() -> None:
    """``auth <provider>`` never touches ``load_project`` / ``require_agent_installed``."""
    args = argparse.Namespace(cmd="auth", provider="claude", project_id=None, project_flag=None)
    with (
        patch("terok.cli.commands.auth.load_project") as mock_load,
        patch("terok.cli.commands.auth.require_agent_installed") as mock_check,
        patch("terok.cli.commands.auth.authenticate") as mock_auth,
    ):
        assert dispatch(args) is True

    mock_load.assert_not_called()
    mock_check.assert_not_called()
    mock_auth.assert_called_once_with("claude", None)


def test_dispatch_project_positional_runs_install_check() -> None:
    """Legacy ``auth <p> <id>`` loads the project and verifies the agent."""
    fake_project = SimpleNamespace(id="p1")
    args = argparse.Namespace(cmd="auth", provider="claude", project_id="p1", project_flag=None)
    with (
        patch("terok.cli.commands.auth.load_project", return_value=fake_project),
        patch("terok.cli.commands.auth.require_agent_installed") as mock_check,
        patch("terok.cli.commands.auth.authenticate") as mock_auth,
    ):
        assert dispatch(args) is True

    mock_check.assert_called_once_with(fake_project, "claude", noun="Provider")
    mock_auth.assert_called_once_with("claude", "p1")


def test_dispatch_project_flag_wins_over_positional() -> None:
    """``--project`` wins when both the flag and the legacy positional are set."""
    fake_project = SimpleNamespace(id="flagged")
    args = argparse.Namespace(
        cmd="auth", provider="claude", project_id="positional", project_flag="flagged"
    )
    with (
        patch("terok.cli.commands.auth.load_project", return_value=fake_project) as mock_load,
        patch("terok.cli.commands.auth.require_agent_installed"),
        patch("terok.cli.commands.auth.authenticate") as mock_auth,
    ):
        dispatch(args)

    mock_load.assert_called_once_with("flagged")
    mock_auth.assert_called_once_with("claude", "flagged")


def test_dispatch_no_provider_runs_interactive() -> None:
    """``auth`` with no provider routes into the chained interactive flow."""
    args = argparse.Namespace(cmd="auth", provider=None, project_id=None, project_flag=None)
    with patch("terok.cli.commands.auth._run_interactive") as mock_inter:
        dispatch(args)
    mock_inter.assert_called_once_with(None)


# ── interactive helpers ────────────────────────────────────────────────


def test_parse_provider_selection_accepts_mixed_numbers_and_names() -> None:
    """Numeric indices and names co-exist in one selection string."""
    names = ["claude", "codex", "gh"]
    assert _parse_provider_selection("1, gh, 2", names) == ["claude", "gh", "codex"]


def test_parse_provider_selection_deduplicates() -> None:
    """Repeated picks collapse to a single entry, preserving first-seen order."""
    names = ["claude", "codex"]
    assert _parse_provider_selection("1, claude, 1", names) == ["claude"]


def test_parse_provider_selection_skips_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    """Unknown tokens are reported on stderr and skipped; valid picks still run."""
    names = ["claude"]
    picked = _parse_provider_selection("claude, bogus, 99", names)
    assert picked == ["claude"]
    err = capsys.readouterr().err
    assert "bogus" in err
    assert "99" in err


def test_run_interactive_cancels_on_empty_answer(capsys: pytest.CaptureFixture[str]) -> None:
    """Empty input aborts without launching any auth."""
    with (
        patch("sys.stdin", new=StringIO("\n")),
        patch("terok.cli.commands.auth._run_one") as mock_run,
    ):
        _run_interactive(project_id=None)
    mock_run.assert_not_called()


def test_run_interactive_runs_each_selected_provider() -> None:
    """Selected providers are authenticated in order, sharing the same project scope."""
    with (
        patch("sys.stdin", new=StringIO("claude, codex\n")),
        patch("terok.cli.commands.auth._run_one") as mock_run,
    ):
        _run_interactive(project_id="myproj")
    assert [call.args for call in mock_run.call_args_list] == [
        ("claude", "myproj"),
        ("codex", "myproj"),
    ]


# ── single-provider runner ─────────────────────────────────────────────


def test_run_one_skips_install_check_when_host_wide() -> None:
    """Host-wide ``_run_one`` goes straight to ``authenticate`` — no project load."""
    with (
        patch("terok.cli.commands.auth.load_project") as mock_load,
        patch("terok.cli.commands.auth.authenticate") as mock_auth,
    ):
        _run_one("claude", project_id=None)
    mock_load.assert_not_called()
    mock_auth.assert_called_once_with("claude", None)
