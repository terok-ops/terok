# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared test constants: filesystem paths.

Centralizes hardcoded path literals so SonarCloud only flags the constant
definition, not every test assertion.  Mirrors terok-shield's ``testfs.py``
convention by keeping test-owned path fragments and synthetic filesystem
locations in one place.
"""

from pathlib import Path

# ── Placeholder directories used in mocked tests ─────────────────────────────

MOCK_BASE = Path("/tmp/terok-testing")
"""Root for synthetic filesystem paths used by mocked tests."""

MOCK_TASK_DIR = MOCK_BASE / "tasks" / "42"
"""Fake per-task directory used by shield adapter tests."""

MOCK_TASK_DIR_1 = MOCK_BASE / "tasks" / "1"
"""Alternate fake per-task directory used by CLI shield tests."""

MOCK_CONFIG_ROOT = Path("/home/user/.config/terok")
"""Fake XDG-style config root used by path-related tests."""

FAKE_GATE_DIR = MOCK_BASE / "gate"
"""Fake gate mirror path used by CLI and gate-server tests."""

FAKE_STATE_DIR = MOCK_BASE / "state"
"""Fake state root used by gate-server related tests."""

FAKE_TEROK_STATE_DIR = MOCK_BASE / "terok-state"
"""Fake terok state root used by token-file path tests."""

FAKE_PROJECT_ROOT = MOCK_BASE / "project-root"
"""Fake project root used by container/project config tests."""

FAKE_PROJECT_TASKS_ROOT = FAKE_PROJECT_ROOT / "tasks"
"""Fake project tasks root used by container/project config tests."""

FAKE_PROJECT_GATE_DIR = FAKE_PROJECT_ROOT / "gate"
"""Fake project gate path used by container/project config tests."""

FAKE_SSH_DIR = MOCK_BASE / "ssh"
"""Fake SSH host directory used by mount-related tests."""

FAKE_TMUX_SOCKET = MOCK_BASE / "tmux-1000" / "default,12345,0"
"""Fake tmux socket path used by terminal-detection tests."""

FAKE_WIZARD_PROJECTS_DIR = MOCK_BASE / "wizard-projects"
"""Base directory for fake project.yml paths returned by wizard tests."""

# ── Nonexistent / missing paths ──────────────────────────────────────────────

NONEXISTENT_DIR = Path("/nonexistent")
"""Guaranteed-missing absolute path used for missing-file behavior tests."""

NONEXISTENT_MARKDOWN_PATH = NONEXISTENT_DIR / "path.md"
"""Missing markdown path used by CLI instructions-file validation tests."""

NONEXISTENT_AGENT_PATH = NONEXISTENT_DIR / "agent.md"
"""Missing agent markdown path used by sub-agent parsing tests."""

NONEXISTENT_FILE_PATH = NONEXISTENT_DIR / "file.md"
"""Missing generic file path used by parse-md-agent tests."""

NONEXISTENT_TOKENS_PATH = NONEXISTENT_DIR / "tokens.json"
"""Missing gate token store path used by token-store tests."""

NONEXISTENT_CONFIG_YAML = NONEXISTENT_DIR / "config.yml"
"""Missing YAML config path used by config-stack tests."""

NONEXISTENT_CONFIG_JSON = NONEXISTENT_DIR / "config.json"
"""Missing JSON config path used by config-stack tests."""

NONEXISTENT_PROJECT_ROOT = MOCK_BASE / "does-not-exist"
"""Missing fake project root used by instruction-resolution tests."""

MISSING_TOKENS_PATH = NONEXISTENT_PROJECT_ROOT / "tokens.json"
"""Absent token-store path with a writable parent used by token-lock tests."""

# ── Container/internal paths asserted in generated scripts ───────────────────

CONTAINER_HOME = Path("/home/dev")
"""Container home directory used in generated wrapper/config assertions."""

CONTAINER_SSH_DIR = CONTAINER_HOME / ".ssh"
"""Container SSH directory used by bind-mount assertions."""

CONTAINER_TEROK_DIR = CONTAINER_HOME / ".terok"
"""Container terok state/config directory used by wrapper assertions."""

CONTAINER_INSTRUCTIONS_PATH = CONTAINER_TEROK_DIR / "instructions.md"
"""Container instructions file path injected into agent configs."""

CONTAINER_CLAUDE_SESSION_PATH = CONTAINER_TEROK_DIR / "claude-session.txt"
"""Container Claude session file path used by session-hook assertions."""

CONTAINER_TEROK_MOUNT_Z = f"{CONTAINER_TEROK_DIR}:Z"
"""Bind-mount fragment for the container terok directory with SELinux relabeling."""

CONTAINER_CLAUDE_MEMORY_OVERRIDE = "/home/dev/.claude/projects/${PROJECT_ID}-workspace/memory"
"""Literal shell path used in generated Claude wrapper memory override assertions."""

WORKSPACE_ROOT = Path("/workspace")
"""Canonical workspace root referenced in bundled instructions assertions."""

# ── Well-known integration environment path fragments ────────────────────────

HOME_DIR_NAME = "home"
"""Temporary HOME directory name used by integration fixtures."""

XDG_CONFIG_HOME_NAME = "xdg-config"
"""Temporary XDG config root name used by integration fixtures."""

CONFIG_ROOT_NAME = "config"
"""Temporary terok system-config root name used by integration fixtures."""

STATE_ROOT_NAME = "state"
"""Temporary terok state root name used by integration fixtures."""


def mock_wizard_project_file(project_id: str) -> Path:
    """Return a fake wizard output path for ``project_id``."""
    return FAKE_WIZARD_PROJECTS_DIR / project_id / "project.yml"
