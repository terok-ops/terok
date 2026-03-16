# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for terok's shared Git authorship helper script."""

from __future__ import annotations

import json
import shlex
import subprocess
from importlib import resources

import pytest

SCRIPT_TIMEOUT_SECONDS = 15
"""Timeout for the shell-based Git authorship helper smoke test."""


def apply_mode(mode: str) -> dict[str, str | None]:
    """Run the shared helper in a shell and capture the resulting Git env."""
    helper = resources.files("terok") / "resources" / "scripts" / "terok-env-git-identity.sh"
    with resources.as_file(helper) as helper_path:
        shell = f"""
set -euo pipefail
. {shlex.quote(str(helper_path))}
export HUMAN_GIT_NAME="Alice Example"
export HUMAN_GIT_EMAIL="alice@example.com"
export TEROK_GIT_AUTHORSHIP={shlex.quote(mode)}
export GIT_COMMITTER_NAME="stale"
export GIT_COMMITTER_EMAIL="stale@example.com"
_terok_apply_git_identity "Codex" "noreply@openai.com"
python3 - <<'PY'
import json
import os

keys = (
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
)
print(json.dumps({{key: os.environ.get(key) for key in keys}}))
PY
"""
        result = subprocess.run(
            ["bash", "-lc", shell],
            check=True,
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT_SECONDS,
        )
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (
            "agent-human",
            {
                "GIT_AUTHOR_NAME": "Codex",
                "GIT_AUTHOR_EMAIL": "noreply@openai.com",
                "GIT_COMMITTER_NAME": "Alice Example",
                "GIT_COMMITTER_EMAIL": "alice@example.com",
            },
        ),
        (
            "human-agent",
            {
                "GIT_AUTHOR_NAME": "Alice Example",
                "GIT_AUTHOR_EMAIL": "alice@example.com",
                "GIT_COMMITTER_NAME": "Codex",
                "GIT_COMMITTER_EMAIL": "noreply@openai.com",
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
        (
            "agent",
            {
                "GIT_AUTHOR_NAME": "Codex",
                "GIT_AUTHOR_EMAIL": "noreply@openai.com",
                "GIT_COMMITTER_NAME": "Codex",
                "GIT_COMMITTER_EMAIL": "noreply@openai.com",
            },
        ),
        (
            "invalid-mode",
            {
                "GIT_AUTHOR_NAME": "Codex",
                "GIT_AUTHOR_EMAIL": "noreply@openai.com",
                "GIT_COMMITTER_NAME": "Alice Example",
                "GIT_COMMITTER_EMAIL": "alice@example.com",
            },
        ),
    ],
    ids=["agent-human", "human-agent", "human", "agent", "invalid-falls-back"],
)
def test_git_authorship_helper_modes(mode: str, expected: dict[str, str]) -> None:
    """The helper applies the expected author/committer mapping for each mode."""
    assert apply_mode(mode) == expected
