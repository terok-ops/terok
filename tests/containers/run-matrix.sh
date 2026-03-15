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
#
# The host must support nested podman (rootless or --privileged).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
IMAGE_PREFIX="terok-test"
SOURCE_MOUNT="/src"
WORKSPACE_DIR="/workspace"
PYTHON_VERSION="3.12"
DEFAULT_MARKER="needs_host_features"
TEROK_INFO_COMMAND="poetry run terokctl info"

# Target distros: name -> Containerfile suffix
declare -A DISTROS=(
    [debian12]="debian12"
    [ubuntu2404]="ubuntu2404"
    [debian13]="debian13"
    [fedora43]="fedora43"
)

# Expected podman versions (for reporting, not enforcement)
declare -A EXPECTED_VERSIONS=(
    [debian12]="4.3.x"
    [ubuntu2404]="4.9.x"
    [debian13]="5.4.x"
    [fedora43]="5.8.x"
)

usage() {
    echo "Usage: $0 [OPTIONS] [DISTRO...]"
    echo ""
    echo "Options:"
    echo "  --build-only   Build images without running tests"
    echo "  --list         List available distros"
    echo "  --host-only    Run only needs_host_features tests (fast)"
    echo "  --podman       Run only needs_podman tests"
    echo "  --all-markers  Run the full integration suite"
    echo "  -h, --help     Show this help"
    echo ""
    echo "Available distros: ${!DISTROS[*]}"
    return 0
}

build_image() {
    local name="$1"
    local file="$SCRIPT_DIR/Containerfile.${DISTROS[$name]}"
    local image="$IMAGE_PREFIX:$name"

    echo "==> Building $image from $file"
    podman build -t "$image" -f "$file" "$REPO_ROOT"
    return 0
}

run_tests() {
    local name="$1"
    local marker="${2:-$DEFAULT_MARKER}"
    local image="$IMAGE_PREFIX:$name"
    local ctr_name="$IMAGE_PREFIX-$name"
    local pytest_args="tests/integration/ -v --tb=short"

    if [[ -n "$marker" ]]; then
        pytest_args="$pytest_args -m '$marker'"
    fi

    echo ""
    echo "==> Testing $name (expected podman ${EXPECTED_VERSIONS[$name]})"
    echo "    marker: ${marker:-<all>}"
    echo ""

    podman run --rm --name "$ctr_name" \
        --privileged \
        --security-opt label=disable \
        -v "$REPO_ROOT:$SOURCE_MOUNT:ro,Z" \
        "$image" \
        bash -c "
            set -e
            echo '--- podman version ---'
            podman --version || echo 'podman not available'
            echo '--- python version ---'
            python3 --version

            cp -a $SOURCE_MOUNT $WORKSPACE_DIR
            cd $WORKSPACE_DIR

            if command -v uv &>/dev/null; then
                uv venv --python $PYTHON_VERSION .venv
                . .venv/bin/activate
                uv pip install poetry
            else
                python3 -m venv .venv
                . .venv/bin/activate
                pip install --quiet --upgrade pip
                pip install --quiet poetry
            fi
            poetry install --with test --quiet 2>&1 | tail -3

            echo ''
            echo '--- running tests ---'
            poetry run pytest $pytest_args
            echo ''
            echo '--- terokctl info ---'
            $TEROK_INFO_COMMAND 2>&1 || true
        "

    echo "==> $name: done"
    return 0
}

BUILD_ONLY=false
LIST_ONLY=false
MARKER="$DEFAULT_MARKER"
TARGETS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build-only) BUILD_ONLY=true ;;
        --list) LIST_ONLY=true ;;
        --host-only) MARKER="$DEFAULT_MARKER" ;;
        --podman) MARKER="needs_podman" ;;
        --all-markers) MARKER="" ;;
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
        echo "Error: unknown distro '$target'. Available: ${!DISTROS[*]}" >&2
        exit 1
    fi
done

for target in "${TARGETS[@]}"; do
    build_image "$target"
done

if $BUILD_ONLY; then
    echo "Images built. Use '$0' without --build-only to run tests."
    exit 0
fi

PASSED=()
FAILED=()

for target in "${TARGETS[@]}"; do
    if run_tests "$target" "$MARKER"; then
        PASSED+=("$target")
    else
        FAILED+=("$target")
    fi
done

echo ""
echo "===== Matrix Summary ====="
for target in "${PASSED[@]}"; do
    echo "  PASS: $target (podman ${EXPECTED_VERSIONS[$target]})"
done
for target in "${FAILED[@]}"; do
    echo "  FAIL: $target (podman ${EXPECTED_VERSIONS[$target]})"
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
    exit 1
fi
