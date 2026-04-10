#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Open a PR chain for cross-cutting development.
#
# Creates a branch in each repo of the dependency chain, wires sibling
# deps as Poetry git-branch references (so `poetry install` resolves
# the tip of each lower-layer branch), and opens a PR per repo.
#
# Shares the clone cache (~/.cache/terok-release/) with the release
# chain script.  During an open PR chain, use `poetry install` for
# development — not pipx.
#
# Usage:
#   terok-chain-open <branch> <start-repo> [<end-repo>]
#   terok-chain-open feat/comms dbus              # full chain
#   terok-chain-open feat/my-feature sandbox terok # sandbox..terok
#   terok-chain-open feat/comms dbus --pretend     # dry run

set -euo pipefail

# ── Dependency chain (mirrors terok-release-chain.sh) ──────────────────

CHAIN=(terok-dbus terok-shield terok-sandbox terok-agent terok)

declare -A DEPS=(
    [terok-dbus]=""
    [terok-shield]="terok-dbus"
    [terok-sandbox]="terok-shield"
    [terok-agent]="terok-sandbox"
    [terok]="terok-agent terok-sandbox terok-dbus"
)

RELEASE_DIR="${TEROK_RELEASE_DIR:-$HOME/.cache/terok-release}"
GH_ORG="${TEROK_GH_ORG:-terok-ai}"
GH_FORK="${TEROK_GH_FORK:-}"
DRY_RUN=false

# ── Output ─────────────────────────────────────────────────────────────

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
RED='\033[0;31m'; BOLD='\033[1m'; RESET='\033[0m'

log()     { printf "${CYAN}>>>${RESET} %s\n" "$*"; }
success() { printf "${GREEN}>>>${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}>>>${RESET} %s\n" "$*" >&2; }
die()     { printf "${RED}ERROR:${RESET} %s\n" "$*" >&2; exit 1; }

run() {
    if $DRY_RUN; then
        printf "  ${YELLOW}[pretend]${RESET} %s\n" "$*"
        return 0
    fi
    "$@"
}

# ── Vocabulary ─────────────────────────────────────────────────────────

pkg_name() { echo "${1//-/_}"; }

normalise_repo() {
    case "$1" in
        dbus)    echo "terok-dbus" ;;
        shield)  echo "terok-shield" ;;
        sandbox) echo "terok-sandbox" ;;
        agent)   echo "terok-agent" ;;
        terok)   echo "terok" ;;
        terok-dbus|terok-shield|terok-sandbox|terok-agent) echo "$1" ;;
        *) die "Unknown repo: $1" ;;
    esac
}

# ── Clone management ──────────────────────────────────────────────────

ensure_clone() {
    local repo="$1"
    local repo_dir="${RELEASE_DIR}/${repo}"
    local upstream_url="git@github.com:${GH_ORG}/${repo}.git"
    local fork_url="git@github.com:${GH_FORK}/${repo}.git"

    if [[ -d "$repo_dir/.git" ]]; then
        printf "  ${CYAN}%-16s${RESET} syncing..." "$repo"
        git -C "$repo_dir" fetch upstream --quiet
        git -C "$repo_dir" reset --hard upstream/master -q
        git -C "$repo_dir" clean -fd --quiet
    else
        printf "  ${CYAN}%-16s${RESET} cloning..." "$repo"
        git clone --quiet "$upstream_url" "$repo_dir"
        git -C "$repo_dir" remote rename origin upstream
        git -C "$repo_dir" remote add origin "$fork_url"
    fi
    printf "\33[2K\r  ${CYAN}%-16s${RESET} ready\n" "$repo"
}

# ── Branch dep wiring ─────────────────────────────────────────────────

# Replace a URL wheel dep with a Poetry git-branch dep.
#   {url = "https://.../terok_sandbox-0.0.50-py3-none-any.whl"}
#     → {git = "https://github.com/FORK/terok-sandbox.git", branch = "BRANCH"}
set_branch_dep() {
    local repo_dir="$1" dep_repo="$2" branch="$3"
    local fork_url="https://github.com/${GH_FORK}/${dep_repo}.git"

    log "Wiring ${dep_repo} -> branch ${branch}"
    # Use dep_repo (hyphens) — pyproject.toml keys are hyphenated.
    run sed -i \
        "s|${dep_repo} = {url = \"https://[^\"]*\"}|${dep_repo} = {git = \"${fork_url}\", branch = \"${branch}\"}|" \
        "${repo_dir}/pyproject.toml"
}

# Check if a repo is in the given chain slice.
in_chain() {
    local target="$1"; shift
    for r in "$@"; do [[ "$r" == "$target" ]] && return 0; done
    return 1
}

# ── Main ───────────────────────────────────────────────────────────────

main() {
    [[ -n "$GH_FORK" ]] \
        || die "TEROK_GH_FORK is not set (e.g. TEROK_GH_FORK=sliwowitz)"

    # Parse flags
    local positionals=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -p|--pretend) DRY_RUN=true; shift ;;
            -h|--help)
                printf "${BOLD}Usage:${RESET} terok-chain-open [--pretend] <branch> <start-repo> [<end-repo>]\n\n"
                printf "Opens a PR chain for cross-cutting development.\n"
                printf "Creates branches, wires git-branch deps, opens PRs.\n\n"
                printf "${BOLD}Environment:${RESET}\n"
                printf "  ${YELLOW}TEROK_GH_FORK${RESET}   Fork owner ${BOLD}(required)${RESET}\n"
                exit 0 ;;
            -*) die "Unknown option: $1" ;;
            *)  positionals+=("$1"); shift ;;
        esac
    done

    (( ${#positionals[@]} >= 2 )) || die "Usage: terok-chain-open <branch> <start-repo> [<end-repo>]"

    local branch="${positionals[0]}"
    local start_repo end_repo
    start_repo=$(normalise_repo "${positionals[1]}")
    end_repo=$(normalise_repo "${positionals[2]:-terok}")

    # Build chain slice
    local chain=() found=false
    for repo in "${CHAIN[@]}"; do
        [[ "$repo" == "$start_repo" ]] && found=true
        $found && chain+=("$repo")
        [[ "$repo" == "$end_repo" ]] && break
    done
    $found || die "Unknown start repo: ${start_repo}"
    in_chain "$end_repo" "${chain[@]}" || die "${end_repo} is not downstream of ${start_repo}"

    printf "\n${BOLD}Opening PR chain:${RESET} %s\n" "$branch"
    printf "  Repos: %s\n\n" "${chain[*]}"

    # Sync clones
    mkdir -p "$RELEASE_DIR"
    for repo in "${chain[@]}"; do ensure_clone "$repo"; done
    printf "\n"

    # Create branches, wire deps, open PRs
    local pr_urls=() is_first=true
    for repo in "${chain[@]}"; do
        local repo_dir="${RELEASE_DIR}/${repo}"
        local gh_repo="${GH_ORG}/${repo}"

        log "${repo}: creating branch ${branch}"
        run git -C "$repo_dir" checkout -B "$branch" upstream/master

        if ! $is_first; then
            local deps_str="${DEPS[$repo]}"
            for dep in $deps_str; do
                in_chain "$dep" "${chain[@]}" && set_branch_dep "$repo_dir" "$dep" "$branch"
            done
            log "${repo}: locking dependencies"
            run bash -c "cd '${repo_dir}' && poetry lock"
            run git -C "$repo_dir" add pyproject.toml poetry.lock
            run git -C "$repo_dir" commit -m "chore: wire ${branch} branch deps"
        fi
        is_first=false

        log "${repo}: pushing to fork"
        run git -C "$repo_dir" push -u origin "$branch" --force-with-lease

        if ! $DRY_RUN; then
            local pr_url
            pr_url=$(gh pr create \
                --repo "$gh_repo" \
                --base master \
                --head "${GH_FORK}:${branch}" \
                --title "${branch}" \
                --body "Part of \`${branch}\` PR chain." 2>&1) \
                || { warn "PR create for ${repo}: ${pr_url}"; pr_url="(exists)"; }
            pr_urls+=("$pr_url")
            success "${repo}: ${pr_url}"
        else
            printf "  ${YELLOW}[pretend]${RESET} Would create PR for %s\n" "$repo"
            pr_urls+=("(pretend)")
        fi
        printf "\n"
    done

    # Summary
    printf "${GREEN}${BOLD}PR chain opened!${RESET}\n\n"
    for i in "${!chain[@]}"; do
        printf "  %s  %s\n" "${chain[$i]}" "${pr_urls[$i]}"
    done
    printf "\n"
}

main "$@"
