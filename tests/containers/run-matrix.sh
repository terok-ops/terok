#!/bin/bash
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0
#
# Multi-distro integration test runner for terok.
#
# Builds test containers for each target distro and runs the
# integration suite inside them. Requires a modern host with
# podman and privileges to run nested containers.
#
# Usage:
#   ./tests/containers/run-matrix.sh               # run all distros
#   ./tests/containers/run-matrix.sh debian12      # run one distro
#   ./tests/containers/run-matrix.sh --build-only  # build images only
#   ./tests/containers/run-matrix.sh --list        # list available distros
#   ./tests/containers/run-matrix.sh --no-cache    # force full rebuild
#
# The host must support nested podman (rootless or --privileged).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMAGE_PREFIX="terok-test"
SOURCE_MOUNT="/src"
WORKSPACE_DIR="/workspace"
PYTHON_VERSION="3.12"
TEROK_DIAGNOSTIC_COMMAND="poetry run terok config"

# ── Terminal colors (disabled when stdout is not a tty) ──
if [[ -t 1 ]]; then
    C_BOLD='\033[1m'
    C_CYAN='\033[1;36m'
    C_YELLOW='\033[1;33m'
    C_GREEN='\033[1;32m'
    C_RED='\033[1;31m'
    C_DIM='\033[2m'
    C_RESET='\033[0m'
else
    C_BOLD='' C_CYAN='' C_YELLOW='' C_GREEN='' C_RED='' C_DIM='' C_RESET=''
fi

# Target distros: name -> Containerfile suffix
declare -A DISTROS=(
    [debian12]="debian12"
    [ubuntu2404]="ubuntu2404"
    [debian13]="debian13"
    [fedora43]="fedora43"
    [podman]="podman"
)

# Expected podman versions (for reporting, not enforcement)
declare -A EXPECTED_VERSIONS=(
    [debian12]="4.3.x"
    [ubuntu2404]="4.9.x"
    [debian13]="5.4.x"
    [fedora43]="5.8.x"
    [podman]="latest"
)

# Non-root user baked into each Containerfile (uid 1000).
# The podman image uses its pre-existing 'podman' user.
declare -A TEST_USERS=(
    [debian12]="testrunner"
    [ubuntu2404]="testrunner"
    [debian13]="testrunner"
    [fedora43]="testrunner"
    [podman]="podman"
)

usage() {
    echo "Usage: $0 [OPTIONS] [DISTRO...]"
    echo ""
    echo "Options:"
    echo "  --build-only   Build images without running tests"
    echo "  --no-cache     Rebuild images from scratch (ignore layer cache)"
    echo "  --list         List available distros"
    echo "  -h, --help     Show this help"
    echo ""
    echo "Default: install full infrastructure, run all integration tests."
    echo ""
    echo "Available distros: ${!DISTROS[*]}"
    return 0
}

build_image() {
    local name="$1"
    local file="$SCRIPT_DIR/Containerfile.${DISTROS[$name]}"
    local image="$IMAGE_PREFIX:$name"
    local -a build_args=()

    $NO_CACHE && build_args+=(--no-cache)

    echo -e "${C_CYAN}==> Building ${C_BOLD}$image${C_CYAN} from $file${C_RESET}"
    podman build "${build_args[@]}" -t "$image" -f "$file" "$REPO_ROOT"
    return $?
}

run_tests() {
    local name="$1"
    local image="$IMAGE_PREFIX:$name"
    local ctr_name="$IMAGE_PREFIX-$name"
    local test_user="${TEST_USERS[$name]}"

    echo ""
    echo -e "${C_CYAN}==> Testing ${C_BOLD}$name${C_CYAN} (expected podman ${EXPECTED_VERSIONS[$name]})${C_RESET}"
    echo -e "    ${C_DIM}user: $test_user${C_RESET}"
    echo ""

    # Three-phase flow:
    #   Phase 1: tests that do NOT need hooks
    #   Phase 2: install global hooks via terok shield setup --user
    #   Phase 3: tests that need hooks
    #
    # Privileged mode gives the outer container the capabilities needed
    # for nested podman, but tests run as uid 1000 (rootless podman).
    podman run --rm --name "$ctr_name" \
        --privileged \
        --security-opt label=disable \
        --device /dev/fuse:rw \
        -e container=podman \
        -v "$REPO_ROOT:$SOURCE_MOUNT:ro,Z" \
        "$image" \
        bash -c "
            set -e

            # ── Prepare workspace (as root) ──
            cp -a $SOURCE_MOUNT $WORKSPACE_DIR
            chown -R $test_user:$test_user $WORKSPACE_DIR

            # Strip IPv6 zone-ID nameservers — they reference host interfaces
            # (e.g. eno1) that don't exist inside the container, causing dig
            # to reject the entire resolv.conf.  Fixed upstream in podman 5.4+
            # (https://github.com/containers/common/pull/2233).
            # Remove once we drop < 5.4 support.
            cp /etc/resolv.conf /tmp/resolv.conf.clean
            grep -v '^nameserver.*%' /tmp/resolv.conf.clean > /etc/resolv.conf

            # ── Run everything as the rootless test user ──
            su - $test_user -c '
                set -e
                export XDG_RUNTIME_DIR=/run/user/\$(id -u)

                cd $WORKSPACE_DIR

                echo \"--- podman version ---\"
                podman --version || echo \"podman not available\"

                echo \"--- rootless podman preflight ---\"
                podman info --format \"podman={{.Version.Version}} storage={{.Store.GraphDriverName}}\" \
                    || { echo \"FATAL: rootless podman not functional\" >&2; exit 1; }

                if command -v uv >/dev/null 2>&1; then
                    uv venv --python $PYTHON_VERSION .venv
                    . .venv/bin/activate
                    uv pip install poetry
                else
                    python\${PYTHON_VERSION} -m venv .venv 2>/dev/null \
                        || python3 -m venv .venv
                    . .venv/bin/activate
                    pip install --quiet --upgrade pip
                    pip install --quiet poetry
                fi

                echo \"--- python version ---\"
                python --version
                poetry install --with test --no-interaction
                echo \"--- deps installed ---\"

                # ── Phase 1: tests without hooks ──
                echo \"\"
                echo \"--- phase 1: tests without hooks ---\"
                poetry run pytest tests/integration/ -v --tb=short -m \"not needs_hooks\"

                # ── Phase 2: install global hooks ──
                echo \"\"
                echo \"--- phase 2: installing shield hooks ---\"
                poetry run terok shield setup --user

                # Verify hooks are detectable — fail fast if setup did not work
                poetry run python3 -c \"
from terok_shield import has_global_hooks
assert has_global_hooks(), \\\"Shield hooks not detected after setup\\\"
print(\\\"Shield hooks verified.\\\")
\"

                # ── Phase 3: tests with hooks ──
                echo \"\"
                echo \"--- phase 3: tests with hooks ---\"
                poetry run pytest tests/integration/ -v --tb=short -m \"needs_hooks\"

                echo \"\"
                echo \"--- terok config ---\"
                $TEROK_DIAGNOSTIC_COMMAND 2>&1 || true
            '
        "

    local status=$?
    if [[ $status -eq 0 ]]; then
        echo -e "${C_GREEN}==> $name: PASS${C_RESET}"
    else
        echo -e "${C_RED}==> $name: FAIL${C_RESET}" >&2
    fi
    return "$status"
}

BUILD_ONLY=false
LIST_ONLY=false
NO_CACHE=false
TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-only) BUILD_ONLY=true ;;
        --no-cache) NO_CACHE=true ;;
        --list) LIST_ONLY=true ;;
        -h|--help) usage; exit 0 ;;
        *) TARGETS+=("$1") ;;
    esac
    shift
done

if $LIST_ONLY; then
    for name in "${!DISTROS[@]}"; do
        echo "$name (podman ${EXPECTED_VERSIONS[$name]})"
    done | sort
    exit 0
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
    TARGETS=("${!DISTROS[@]}")
fi

for target in "${TARGETS[@]}"; do
    if [[ -z "${DISTROS[$target]+x}" ]]; then
        echo -e "${C_RED}Error: unknown distro '$target'. Available: ${!DISTROS[*]}${C_RESET}" >&2
        exit 1
    fi
done

for target in "${TARGETS[@]}"; do
    build_image "$target"
done

if $BUILD_ONLY; then
    echo -e "${C_GREEN}Images built.${C_RESET} Use '$0' without --build-only to run tests."
    exit 0
fi

PASSED=()
FAILED=()

for target in "${TARGETS[@]}"; do
    if run_tests "$target"; then
        PASSED+=("$target")
    else
        FAILED+=("$target")
    fi
done

echo ""
echo -e "${C_BOLD}===== Matrix Summary =====${C_RESET}"
for target in "${PASSED[@]}"; do
    echo -e "  ${C_GREEN}PASS${C_RESET}: $target ${C_DIM}(podman ${EXPECTED_VERSIONS[$target]})${C_RESET}"
done
for target in "${FAILED[@]}"; do
    echo -e "  ${C_RED}FAIL${C_RESET}: $target ${C_DIM}(podman ${EXPECTED_VERSIONS[$target]})${C_RESET}"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    exit 1
fi
