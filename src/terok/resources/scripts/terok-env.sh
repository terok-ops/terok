# shellcheck shell=bash
# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

# Core terok container environment — sourced by ALL shell modes:
#   - Non-interactive (bash -c): via BASH_ENV
#   - Interactive:               via /etc/bash.bashrc
#   - Login:                     via /etc/profile.d/
#
# Guard against double-sourcing (login shells source both profile.d and bashrc).
[ -n "$_TEROK_ENV_LOADED" ] && return 0
_TEROK_ENV_LOADED=1

# ── PATH ──────────────────────────────────────────────────────────────────────

export PATH="$HOME/.npm-packages/bin:$HOME/.local/bin:$HOME/.opencode/bin:$PATH"

# ── Git identity ──────────────────────────────────────────────────────────────

# Source the helper that defines _terok_apply_git_identity().
# Wrapper functions (claude, codex, etc.) call this in subshells to set
# GIT_AUTHOR_*/GIT_COMMITTER_* per invocation.
[ -r /usr/local/share/terok/terok-git-identity.sh ] && \
    . /usr/local/share/terok/terok-git-identity.sh

# Git identity wrappers for non-agent CLIs.
# Agent wrappers (claude, codex, vibe, …) are generated per-task in
# terok-agent.sh and sourced via zz-terok-project.sh below.
gh() {
  (
    _terok_apply_git_identity "GitHub CLI" "gh@github.com"
    command gh "$@"
  )
}
glab() {
  (
    _terok_apply_git_identity "GitLab CLI" "glab@gitlab.com"
    command glab "$@"
  )
}

# ── Per-project agent wrappers ────────────────────────────────────────────────

# Source terok-agent.sh (mounted per-task at /home/dev/.terok/).
# Defines wrapper functions for all agent CLIs: claude(), codex(), vibe(), etc.
[ -r /etc/profile.d/zz-terok-project.sh ] && . /etc/profile.d/zz-terok-project.sh
