# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Re-export shim — canonical code lives in ``terok_agent.agents``."""

from terok_agent.agents import (  # noqa: F401 — re-exported
    AgentConfigSpec,
    _generate_claude_wrapper,
    _inject_opencode_instructions,
    _subagents_to_json,
    _write_session_hook,
    parse_md_agent,
    prepare_agent_config_dir,
)

__all__ = [
    "AgentConfigSpec",
    "_generate_claude_wrapper",
    "_inject_opencode_instructions",
    "_subagents_to_json",
    "_write_session_hook",
    "parse_md_agent",
    "prepare_agent_config_dir",
]
