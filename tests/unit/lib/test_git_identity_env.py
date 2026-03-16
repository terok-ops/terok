# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for baked-in git identity env vars (environment.resolve_git_identity).

Verifies that the Python-side identity resolution matches the shell helper
(terok-env-git-identity.sh) for all authorship modes, and that
apply_git_identity_env correctly wires project config into the env dict.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from terok.lib.containers.environment import apply_git_identity_env, resolve_git_identity

# ── Fixtures ──────────────────────────────────────────────────────────────────

AGENT = ("Claude", "noreply@anthropic.com")
HUMAN = ("Alice Example", "alice@example.com")


def _make_project(
    authorship: str = "agent-human",
    human_name: str = HUMAN[0],
    human_email: str = HUMAN[1],
) -> MagicMock:
    """Create a minimal ProjectConfig mock with git identity fields."""
    project = MagicMock()
    project.git_authorship = authorship
    project.human_name = human_name
    project.human_email = human_email
    return project


# ── resolve_git_identity ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            "agent-human",
            {
                "GIT_AUTHOR_NAME": "Claude",
                "GIT_AUTHOR_EMAIL": "noreply@anthropic.com",
                "GIT_COMMITTER_NAME": "Alice Example",
                "GIT_COMMITTER_EMAIL": "alice@example.com",
            },
        ),
        (
            "human-agent",
            {
                "GIT_AUTHOR_NAME": "Alice Example",
                "GIT_AUTHOR_EMAIL": "alice@example.com",
                "GIT_COMMITTER_NAME": "Claude",
                "GIT_COMMITTER_EMAIL": "noreply@anthropic.com",
            },
        ),
        (
            "agent",
            {
                "GIT_AUTHOR_NAME": "Claude",
                "GIT_AUTHOR_EMAIL": "noreply@anthropic.com",
                "GIT_COMMITTER_NAME": "Claude",
                "GIT_COMMITTER_EMAIL": "noreply@anthropic.com",
            },
        ),
        (
            "human",
            {
                "GIT_AUTHOR_NAME": "Alice Example",
                "GIT_AUTHOR_EMAIL": "alice@example.com",
                "GIT_COMMITTER_NAME": "Alice Example",
                "GIT_COMMITTER_EMAIL": "alice@example.com",
            },
        ),
    ],
    ids=["agent-human", "human-agent", "agent", "human"],
)
def test_resolve_git_identity_modes(mode: str, expected: dict[str, str]) -> None:
    """Each authorship mode produces the correct author/committer mapping."""
    result = resolve_git_identity(
        agent_name=AGENT[0],
        agent_email=AGENT[1],
        human_name=HUMAN[0],
        human_email=HUMAN[1],
        authorship=mode,
    )
    assert result == expected


def test_resolve_git_identity_unknown_mode_defaults_to_agent_human() -> None:
    """Unknown authorship modes fall back to agent-human (match/case default)."""
    result = resolve_git_identity(
        agent_name=AGENT[0],
        agent_email=AGENT[1],
        human_name=HUMAN[0],
        human_email=HUMAN[1],
        authorship="bogus-mode",
    )
    assert result["GIT_AUTHOR_NAME"] == "Claude"
    assert result["GIT_COMMITTER_NAME"] == "Alice Example"


def test_resolve_git_identity_default_authorship_is_agent_human() -> None:
    """Omitting authorship defaults to agent-human."""
    result = resolve_git_identity(
        agent_name="Codex",
        agent_email="noreply@openai.com",
        human_name="Bob",
        human_email="bob@example.com",
    )
    assert result["GIT_AUTHOR_NAME"] == "Codex"
    assert result["GIT_COMMITTER_NAME"] == "Bob"


def test_resolve_git_identity_returns_all_four_keys() -> None:
    """The result always contains exactly the four expected keys."""
    result = resolve_git_identity("A", "a@x", "H", "h@x")
    assert set(result) == {
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    }


# ── Shell parity ──────────────────────────────────────────────────────────────
# The shell helper (terok-env-git-identity.sh) is tested in test_git_authorship.py.
# These cross-check that the Python function produces identical output for the
# same inputs used by those shell tests.


@pytest.mark.parametrize(
    ("mode", "agent_name", "agent_email"),
    [
        ("agent-human", "Codex", "noreply@openai.com"),
        ("human-agent", "Codex", "noreply@openai.com"),
        ("agent", "Codex", "noreply@openai.com"),
        ("human", "Codex", "noreply@openai.com"),
    ],
)
def test_resolve_matches_shell_helper_test_vectors(
    mode: str, agent_name: str, agent_email: str
) -> None:
    """Python resolve_git_identity matches the shell helper's test vectors.

    The expected values are copied from test_git_authorship.py to ensure
    the two implementations stay in sync.
    """
    shell_expected = {
        "agent-human": {
            "GIT_AUTHOR_NAME": "Codex",
            "GIT_AUTHOR_EMAIL": "noreply@openai.com",
            "GIT_COMMITTER_NAME": "Alice Example",
            "GIT_COMMITTER_EMAIL": "alice@example.com",
        },
        "human-agent": {
            "GIT_AUTHOR_NAME": "Alice Example",
            "GIT_AUTHOR_EMAIL": "alice@example.com",
            "GIT_COMMITTER_NAME": "Codex",
            "GIT_COMMITTER_EMAIL": "noreply@openai.com",
        },
        "human": {
            "GIT_AUTHOR_NAME": "Alice Example",
            "GIT_AUTHOR_EMAIL": "alice@example.com",
            "GIT_COMMITTER_NAME": "Alice Example",
            "GIT_COMMITTER_EMAIL": "alice@example.com",
        },
        "agent": {
            "GIT_AUTHOR_NAME": "Codex",
            "GIT_AUTHOR_EMAIL": "noreply@openai.com",
            "GIT_COMMITTER_NAME": "Codex",
            "GIT_COMMITTER_EMAIL": "noreply@openai.com",
        },
    }
    result = resolve_git_identity(
        agent_name=agent_name,
        agent_email=agent_email,
        human_name="Alice Example",
        human_email="alice@example.com",
        authorship=mode,
    )
    assert result == shell_expected[mode]


# ── apply_git_identity_env ────────────────────────────────────────────────────


def test_apply_git_identity_env_populates_env_dict() -> None:
    """apply_git_identity_env adds all four GIT_* keys to the env dict."""
    env: dict[str, str] = {"EXISTING_KEY": "untouched"}
    project = _make_project()
    apply_git_identity_env(env, project, AGENT[0], AGENT[1])

    assert env["GIT_AUTHOR_NAME"] == "Claude"
    assert env["GIT_AUTHOR_EMAIL"] == "noreply@anthropic.com"
    assert env["GIT_COMMITTER_NAME"] == "Alice Example"
    assert env["GIT_COMMITTER_EMAIL"] == "alice@example.com"
    assert env["EXISTING_KEY"] == "untouched"


def test_apply_git_identity_env_respects_authorship_policy() -> None:
    """The authorship policy from the project config is applied."""
    env: dict[str, str] = {}
    project = _make_project(authorship="human-agent")
    apply_git_identity_env(env, project, "Vibe", "noreply@mistral.ai")

    assert env["GIT_AUTHOR_NAME"] == "Alice Example"
    assert env["GIT_COMMITTER_NAME"] == "Vibe"


def test_apply_git_identity_env_defaults_for_missing_human() -> None:
    """Falls back to 'Nobody'/'nobody@localhost' when project has no human identity."""
    env: dict[str, str] = {}
    project = _make_project(human_name=None, human_email=None)
    apply_git_identity_env(env, project, "Claude", "noreply@anthropic.com")

    assert env["GIT_COMMITTER_NAME"] == "Nobody"
    assert env["GIT_COMMITTER_EMAIL"] == "nobody@localhost"


def test_apply_git_identity_env_uses_default_agent_identity() -> None:
    """Omitting agent name/email uses the generic defaults."""
    env: dict[str, str] = {}
    project = _make_project()
    apply_git_identity_env(env, project)

    assert env["GIT_AUTHOR_NAME"] == "AI Agent"
    assert env["GIT_AUTHOR_EMAIL"] == "ai-agent@localhost"


def test_apply_git_identity_env_overwrites_existing_git_vars() -> None:
    """Pre-existing GIT_* keys in env are overwritten."""
    env: dict[str, str] = {
        "GIT_AUTHOR_NAME": "stale",
        "GIT_COMMITTER_EMAIL": "stale@example.com",
    }
    project = _make_project()
    apply_git_identity_env(env, project, AGENT[0], AGENT[1])

    assert env["GIT_AUTHOR_NAME"] == "Claude"
    assert env["GIT_COMMITTER_EMAIL"] == "alice@example.com"
