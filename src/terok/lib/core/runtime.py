# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Process-wide [`ContainerRuntime`][] accessor.

Centralises backend construction so the sandbox-boundary
import-linter ratchet stays tight: every call site asks this module
for its runtime handle instead of instantiating ``PodmanRuntime``
locally.  Selection is env-driven — ``TEROK_RUNTIME=podman`` (default)
or ``TEROK_RUNTIME=null`` for dry-run / test use.

Tests that need a specific backend should call [`set_runtime`][]
in setup and [`reset_runtime`][] in teardown.
"""

from __future__ import annotations

import os

from terok_sandbox import ContainerRuntime, NullRuntime, PodmanRuntime

_runtime: ContainerRuntime | None = None


def get_runtime() -> ContainerRuntime:
    """Return the cached process-wide [`ContainerRuntime`][].

    On first call, inspects ``TEROK_RUNTIME`` and constructs the
    matching backend.  Supported values are ``"podman"`` (the default)
    and ``"null"`` (an in-memory stub useful for CI).  Any other value
    raises [`SystemExit`][] at startup rather than quietly falling
    back — a misspelled env var should be loud.
    """
    global _runtime
    if _runtime is None:
        backend = os.environ.get("TEROK_RUNTIME", "podman").strip().lower()
        if backend == "podman":
            _runtime = PodmanRuntime()
        elif backend == "null":
            _runtime = NullRuntime()
        else:
            raise SystemExit(f"TEROK_RUNTIME={backend!r}: expected 'podman' or 'null'")
    return _runtime


def set_runtime(runtime: ContainerRuntime) -> None:
    """Inject *runtime* as the process-wide handle (for tests)."""
    global _runtime
    _runtime = runtime


def reset_runtime() -> None:
    """Forget the cached runtime so the next ``get_runtime`` rebuilds from env."""
    global _runtime
    _runtime = None
