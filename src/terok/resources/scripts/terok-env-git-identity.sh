#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Git identity helper for terok task containers (sourced by terok-env.sh).
#
# Expected environment:
#   TEROK_GIT_AUTHORSHIP - agent-human (default), human-agent, agent, human
#   HUMAN_GIT_NAME       - optional fallback human name
#   HUMAN_GIT_EMAIL      - optional fallback human email
#
# Usage:
#   _terok_apply_git_identity "Agent Name" "agent@example.com"
#
# The helper mutates the current shell environment so wrappers usually invoke
# it from a subshell to keep identity changes scoped to one command.

_terok_apply_git_identity() {
  local agent_name="${1:-AI Agent}"
  local agent_email="${2:-ai-agent@localhost}"
  local human_name="${HUMAN_GIT_NAME:-Nobody}"
  local human_email="${HUMAN_GIT_EMAIL:-nobody@localhost}"
  local mode="${TEROK_GIT_AUTHORSHIP:-agent-human}"

  unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL

  case "${mode}" in
    agent-human)
      export GIT_AUTHOR_NAME="${agent_name}"
      export GIT_AUTHOR_EMAIL="${agent_email}"
      export GIT_COMMITTER_NAME="${human_name}"
      export GIT_COMMITTER_EMAIL="${human_email}"
      ;;
    human-agent)
      export GIT_AUTHOR_NAME="${human_name}"
      export GIT_AUTHOR_EMAIL="${human_email}"
      export GIT_COMMITTER_NAME="${agent_name}"
      export GIT_COMMITTER_EMAIL="${agent_email}"
      ;;
    agent)
      export GIT_AUTHOR_NAME="${agent_name}"
      export GIT_AUTHOR_EMAIL="${agent_email}"
      export GIT_COMMITTER_NAME="${agent_name}"
      export GIT_COMMITTER_EMAIL="${agent_email}"
      ;;
    human)
      export GIT_AUTHOR_NAME="${human_name}"
      export GIT_AUTHOR_EMAIL="${human_email}"
      export GIT_COMMITTER_NAME="${human_name}"
      export GIT_COMMITTER_EMAIL="${human_email}"
      ;;
    *)
      export GIT_AUTHOR_NAME="${agent_name}"
      export GIT_AUTHOR_EMAIL="${agent_email}"
      export GIT_COMMITTER_NAME="${human_name}"
      export GIT_COMMITTER_EMAIL="${human_email}"
      ;;
  esac
}
