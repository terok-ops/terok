# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the host_cmd subprocess safety guard module."""

import pytest

from terok.lib.util.host_cmd import (
    WORKSPACE_DANGEROUS_DIRNAME,
    assert_not_in_dangerous_workspace,
    is_in_dangerous_workspace,
)


class TestIsInDangerousWorkspace:
    """Tests for the is_in_dangerous_workspace predicate."""

    def test_clean_path(self) -> None:
        """Return False for paths without the sentinel directory."""
        assert not is_in_dangerous_workspace("/home/user/project")

    def test_dangerous_path(self) -> None:
        """Return True when workspace-dangerous is a path component."""
        assert is_in_dangerous_workspace(f"/home/user/tasks/1/{WORKSPACE_DANGEROUS_DIRNAME}")

    def test_dangerous_subdirectory(self) -> None:
        """Return True for subdirectories under workspace-dangerous."""
        assert is_in_dangerous_workspace(
            f"/tmp/terok/tasks/42/{WORKSPACE_DANGEROUS_DIRNAME}/.git/hooks"
        )

    def test_partial_name_no_match(self) -> None:
        """Return False when the sentinel appears as a substring, not a component."""
        # "workspace-dangerous-backup" contains the sentinel string but
        # Path.parts splits on "/" so "workspace-dangerous" would only match
        # if it's an exact path component
        assert not is_in_dangerous_workspace("/home/user/workspace-dangerous-backup")

    def test_exact_dirname_match(self) -> None:
        """Confirm the sentinel constant value."""
        assert WORKSPACE_DANGEROUS_DIRNAME == "workspace-dangerous"


class TestAssertNotInDangerousWorkspace:
    """Tests for the assert_not_in_dangerous_workspace guard."""

    def test_clean_command_passes(self) -> None:
        """No exception for commands that don't reference dangerous paths."""
        assert_not_in_dangerous_workspace(
            ["git", "-C", "/safe/repo", "diff", "HEAD"],
            cwd="/home/user",
        )

    def test_cwd_in_dangerous_workspace_raises(self) -> None:
        """RuntimeError when cwd is inside workspace-dangerous."""
        with pytest.raises(RuntimeError, match="cwd="):
            assert_not_in_dangerous_workspace(
                ["git", "diff"],
                cwd=f"/tmp/tasks/1/{WORKSPACE_DANGEROUS_DIRNAME}",
            )

    def test_dash_c_targeting_dangerous_raises(self) -> None:
        """RuntimeError when -C flag points to workspace-dangerous."""
        with pytest.raises(RuntimeError, match="-C"):
            assert_not_in_dangerous_workspace(
                ["git", "-C", f"/tmp/tasks/1/{WORKSPACE_DANGEROUS_DIRNAME}", "diff"],
            )

    def test_subdirectory_of_dangerous_raises(self) -> None:
        """RuntimeError for -C targeting a subdirectory of workspace-dangerous."""
        with pytest.raises(RuntimeError, match="-C"):
            assert_not_in_dangerous_workspace(
                [
                    "git",
                    "-C",
                    f"/tmp/tasks/1/{WORKSPACE_DANGEROUS_DIRNAME}/subdir",
                    "log",
                ],
            )

    def test_argument_referencing_dangerous_raises(self) -> None:
        """RuntimeError when a positional argument references workspace-dangerous."""
        with pytest.raises(RuntimeError, match="referencing dangerous"):
            assert_not_in_dangerous_workspace(
                ["cat", f"/tmp/tasks/1/{WORKSPACE_DANGEROUS_DIRNAME}/file.txt"],
            )

    def test_no_cwd_clean_passes(self) -> None:
        """No exception when cwd is None and args are clean."""
        assert_not_in_dangerous_workspace(["ls", "/home/user"])
