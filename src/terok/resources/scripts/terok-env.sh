# shellcheck shell=bash
# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Core terok container environment — sourced by ALL shell modes:
#   - Non-interactive (bash -c): via BASH_ENV
#   - Interactive:               via /etc/bash.bashrc
#   - Login:                     via /etc/profile.d/
#
# Guard against double-sourcing (login shells source both profile.d and bashrc).
[ -n "${_TEROK_ENV_LOADED:-}" ] && return 0
_TEROK_ENV_LOADED=1

# ── PATH ──────────────────────────────────────────────────────────────────────

export PATH="$HOME/.npm-packages/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"

# ── Git identity ──────────────────────────────────────────────────────────────

# Source the helper that defines _terok_apply_git_identity().
# Wrapper functions (claude, codex, etc.) call this in subshells to set
# GIT_AUTHOR_*/GIT_COMMITTER_* per invocation.
[ -r /usr/local/share/terok/terok-env-git-identity.sh ] && \
    . /usr/local/share/terok/terok-env-git-identity.sh

# gh/glab are human tools — always use the human identity directly.
# No authorship policy dispatch needed (unlike agent wrappers).
gh() {
  (
    export GIT_AUTHOR_NAME="${HUMAN_GIT_NAME:-Nobody}"
    export GIT_AUTHOR_EMAIL="${HUMAN_GIT_EMAIL:-nobody@localhost}"
    export GIT_COMMITTER_NAME="${HUMAN_GIT_NAME:-Nobody}"
    export GIT_COMMITTER_EMAIL="${HUMAN_GIT_EMAIL:-nobody@localhost}"
    command gh "$@"
  )
}
glab() {
  (
    export GIT_AUTHOR_NAME="${HUMAN_GIT_NAME:-Nobody}"
    export GIT_AUTHOR_EMAIL="${HUMAN_GIT_EMAIL:-nobody@localhost}"
    export GIT_COMMITTER_NAME="${HUMAN_GIT_NAME:-Nobody}"
    export GIT_COMMITTER_EMAIL="${HUMAN_GIT_EMAIL:-nobody@localhost}"
    command glab "$@"
  )
}

# ── Per-project agent wrappers ────────────────────────────────────────────────

# Source per-task agent wrappers (mounted at /home/dev/.terok/terok-agent.sh).
# Defines wrapper functions for all agent CLIs: claude(), codex(), vibe(), etc.
[ -r /usr/local/share/terok/terok-env-wrappers.sh ] && \
    . /usr/local/share/terok/terok-env-wrappers.sh
