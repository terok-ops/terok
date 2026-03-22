# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Integration tests for shield hook installation and detection.

These tests verify that ``terokctl shield setup --user`` correctly installs
global OCI hooks and that ``has_global_hooks()`` detects them.  No podman
or nft is required — the tests only exercise filesystem operations.

Runs in CI phase 2 (after hook installation).
"""

import pytest

terok_shield = pytest.importorskip("terok_shield")
has_global_hooks = terok_shield.has_global_hooks
find_hooks_dirs = terok_shield.find_hooks_dirs

pytestmark = [pytest.mark.needs_host_features, pytest.mark.needs_hooks]


class TestHooksInstalled:
    """Verify global hooks are present after ``terokctl shield setup --user``."""

    def test_global_hooks_detected(self) -> None:
        """has_global_hooks() returns True after user-level setup."""
        assert has_global_hooks(), (
            "Global hooks not detected — expected terokctl shield setup --user "
            "to install hooks before running needs_hooks tests.\n"
            f"Searched dirs: {find_hooks_dirs()}"
        )

    def test_hooks_dir_contains_hook_json(self) -> None:
        """At least one hooks directory contains the terok-shield hook JSON."""
        dirs = find_hooks_dirs()
        assert dirs, "No hooks directories found"
        hook_files = [d / "terok-shield-createRuntime.json" for d in dirs]
        assert any(f.is_file() for f in hook_files), f"No hook JSON found in any hooks dir: {dirs}"
