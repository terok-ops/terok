# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the per-task ACP path helpers in :mod:`terok.lib.core.paths`."""

from __future__ import annotations

import os
from pathlib import Path

from terok.lib.core.paths import acp_bound_path, acp_socket_path, runtime_dir


class TestACPSocketPath:
    """``acp_socket_path`` constructs the per-task listener socket location."""

    def test_path_lives_under_runtime_dir(self) -> None:
        """The socket lives under the same XDG-compliant runtime root as askpass."""
        path = acp_socket_path("proj1", "task-abc")
        assert path.parts[-3] == "acp"
        assert path.parts[-2] == "proj1"
        assert path.parts[-1] == "task-abc.sock"
        assert path.is_relative_to(runtime_dir())

    def test_path_components_are_separated_per_project(self) -> None:
        """Different projects yield distinct subdirs — one mkdir per project."""
        a = acp_socket_path("proj-a", "task-1")
        b = acp_socket_path("proj-b", "task-1")
        assert a.parent != b.parent
        assert a.parent.name == "proj-a"
        assert b.parent.name == "proj-b"

    def test_distinct_tasks_distinct_filenames(self) -> None:
        """Two tasks within one project share a parent dir, distinct filenames."""
        a = acp_socket_path("proj", "task-1")
        b = acp_socket_path("proj", "task-2")
        assert a.parent == b.parent
        assert a.name == "task-1.sock"
        assert b.name == "task-2.sock"


class TestACPBoundPath:
    """``acp_bound_path`` is the sidecar JSON the daemon writes on bind."""

    def test_co_located_with_socket(self) -> None:
        """Bound sidecar lives next to the socket so both move together."""
        sock = acp_socket_path("proj", "task-1")
        bound = acp_bound_path("proj", "task-1")
        assert sock.parent == bound.parent
        assert bound.name == "task-1.bound"

    def test_path_does_not_create_directories(self, tmp_path: Path) -> None:
        """Path construction is pure — no filesystem side effects."""
        # Redirect runtime_dir() so we can verify nothing gets created.
        os.environ["XDG_RUNTIME_DIR"] = str(tmp_path)
        try:
            acp_bound_path("proj", "task-1")
            # Nothing created — the helpers are pure path arithmetic.
            assert not (tmp_path / "terok" / "acp").exists()
        finally:
            os.environ.pop("XDG_RUNTIME_DIR", None)
