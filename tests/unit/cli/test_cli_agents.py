# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``terok agents`` CLI subcommand."""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from terok.cli.commands import agents


def _ns(*, all_flag: bool = False) -> argparse.Namespace:
    """Build a Namespace as the dispatcher would receive."""
    return argparse.Namespace(cmd="agents", **{"all": all_flag})


def _fake_roster(
    *,
    agent_names: tuple[str, ...] = ("claude", "codex"),
    all_names: tuple[str, ...] = ("claude", "codex", "gh"),
    labels: dict[str, str] | None = None,
) -> SimpleNamespace:
    """Stand-in for [`terok_executor.AgentRoster`][] with the bits the dispatcher reads."""
    if labels is None:
        labels = {"claude": "Anthropic Claude", "codex": "OpenAI Codex", "gh": "GitHub CLI"}
    providers = {name: SimpleNamespace(label=labels.get(name, name)) for name in all_names}
    auth_providers: dict[str, SimpleNamespace] = {}
    return SimpleNamespace(
        agent_names=agent_names,
        all_names=all_names,
        providers=providers,
        auth_providers=auth_providers,
    )


def test_dispatch_returns_false_for_other_cmds() -> None:
    """The dispatcher must let unrelated commands fall through."""
    assert agents.dispatch(argparse.Namespace(cmd="not-agents")) is False


def test_dispatch_lists_agent_names_only_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Default invocation prints only the agent rows, not the tool entries."""
    with patch("terok_executor.get_roster", return_value=_fake_roster()):
        assert agents.dispatch(_ns()) is True
    out = capsys.readouterr().out
    assert "claude" in out
    assert "codex" in out
    assert "gh" not in out  # tool entry hidden behind --all


def test_dispatch_includes_tool_entries_with_all_flag(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--all`` widens the listing to include tool / sidecar entries."""
    with patch("terok_executor.get_roster", return_value=_fake_roster()):
        agents.dispatch(_ns(all_flag=True))
    out = capsys.readouterr().out
    assert "claude" in out
    assert "gh" in out


def test_dispatch_renders_label_alongside_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The output table carries each agent's human-readable label."""
    with patch("terok_executor.get_roster", return_value=_fake_roster()):
        agents.dispatch(_ns())
    out = capsys.readouterr().out
    assert "Anthropic Claude" in out
    assert "OpenAI Codex" in out


def test_dispatch_handles_empty_roster(capsys: pytest.CaptureFixture[str]) -> None:
    """An empty roster prints an explanatory line on stderr instead of an empty table."""
    empty = _fake_roster(agent_names=(), all_names=())
    with patch("terok_executor.get_roster", return_value=empty):
        assert agents.dispatch(_ns()) is True
    err = capsys.readouterr().err
    assert "No agents registered" in err


def test_dispatch_falls_back_to_auth_provider_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An agent missing from ``providers`` still renders via ``auth_providers``."""
    roster = _fake_roster(agent_names=("kisski",), all_names=("kisski",), labels={})
    roster.providers = {}
    roster.auth_providers = {"kisski": SimpleNamespace(label="KISSKI AcademicCloud")}
    with patch("terok_executor.get_roster", return_value=roster):
        agents.dispatch(_ns())
    assert "KISSKI AcademicCloud" in capsys.readouterr().out


def test_dispatch_falls_back_to_name_when_no_label(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An agent in neither providers nor auth_providers shows the bare name as label."""
    roster = _fake_roster(agent_names=("nolabel",), all_names=("nolabel",), labels={})
    roster.providers = {}
    roster.auth_providers = {}
    with patch("terok_executor.get_roster", return_value=roster):
        agents.dispatch(_ns())
    assert "nolabel" in capsys.readouterr().out


def test_register_adds_subparser_with_all_flag() -> None:
    """``register`` adds the ``agents`` subparser with the ``--all`` flag."""
    parser = argparse.ArgumentParser()
    agents.register(parser.add_subparsers(dest="cmd"))
    parsed = parser.parse_args(["agents", "--all"])
    assert parsed.cmd == "agents"
    assert parsed.all is True
