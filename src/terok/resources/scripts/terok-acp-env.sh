#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Common environment setup for terok ACP wrappers.
#
# Sourced (not exec'd) by per-agent ACP wrapper scripts to configure git
# identity before the real ACP adapter runs.
#
# Expected variables (set by the sourcing wrapper):
#   _AGENT_NAME   - display name for git author, e.g. "Claude"
#   _AGENT_EMAIL  - email for git author, e.g. "noreply@anthropic.com"
#
# Expected environment (set by terok container):
#   TEROK_GIT_AUTHORSHIP  - authorship policy (default: agent-human)
#   HUMAN_GIT_NAME        - human git name
#   HUMAN_GIT_EMAIL       - human git email

if [[ -r /usr/local/share/terok/terok-env-git-identity.sh ]]; then
    . /usr/local/share/terok/terok-env-git-identity.sh
    _terok_apply_git_identity "${_AGENT_NAME:?}" "${_AGENT_EMAIL:?}"
fi
