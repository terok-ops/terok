#!/usr/bin/env bash

# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

set -euo pipefail

# Expected env:
#   SSH_KEY_NAME        - private key name in ~/.ssh (without .pub)
#   REPO_ROOT           - target repo dir (e.g. /workspace/ultimate-container)
#   CODE_REPO           - git URL (https://, git@, or file://)
#   GIT_BRANCH          - optional, e.g. "main" or "master"
#   GIT_RESET_MODE      - "none" (default), "hard", or "soft"
#   CLONE_FROM          - optional alternate source to seed the repo (e.g. file:///git-gate/gate.git)
#   EXTERNAL_REMOTE_URL - optional URL for upstream repo in gatekeeping mode (added as "external" remote)

: "${GIT_RESET_MODE:=none}"

: "${HOME:=/home/dev}"
SSH_DIR="${HOME}/.ssh"

if [[ -n "${SSH_KEY_NAME:-}" ]]; then
  echo ">> SSH: checking ${SSH_KEY_NAME} in ${SSH_DIR}"
  if [[ -f "${SSH_DIR}/${SSH_KEY_NAME}" && -f "${SSH_DIR}/${SSH_KEY_NAME}.pub" && -f "${SSH_DIR}/config" ]]; then
    install -d -m 700 "${SSH_DIR}" || true
    chmod 700 "${SSH_DIR}" || true
    chmod 600 "${SSH_DIR}/${SSH_KEY_NAME}" || true
    chmod 644 "${SSH_DIR}/${SSH_KEY_NAME}.pub" || true
    chmod 644 "${SSH_DIR}/config" || true

    if command -v ssh >/dev/null 2>&1; then
      # Only warm GitHub known_hosts if the project's code repo uses github.com
      if [[ -n "${CODE_REPO:-}" && "${CODE_REPO}" == *"github.com"* ]]; then
        echo '>> warm github known_hosts (best-effort)'
        ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR git@github.com || true
      fi
    else
      echo 'SSH not installed'
    fi
  else
    echo ">> SSH not fully configured (missing key or config); continuing without SSH"
  fi
fi

# Reset current branch to its remote tracking counterpart.
# Used when no specific branch is configured or the configured branch is missing.
reset_to_current_remote() {
  local current
  current=$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
  if [[ -n "${current}" && "${current}" != "HEAD" ]]; then
    if git -C "${REPO_ROOT}" rev-parse --verify "origin/${current}" >/dev/null 2>&1; then
      echo ">> resetting ${current} to origin/${current}"
      git -C "${REPO_ROOT}" reset --hard "origin/${current}" || true
    fi
  fi
}

if [[ -n "${REPO_ROOT:-}" && -n "${CODE_REPO:-}" ]]; then
  echo ">> syncing repo ${CODE_REPO} -> ${REPO_ROOT}"

  # Make git happy about mounted host-owned dirs
  if command -v git >/dev/null 2>&1; then
    git config --global --add safe.directory "${REPO_ROOT}" || true
  fi

  # New Task Marker Protocol:
  # -------------------------
  # The marker file (.new-task-marker) is created by 'terokctl task new' to signal
  # that this workspace should be reset to the latest remote HEAD. This handles:
  #
  # 1. NEW TASK: Marker exists -> clone or reset to latest HEAD, then remove marker
  # 2. RESTARTED TASK: No marker -> fetch only, preserve local changes
  #
  # This ensures new tasks always start with fresh code while preserving work
  # in progress for restarted containers. It also handles edge cases like stale
  # workspaces from incompletely deleted tasks.
  NEW_TASK_MARKER="${REPO_ROOT}/.new-task-marker"
  IS_NEW_TASK=false
  if [[ -f "${NEW_TASK_MARKER}" ]]; then
    IS_NEW_TASK=true
    echo ">> detected new task marker - will reset to latest HEAD"
  fi

  if [[ ! -d "${REPO_ROOT}/.git" ]]; then
    # No .git directory - perform initial clone
    # Remove marker first so the directory is empty for git clone
    rm -f "${NEW_TASK_MARKER}" 2>/dev/null || true

    # Fresh-task invariant: workspace must be empty before first clone.
    if [[ -n "$(find "${REPO_ROOT}" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null || true)" ]]; then
      echo ">> ERROR: ${REPO_ROOT} is not empty before initial clone" >&2
      find "${REPO_ROOT}" -mindepth 1 -maxdepth 1 -printf '   - %f\n' >&2 || true
      exit 128
    fi

    SRC_REPO="${CLONE_FROM:-${CODE_REPO}}"
    TARGET_BRANCH="${GIT_BRANCH:-}"

    # Clone directly to the target branch if specified, avoiding an extra checkout step
    CLONE_OK=false
    if [[ -n "${TARGET_BRANCH}" ]]; then
      echo ">> initial clone from ${SRC_REPO} (branch: ${TARGET_BRANCH})"
      CLONE_ERR_FILE="$(mktemp)"
      if git clone --recurse-submodules -b "${TARGET_BRANCH}" "${SRC_REPO}" "${REPO_ROOT}" 2>"${CLONE_ERR_FILE}"; then
        CLONE_OK=true
      else
        CLONE_RC=$?
        if grep -Eiq "Remote branch .* not found|couldn't find remote ref" "${CLONE_ERR_FILE}"; then
          echo ">> branch ${TARGET_BRANCH} not found, cloning default branch"
        else
          cat "${CLONE_ERR_FILE}" >&2
          echo ">> ERROR: initial clone failed; aborting without fallback" >&2
          rm -f "${CLONE_ERR_FILE}" 2>/dev/null || true
          exit "${CLONE_RC}"
        fi
      fi
      rm -f "${CLONE_ERR_FILE}" 2>/dev/null || true
    fi

    # Fallback: clone without -b (uses remote's default HEAD)
    if [[ "${CLONE_OK}" != "true" ]]; then
      echo ">> initial clone from ${SRC_REPO}"
      git clone --recurse-submodules "${SRC_REPO}" "${REPO_ROOT}"
    fi

    # If we cloned from a gate, repoint origin to the canonical repo for future updates
    if [[ -n "${CLONE_FROM:-}" && "${CLONE_FROM}" != "${CODE_REPO}" ]]; then
      git -C "${REPO_ROOT}" remote set-url origin "${CODE_REPO}" || true
      git -C "${REPO_ROOT}" remote set-url --push origin "${CODE_REPO}" || true
      # Fetch latest from upstream to ensure we have all refs
      git -C "${REPO_ROOT}" fetch --all --prune || true
      # After repointing, checkout the target branch from the new origin if specified
      if [[ -n "${TARGET_BRANCH}" ]]; then
        if git -C "${REPO_ROOT}" rev-parse --verify "origin/${TARGET_BRANCH}" >/dev/null 2>&1; then
          echo ">> checking out branch ${TARGET_BRANCH}"
          git -C "${REPO_ROOT}" checkout -B "${TARGET_BRANCH}" "origin/${TARGET_BRANCH}"
        fi
      fi
    elif [[ "${CLONE_OK}" != "true" && -n "${TARGET_BRANCH}" ]]; then
      # We cloned with default branch, try to switch to target if it exists
      if git -C "${REPO_ROOT}" rev-parse --verify "origin/${TARGET_BRANCH}" >/dev/null 2>&1; then
        CURRENT=$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
        if [[ "${CURRENT}" != "${TARGET_BRANCH}" ]]; then
          echo ">> checking out branch ${TARGET_BRANCH}"
          git -C "${REPO_ROOT}" checkout -B "${TARGET_BRANCH}" "origin/${TARGET_BRANCH}"
        fi
      else
        echo ">> WARNING: Branch ${TARGET_BRANCH} not found, staying on cloned default"
      fi
    fi

  elif [[ "${IS_NEW_TASK}" == "true" ]]; then
    # .git exists but this is a new task (marker present)
    # This happens when a previous task with the same ID wasn't fully cleaned up.
    # Reset to latest remote HEAD to ensure fresh state.
    echo ">> new task with existing .git - resetting to latest HEAD"
    git -C "${REPO_ROOT}" fetch --all --prune
    TARGET_BRANCH="${GIT_BRANCH:-}"

    # Checkout and reset to the target branch. Use checkout -B to create/reset
    # the local branch to track the remote.
    reset_ok=true
    if [[ -n "${TARGET_BRANCH}" ]]; then
      if git -C "${REPO_ROOT}" rev-parse --verify "origin/${TARGET_BRANCH}" >/dev/null 2>&1; then
        echo ">> checking out branch ${TARGET_BRANCH}"
        if ! git -C "${REPO_ROOT}" checkout -B "${TARGET_BRANCH}" "origin/${TARGET_BRANCH}"; then
          echo ">> WARNING: git checkout failed; preserving new task marker for retry"
          reset_ok=false
        fi
      else
        echo ">> WARNING: Branch origin/${TARGET_BRANCH} not found, staying on current branch"
        reset_to_current_remote
      fi
    else
      # No branch configured - reset to remote HEAD of current branch
      reset_to_current_remote
    fi
    git -C "${REPO_ROOT}" clean -fd || true
    # Remove marker only after successful checkout/reset
    if [[ "${reset_ok}" == "true" ]]; then
      rm -f "${NEW_TASK_MARKER}" 2>/dev/null || true
    fi

  else
    # .git exists and no marker - this is a restarted task
    # Only fetch updates, preserve local changes
    echo ">> restarted task - fetching updates (preserving local changes)"
    git -C "${REPO_ROOT}" fetch --all --prune
    # Only reset if explicitly requested via GIT_RESET_MODE
    if [[ -n "${GIT_BRANCH:-}" && "${GIT_RESET_MODE}" != "none" ]]; then
      echo ">> git reset (${GIT_RESET_MODE}) to origin/${GIT_BRANCH}"
      case "${GIT_RESET_MODE}" in
        hard)
          git -C "${REPO_ROOT}" reset --hard "origin/${GIT_BRANCH}" || true
          ;;
        soft)
          git -C "${REPO_ROOT}" reset "origin/${GIT_BRANCH}" || true
          ;;
      esac
    fi
  fi

  # Gatekeeping mode: Ensure origin remote is set to the git-gate (CODE_REPO).
  # This is necessary because:
  # 1. Existing workspaces might have origin pointing to the real upstream
  #    (e.g., from a previous online mode run or misconfigured setup)
  # 2. In gatekeeping mode, origin should ALWAYS be the local git-gate
  # We detect gatekeeping mode by checking if CODE_REPO is a local file path.
  if [[ "${CODE_REPO}" == file://* ]]; then
    CURRENT_ORIGIN=$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null || echo "")
    if [[ "${CURRENT_ORIGIN}" != "${CODE_REPO}" ]]; then
      echo ">> gatekeeping mode: fixing origin remote (was: ${CURRENT_ORIGIN})"
      git -C "${REPO_ROOT}" remote set-url origin "${CODE_REPO}" || true
      git -C "${REPO_ROOT}" remote set-url --push origin "${CODE_REPO}" || true
    fi
  fi

  # In gatekeeping mode, optionally add an "external" remote pointing to the
  # real upstream. This is informational only - the container cannot actually
  # reach this URL. Useful for IDEs on the host side that want to know the
  # canonical remote without having to track individual task clones.
  if [[ -n "${EXTERNAL_REMOTE_URL:-}" ]]; then
    echo ">> adding 'external' remote: ${EXTERNAL_REMOTE_URL}"
    # Remove existing external remote if present (idempotent)
    git -C "${REPO_ROOT}" remote remove external 2>/dev/null || true
    git -C "${REPO_ROOT}" remote add external "${EXTERNAL_REMOTE_URL}"
  fi

  # Check gate staleness (informational only)
  # This only works when EXTERNAL_REMOTE_URL is set (relaxed gatekeeping mode)
  if [[ -n "${EXTERNAL_REMOTE_URL:-}" && -d "${REPO_ROOT}/.git" ]]; then
    echo ">> checking gate freshness against upstream..."

    # Get local HEAD
    LOCAL_HEAD=$(git -C "${REPO_ROOT}" rev-parse HEAD 2>/dev/null || echo "")

    # Get upstream HEAD using ls-remote (cheap, just queries refs)
    # Use configured branch, or fall back to the current local branch
    TARGET_BRANCH="${GIT_BRANCH:-}"
    if [[ -z "${TARGET_BRANCH}" ]]; then
      TARGET_BRANCH=$(git -C "${REPO_ROOT}" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    fi
    UPSTREAM_HEAD=$(git ls-remote "${EXTERNAL_REMOTE_URL}" "refs/heads/${TARGET_BRANCH}" 2>/dev/null | cut -f1 || echo "")

    if [[ -n "${LOCAL_HEAD}" && -n "${UPSTREAM_HEAD}" ]]; then
      if [[ "${LOCAL_HEAD}" != "${UPSTREAM_HEAD}" ]]; then
        # Try to count commits behind (may fail if we don't have upstream commits locally)
        BEHIND_COUNT=""
        if git -C "${REPO_ROOT}" cat-file -e "${UPSTREAM_HEAD}" 2>/dev/null; then
          BEHIND_COUNT=$(git -C "${REPO_ROOT}" rev-list --count "${LOCAL_HEAD}..${UPSTREAM_HEAD}" 2>/dev/null || echo "")
        fi

        if [[ -n "${BEHIND_COUNT}" && "${BEHIND_COUNT}" != "0" ]]; then
          echo ""
          echo "=========================================="
          echo "NOTE: Gate is ${BEHIND_COUNT} commits behind upstream on ${TARGET_BRANCH}"
          echo "  Local:    ${LOCAL_HEAD:0:8}"
          echo "  Upstream: ${UPSTREAM_HEAD:0:8}"
          echo "  Run 'terokctl gate-sync <project>' on host to update"
          echo "=========================================="
          echo ""
        else
          echo ""
          echo "=========================================="
          echo "NOTE: Gate may be behind upstream on ${TARGET_BRANCH}"
          echo "  Local:    ${LOCAL_HEAD:0:8}"
          echo "  Upstream: ${UPSTREAM_HEAD:0:8}"
          echo "  (Cannot determine exact commit count)"
          echo "=========================================="
          echo ""
        fi
      else
        echo ">> gate is up to date with upstream"
      fi
    elif [[ -z "${UPSTREAM_HEAD}" ]]; then
      echo ">> could not query upstream (network may be restricted)"
    fi
  fi
fi

# Optional toolchain introspection
if command -v gcc >/dev/null 2>&1; then
  echo "gcc: $(gcc --version | head -1)"
fi
if command -v gfortran >/dev/null 2>&1; then
  echo "gfortran: $(gfortran --version | head -1)"
fi
if command -v cmake >/dev/null 2>&1; then
  echo "cmake: $(cmake --version | head -1)"
fi
if command -v clang-format-20 >/dev/null 2>&1; then
  echo "clang-format: $(clang-format-20 --version)"
fi
if command -v nvidia-smi >/dev/null 2>&1; then
  echo "nvidia-smi:"
  nvidia-smi || true
fi
if command -v nvcc >/dev/null 2>&1; then
  echo "nvcc:"
  nvcc --version || true
fi
if command -v nvc >/dev/null 2>&1; then
  echo "nvc:"
  nvc --version || true
fi
if command -v nvfortran >/dev/null 2>&1; then
  echo "nvfortran:"
  nvfortran --version || true
fi

# Signal readiness for host tools that watch initial logs
echo ">> init complete"
exec bash
