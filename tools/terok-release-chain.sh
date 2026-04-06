#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Cascading release script for the terok package family.
#
# Walks the dependency chain bottom-up, releasing each package in turn:
# bump version, update sibling dep URLs, lock, PR, wait for CI, merge,
# tag, GitHub release, wait for wheel — then move to the next consumer.
#
# Maintains a dedicated clone cache (~/.cache/terok-release/) so it
# never touches dev working trees.
#
# Usage:
#   terok-release-chain [options] <start-repo> [<end-repo>]

set -euo pipefail

# ── Dependency chain ───────────────────────────────────────────────────────
#
# The single source of truth for release ordering and sibling relationships.

CHAIN=(terok-dbus terok-shield terok-sandbox terok-agent terok)

declare -A DEPS=(
    [terok-dbus]=""
    [terok-shield]="terok-dbus"
    [terok-sandbox]="terok-shield"
    [terok-agent]="terok-sandbox"
    [terok]="terok-agent terok-sandbox"
)

# ── Defaults ───────────────────────────────────────────────────────────────

RELEASE_DIR="${TEROK_RELEASE_DIR:-$HOME/.cache/terok-release}"
GH_ORG="${TEROK_GH_ORG:-terok-ai}"
GH_FORK="${TEROK_GH_FORK:-}"

DRY_RUN=false
AUTO_YES=false
AUTO_YES_ALL=false
STOP_AT=""
RELEASE_NAME=""
VERSION_STEP="patch"
VERSION_STEP_UNIFORM=false
CHECK_TIMEOUT=1800
SKIP_CHECKS=false
WHEEL_TIMEOUT=300

# ── Output primitives ─────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

log()     { printf "${CYAN}>>>${RESET} %s\n" "$*"; }
success() { printf "${GREEN}>>>${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}>>>${RESET} %s\n" "$*" >&2; }
die()     { printf "${RED}ERROR:${RESET} %s\n" "$*" >&2; exit 1; }

# Ask for confirmation.  Normal-flow defaults to Y; risky defaults to N.
# --yes auto-approves normal, --yes-all auto-approves both.
ask() {
    local msg="$1" risky="${2:-false}"

    if $DRY_RUN; then
        printf "${YELLOW}[pretend]${RESET} %s\n" "$msg"
        return 0
    fi

    if $risky; then
        if $AUTO_YES_ALL; then
            warn "$msg — force-approved"
            return 0
        fi
        printf "\n${BOLD}%s${RESET} [y/N] " "$msg"
        read -r answer
        [[ "$answer" =~ ^[Yy]$ ]] || die "Aborted by user."
    else
        if $AUTO_YES; then
            log "$msg — auto-approved"
            return 0
        fi
        printf "\n${BOLD}%s${RESET} [Y/n] " "$msg"
        read -r answer
        [[ -z "$answer" || "$answer" =~ ^[Yy]$ ]] || die "Aborted by user."
    fi
}

# Execute a command, or print it in pretend mode.
run() {
    if $DRY_RUN; then
        printf "  ${YELLOW}[pretend]${RESET} %s\n" "$*"
        return 0
    fi
    "$@"
}

# ── Contract ───────────────────────────────────────────────────────────────
#
# Interface agreement with the operator: flags, positionals, environment.
# Placed here so a reader opening the file sees what the script accepts
# before how it works — the Reading Flow's layer 4 at file scope.

usage() {
    printf '%b\n' "$(cat <<USAGE
${BOLD}Usage:${RESET} terok-release-chain [options] <start-repo> [<end-repo>]

  Releases packages bottom-up through the dependency chain.
  When end-repo is given, packages from start through end-1 are fully
  released; end-repo gets a deps-only PR opened for review.

  Uses a dedicated clone cache — never touches your dev working trees.

${BOLD}Arguments:${RESET}
  ${CYAN}start-repo${RESET}              First repo to release (dbus|shield|sandbox|agent)
  ${CYAN}end-repo${RESET}                Stop here with deps-only PR (not released)

${BOLD}Options:${RESET}
  ${GREEN}--version-step${RESET} LEVEL    Semver segment: major | minor | patch (default: patch)
                          Applies to leaf repo only; combine with
                          ${GREEN}--version-step-uniform${RESET} for all repos.
  ${GREEN}-n, --name${RESET} NAME         Release name suffix (prompted if omitted)
  ${GREEN}-y, --yes${RESET}               Auto-approve normal confirmations
  ${GREEN}-Y, --yes-all${RESET}           Auto-approve everything incl. risky actions
  ${GREEN}--check-timeout${RESET} SECS    PR check timeout (default: 1800)
  ${GREEN}--skip-checks${RESET}           Merge PRs without waiting for CI
  ${GREEN}-p, --pretend${RESET}           Dry run — show what would happen
  ${GREEN}-h, --help${RESET}              Show this help

${BOLD}Environment:${RESET}
  ${YELLOW}TEROK_GH_FORK${RESET}           Fork owner ${BOLD}(required)${RESET}
  ${YELLOW}TEROK_RELEASE_DIR${RESET}       Clone cache dir [~/.cache/terok-release]
  ${YELLOW}TEROK_GH_ORG${RESET}            Upstream GitHub org [terok-ai]

${BOLD}Examples:${RESET}
  terok-release-chain dbus           ${CYAN}# patch-release entire chain${RESET}
  terok-release-chain dbus terok     ${CYAN}# release dbus..agent, PR on terok${RESET}
  terok-release-chain --version-step minor dbus
                                     ${CYAN}# minor bump on dbus, patch rest${RESET}
  terok-release-chain --version-step minor --version-step-uniform dbus
                                     ${CYAN}# minor bump on every repo${RESET}
  terok-release-chain -n "Comms" dbus terok -p
                                     ${CYAN}# named dry run, PR on terok${RESET}
USAGE
)"
    exit 1
}

# ── Main ───────────────────────────────────────────────────────────────────
#
# The top-level story: validate environment → parse args → build the chain
# → sync clones → compute versions → preview → execute each repo in turn.

main() {
    preflight
    parse_args "$@"

    local release_chain=()
    build_chain release_chain

    mkdir -p "$RELEASE_DIR"
    log "Release cache: ${RELEASE_DIR}"
    sync_clones "${release_chain[@]}"

    declare -gA RELEASED_VERSIONS=()
    local current_versions=() versions=()
    compute_versions release_chain current_versions versions

    preview_plan release_chain current_versions versions
    ask "Start the release chain?"

    execute_chain release_chain versions
    print_summary release_chain versions
}

# ── Main steps ─────────────────────────────────────────────────────────────

preflight() {
    [[ -n "$GH_FORK" ]] \
        || die "TEROK_GH_FORK is not set (e.g. TEROK_GH_FORK=sliwowitz)"
    for tool in git gh jq; do
        command -v "$tool" >/dev/null 2>&1 || die "${tool} is required but not found"
    done
    local ver
    ver=$(poetry --version 2>/dev/null | sed 's/Poetry (version \(.*\))/\1/') \
        || die "poetry is required but not found"
    local min="2.0.0"
    printf '%s\n%s\n' "$min" "$ver" | sort -V | head -1 | grep -qF "$min" \
        || die "Poetry >= ${min} required (found ${ver})"
}

parse_args() {
    local positionals=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -p|--pretend)            DRY_RUN=true; shift ;;
            -y|--yes)                AUTO_YES=true; shift ;;
            -Y|--yes-all)            AUTO_YES=true; AUTO_YES_ALL=true; shift ;;
            -n|--name)               [[ $# -ge 2 ]] || die "-n/--name requires a value"
                                     RELEASE_NAME="$2"; shift 2 ;;
            --version-step)          [[ $# -ge 2 ]] || die "--version-step requires major|minor|patch"
                                     VERSION_STEP="$2"; shift 2 ;;
            --version-step-uniform)  VERSION_STEP_UNIFORM=true; shift ;;
            --check-timeout)         [[ $# -ge 2 ]] || die "--check-timeout requires seconds"
                                     CHECK_TIMEOUT="$2"; shift 2 ;;
            --skip-checks)           SKIP_CHECKS=true; shift ;;
            --help|-h)               usage ;;
            --)                      shift; positionals+=("$@"); break ;;
            -*)                      die "Unknown option: $1" ;;
            *)                       positionals+=("$1"); shift ;;
        esac
    done

    case ${#positionals[@]} in
        1) START_REPO=$(normalise_repo "${positionals[0]}") ;;
        2) START_REPO=$(normalise_repo "${positionals[0]}")
           STOP_AT=$(normalise_repo "${positionals[1]}") ;;
        *) usage ;;
    esac

    case "$VERSION_STEP" in
        major|minor|patch) ;;
        *) die "Invalid version step: ${VERSION_STEP}. Use major, minor, or patch." ;;
    esac

    if [[ -z "$RELEASE_NAME" ]]; then
        printf "${BOLD}Release name${RESET} (enter for version-only titles): "
        read -r RELEASE_NAME
    fi
}

# Populate the release_chain array from START_REPO through STOP_AT (or end).
build_chain() {
    local -n _chain=$1
    local found=false
    for repo in "${CHAIN[@]}"; do
        [[ "$repo" == "$START_REPO" ]] && found=true
        if $found; then
            _chain+=("$repo")
            [[ "$repo" == "$STOP_AT" ]] && break
        fi
    done
    $found || die "Unknown repo: ${START_REPO}"

    if [[ -n "$STOP_AT" ]]; then
        local stop_found=false
        for repo in "${_chain[@]}"; do
            [[ "$repo" == "$STOP_AT" ]] && stop_found=true
        done
        $stop_found || die "${STOP_AT} is not downstream of ${START_REPO} in the chain"
    fi
}

sync_clones() {
    for repo in "$@"; do
        ensure_clone "$repo"
    done
}

# Leaf repo gets VERSION_STEP; downstream repos get patch (unless --version-step-uniform).
compute_versions() {
    local -n _chain=$1 _current=$2 _new=$3
    local is_leaf=true
    for repo in "${_chain[@]}"; do
        local current level
        current=$(upstream_version "$repo")
        if $is_leaf; then
            level="$VERSION_STEP"
            is_leaf=false
        elif $VERSION_STEP_UNIFORM; then
            level="$VERSION_STEP"
        else
            level="patch"
        fi
        _current+=("$current")
        _new+=("$(bump_version "$current" "$level")")
    done
}

preview_plan() {
    local -n _chain=$1 _current=$2 _new=$3
    printf "\n${BOLD}Release chain plan:${RESET}\n\n"
    for i in "${!_chain[@]}"; do
        local repo="${_chain[$i]}"
        if [[ "$repo" == "$STOP_AT" ]]; then
            printf "  %d. ${CYAN}%-16s${RESET} %s    ${YELLOW}(deps only, no release)${RESET}\n" \
                $((i + 1)) "$repo" "${_current[$i]}"
        else
            local title
            title=$(release_title "${_new[$i]}")
            printf "  %d. ${CYAN}%-16s${RESET} %s -> ${GREEN}%s${RESET}  \"%s\"\n" \
                $((i + 1)) "$repo" "${_current[$i]}" "${_new[$i]}" "$title"
        fi
    done
    printf "\n"
}

execute_chain() {
    local -n _chain=$1 _versions=$2
    for i in "${!_chain[@]}"; do
        local repo="${_chain[$i]}"
        if [[ "$repo" == "$STOP_AT" ]]; then
            prepare_repo "$repo"
            break
        fi
        release_repo "$repo" "${_versions[$i]}"
    done
}

print_summary() {
    local -n _chain=$1 _versions=$2
    printf "\n${GREEN}${BOLD}All releases complete!${RESET}\n\n"
    for i in "${!_chain[@]}"; do
        local repo="${_chain[$i]}"
        if [[ "$repo" == "$STOP_AT" ]]; then
            printf "  ${YELLOW}*${RESET} %s  (deps bumped, PR open)\n" "$repo"
        else
            printf "  ${GREEN}*${RESET} %s v%s\n" "$repo" "${_versions[$i]}"
        fi
    done
    printf "\n"
}

# ── Per-repo workflows ─────────────────────────────────────────────────────
#
# release_repo: full release cycle — version bump, deps, PR, merge, tag, release.
# prepare_repo: deps-only — bump sibling URLs, open PR for manual review.

release_repo() {
    local repo="$1" new_version="$2"
    local repo_dir="${RELEASE_DIR}/${repo}"
    local gh_repo="${GH_ORG}/${repo}"
    local branch="chore/release-${new_version}"
    local tag="v${new_version}"
    local title
    title=$(release_title "$new_version")

    printf "\n${BOLD}════════════════════════════════════════════════════════════════${RESET}\n"
    printf "${BOLD}  Releasing ${CYAN}%s${RESET}${BOLD} %s${RESET}" "$repo" "$new_version"
    [[ -n "$RELEASE_NAME" ]] && printf " ${GREEN}%s${RESET}" "$RELEASE_NAME"
    printf "\n${BOLD}════════════════════════════════════════════════════════════════${RESET}\n\n"

    log "Current version: $(upstream_version "$repo")"
    log "New version:     ${new_version}"
    log "Branch:          ${branch}"
    log "Release title:   ${title}"
    log "New tag:         ${tag}"

    local deps_str="${DEPS[$repo]}"
    if [[ -n "$deps_str" ]]; then
        for dep in $deps_str; do
            local dep_ver="${RELEASED_VERSIONS[$dep]:-}"
            if [[ -n "$dep_ver" ]]; then
                log "Dep update: ${dep} -> v${dep_ver}"
            else
                log "Dep unchanged: ${dep}"
            fi
        done
    fi

    ask "Proceed with ${repo} v${new_version}?"

    run git -C "$repo_dir" checkout -B "$branch" upstream/master

    set_version "$repo_dir" "$new_version"
    update_sibling_deps "$repo_dir" "$deps_str"
    lock_and_commit "$repo_dir" "chore: release ${title}"

    push_and_create_pr "$repo_dir" "$gh_repo" "$branch" "chore: release ${title}" \
        "Automated release bump to v${new_version}."

    local merged_sha=""
    if [[ -n "$PR_URL" ]]; then
        wait_for_checks "$PR_URL" "$gh_repo"
        log "Merging PR..."
        gh pr merge "$PR_URL" --squash --delete-branch --admin
        merged_sha=$(gh pr view "$PR_URL" --repo "$gh_repo" --json mergeCommit --jq '.mergeCommit.oid')
        success "PR merged (${merged_sha:0:12})."
    fi

    tag_and_release "$repo_dir" "$gh_repo" "$tag" "$title" "$merged_sha"
    wait_for_wheel "$repo" "$new_version"

    success "${repo} v${new_version} released!"
    RELEASED_VERSIONS[$repo]="$new_version"
}

prepare_repo() {
    local repo="$1"
    local repo_dir="${RELEASE_DIR}/${repo}"
    local gh_repo="${GH_ORG}/${repo}"
    local current_ver
    current_ver=$(upstream_version "$repo")
    local branch_suffix="${RELEASE_NAME:+${RELEASE_NAME// /-}}"
    local branch="chore/bump-deps${branch_suffix:+-${branch_suffix}}"

    printf "\n${BOLD}════════════════════════════════════════════════════════════════${RESET}\n"
    printf "${BOLD}  Preparing ${CYAN}%s${RESET}${BOLD} %s ${YELLOW}(deps only)${RESET}" "$repo" "$current_ver"
    [[ -n "$RELEASE_NAME" ]] && printf " ${GREEN}%s${RESET}" "$RELEASE_NAME"
    printf "\n${BOLD}════════════════════════════════════════════════════════════════${RESET}\n\n"

    local deps_str="${DEPS[$repo]}"
    [[ -n "$deps_str" ]] || die "${repo} has no sibling deps to bump"

    for dep in $deps_str; do
        local dep_ver="${RELEASED_VERSIONS[$dep]:-}"
        [[ -n "$dep_ver" ]] && log "Dep update: ${dep} -> v${dep_ver}"
    done

    ask "Proceed with dep bump for ${repo}?"

    run git -C "$repo_dir" checkout -B "$branch" upstream/master

    update_sibling_deps "$repo_dir" "$deps_str"
    local commit_msg="chore: bump sibling deps"
    [[ -n "$RELEASE_NAME" ]] && commit_msg="chore: bump sibling deps — ${RELEASE_NAME}"
    lock_and_commit "$repo_dir" "$commit_msg"

    push_and_create_pr "$repo_dir" "$gh_repo" "$branch" "$commit_msg" \
        "Bump sibling dependency wheels. Review and merge manually."

    if [[ -n "$PR_URL" ]]; then
        printf "\n  ${GREEN}PR ready for review:${RESET} %s\n\n" "$PR_URL"
    fi

    success "${repo} dep bump prepared (not merged)."
}

# ── Shared release operations ──────────────────────────────────────────────
#
# Building blocks used by both release_repo and prepare_repo.

update_sibling_deps() {
    local repo_dir="$1" deps_str="$2"
    [[ -n "$deps_str" ]] || return 0
    for dep in $deps_str; do
        local dep_ver="${RELEASED_VERSIONS[$dep]:-}"
        if [[ -z "$dep_ver" ]]; then
            log "Skipping ${dep} (not released in this run)"
            continue
        fi
        update_dep_url "$repo_dir" "$dep" "$dep_ver"
    done
}

lock_and_commit() {
    local repo_dir="$1" message="$2"
    log "Running poetry lock..."
    run bash -c "cd '${repo_dir}' && poetry lock"
    log "Committing..."
    run git -C "$repo_dir" add pyproject.toml poetry.lock
    run git -C "$repo_dir" commit -m "$message"
}

# Push branch to fork and open a PR.  Sets PR_URL (empty in pretend mode).
push_and_create_pr() {
    local repo_dir="$1" gh_repo="$2" branch="$3" title="$4" body="$5"
    PR_URL=""
    log "Pushing to fork..."
    run git -C "$repo_dir" push -u origin "$branch" --force-with-lease
    if $DRY_RUN; then
        return 0
    fi
    log "Creating PR..."
    PR_URL=$(gh pr create \
        --repo "$gh_repo" \
        --base master \
        --head "${GH_FORK}:${branch}" \
        --title "$title" \
        --body "$body")
    log "PR created: ${PR_URL}"
}

tag_and_release() {
    local repo_dir="$1" gh_repo="$2" tag="$3" title="$4" target="${5:-}"
    # Always fetch — the squash-merge commit only exists on the remote
    # until we pull it into the local clone.
    run git -C "$repo_dir" fetch upstream
    if [[ -z "$target" ]]; then
        target="upstream/master"
    fi
    log "Tagging ${tag} on ${target:0:12}..."
    run git -C "$repo_dir" tag -f "$tag" "$target"
    run git -C "$repo_dir" push upstream "$tag"
    log "Creating GitHub release..."
    run gh release create "$tag" \
        --repo "$gh_repo" \
        --title "$title" \
        --generate-notes
}

# ── Clone management ──────────────────────────────────────────────────────
#
# Release clones live in RELEASE_DIR, isolated from dev working trees.
# Created on first use, fetched and hard-reset on subsequent runs.

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

# ── Waiting ────────────────────────────────────────────────────────────────

# Poll PR checks until all pass (or fail).
# Checks may take a few seconds to register after PR creation — empty
# results within a 30s grace period mean "not started yet", not "none".
wait_for_checks() {
    local pr_url="$1" gh_repo="$2"

    if $SKIP_CHECKS; then
        warn "Skipping CI checks (--skip-checks)"
        return 0
    fi
    if $DRY_RUN; then
        printf "  ${YELLOW}[pretend]${RESET} Would wait for PR checks on %s\n" "$pr_url"
        return 0
    fi

    log "Waiting for PR checks (timeout ${CHECK_TIMEOUT}s)..."
    local elapsed=0 timeout="$CHECK_TIMEOUT" grace=30 poll=2 registered=false
    while (( elapsed < timeout )); do
        local json="" gh_exit=0
        json=$(gh pr checks "$pr_url" --repo "$gh_repo" --json name,bucket 2>/dev/null) \
            && gh_exit=0 || gh_exit=$?

        # exit 0 = all passed, exit 8 = pending — both have valid JSON output
        # Any other non-zero exit with empty output is an API/network error
        if (( gh_exit != 0 && gh_exit != 8 )) && [[ -z "$json" ]]; then
            if (( elapsed < grace )); then
                sleep "$poll"
                elapsed=$((elapsed + poll))
                continue
            fi
            die "gh pr checks failed (exit ${gh_exit}) — check network/auth"
        fi

        if [[ "$json" == "[]" || -z "$json" ]]; then
            if (( elapsed < grace )); then
                printf "  ... waiting for checks to register (%ds)\r" "$elapsed"
                sleep "$poll"
                elapsed=$((elapsed + poll))
                continue
            fi
            printf "\33[2K\r"
            success "No checks configured."
            return 0
        fi

        # Clear the "register" line on first transition to "running"
        if ! $registered; then
            printf "\33[2K\r"
            registered=true
        fi

        local pending failing
        pending=$(echo "$json" | jq -r '[.[] | select(.bucket == "pending")] | length')
        failing=$(echo "$json" | jq -r '[.[] | select(.bucket == "fail" or .bucket == "cancel")] | length')

        if (( pending > 0 )); then
            printf "  ... checks running (%ds/%ds)\r" "$elapsed" "$timeout"
            sleep "$poll"
            elapsed=$((elapsed + poll))
            continue
        fi

        printf "\33[2K\r"
        if (( failing == 0 )); then
            success "All PR checks passed!"
            return 0
        fi

        warn "PR checks failed:"
        echo "$json" | jq -r '.[] | select(.bucket == "fail" or .bucket == "cancel") | "  \(.name): \(.bucket)"' >&2
        ask "Checks failed. Force merge anyway?" true
        return 0
    done
    die "Timed out waiting for PR checks after ${timeout}s"
}

# Poll GitHub release assets until the wheel appears.
wait_for_wheel() {
    local repo="$1" version="$2"
    local pkg
    pkg=$(pkg_name "$repo")
    local expected="${pkg}-${version}-py3-none-any.whl"
    local gh_repo="${GH_ORG}/${repo}"

    if $DRY_RUN; then
        printf "  ${YELLOW}[pretend]${RESET} Would wait for %s in %s v%s\n" "$expected" "$gh_repo" "$version"
        return 0
    fi

    log "Waiting for wheel ${expected}..."
    local elapsed=0 poll=10
    while (( elapsed < WHEEL_TIMEOUT )); do
        if (( elapsed % poll == 0 )); then
            local assets
            assets=$(gh release view "v${version}" --repo "$gh_repo" --json assets -q '.assets[].name' 2>/dev/null || true)
            if echo "$assets" | grep -qF "$expected"; then
                printf "\33[2K\r"
                success "Wheel ${expected} is available!"
                return 0
            fi
        fi
        printf "  ... waiting (%ds/%ds)\r" "$elapsed" "$WHEEL_TIMEOUT"
        sleep 1
        elapsed=$((elapsed + 1))
    done
    die "Timed out waiting for wheel ${expected} after ${WHEEL_TIMEOUT}s"
}

# ── Vocabulary ─────────────────────────────────────────────────────────────
#
# Small pure functions that define the domain language used above.

# terok-shield → terok_shield
pkg_name() { echo "${1//-/_}"; }

# Read version from upstream/master without touching the worktree.
upstream_version() {
    git -C "${RELEASE_DIR}/$1" show upstream/master:pyproject.toml \
        | grep -m1 '^version = ' | sed 's/version = "\(.*\)"/\1/'
}

# X.Y.Z → next version at the given semver level.
bump_version() {
    local ver="$1" level="${2:-patch}"
    local major minor patch
    IFS='.' read -r major minor patch <<< "$ver"
    case "$level" in
        major) echo "$((major + 1)).0.0" ;;
        minor) echo "${major}.$((minor + 1)).0" ;;
        patch) echo "${major}.${minor}.$((patch + 1))" ;;
        *) die "Unknown version step: ${level}" ;;
    esac
}

# Format release title: "X.Y.Z" or "X.Y.Z Name".
release_title() {
    local version="$1"
    if [[ -n "$RELEASE_NAME" ]]; then
        echo "${version} ${RELEASE_NAME}"
    else
        echo "$version"
    fi
}

# Accept short names (shield) and full names (terok-shield).
normalise_repo() {
    case "$1" in
        dbus)    echo "terok-dbus" ;;
        shield)  echo "terok-shield" ;;
        sandbox) echo "terok-sandbox" ;;
        agent)   echo "terok-agent" ;;
        terok)   echo "terok" ;;
        terok-dbus|terok-shield|terok-sandbox|terok-agent) echo "$1" ;;
        *) die "Unknown repo: $1. Use: dbus, shield, sandbox, agent, or terok" ;;
    esac
}

set_version() {
    local repo_dir="$1" new_ver="$2"
    log "Setting version to ${new_ver}"
    run sed -i "s/^version = \".*\"/version = \"${new_ver}\"/" "${repo_dir}/pyproject.toml"
}

update_dep_url() {
    local repo_dir="$1" dep_repo="$2" dep_version="$3"
    local dep_pkg
    dep_pkg=$(pkg_name "$dep_repo")
    log "Updating ${dep_repo} dep to v${dep_version}"
    run sed -i "s|${GH_ORG}/${dep_repo}/releases/download/v[^/]*/${dep_pkg}-[^\"]*\\.whl|${GH_ORG}/${dep_repo}/releases/download/v${dep_version}/${dep_pkg}-${dep_version}-py3-none-any.whl|" "${repo_dir}/pyproject.toml"
}

# ── Entry point ────────────────────────────────────────────────────────────

main "$@"
