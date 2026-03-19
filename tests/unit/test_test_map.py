# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the integration test map generator (mkdocs_terok.test_map)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from mkdocs_terok.test_map import (
    TestMapConfig,
    _group_by_directory,
    _sorted_dirs,
    _test_row,
    collect_tests,
    generate_test_map,
)

from tests.testfs import MOCK_BASE


def test_collect_tests_filters_output_and_uses_integration_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collection should call pytest on the integration dir and keep only node IDs."""
    fake_root = MOCK_BASE / "docs-root"
    config = TestMapConfig(root=fake_root)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((command, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "tests/integration/tasks/test_lifecycle.py::test_create\n"
                "tests/integration/cli/test_cli.py::TestCLI::test_help\n"
                "collected 2 items\n"
            ),
            stderr="",
        )

    with patch("mkdocs_terok.test_map.subprocess.run", fake_run):
        result = collect_tests(config=config)

    assert result == [
        "tests/integration/tasks/test_lifecycle.py::test_create",
        "tests/integration/cli/test_cli.py::TestCLI::test_help",
    ]
    cmd = calls[0][0]
    assert cmd[2] == "pytest"
    assert str(fake_root / "tests" / "integration") in cmd


def test_collect_tests_raises_with_pytest_output_on_failure() -> None:
    """Collection failures should surface pytest output for debugging."""
    with (
        patch(
            "mkdocs_terok.test_map.subprocess.run",
            return_value=SimpleNamespace(
                returncode=2,
                stdout="stdout details\n",
                stderr="stderr details\n",
            ),
        ),
        pytest.raises(RuntimeError, match="pytest collection failed \\(exit 2\\)"),
    ):
        collect_tests()


def test_group_by_directory_groups_root_and_subdirs() -> None:
    """Collected node IDs should be grouped by the first integration path segment."""
    groups = _group_by_directory(
        [
            "tests/integration/tasks/test_lifecycle.py::test_create",
            "tests/integration/tasks/test_lifecycle.py::test_delete",
            "tests/integration/test_root.py::test_root_only",
        ]
    )

    assert groups == {
        "tasks": [
            "tests/integration/tasks/test_lifecycle.py::test_create",
            "tests/integration/tasks/test_lifecycle.py::test_delete",
        ],
        "(root)": ["tests/integration/test_root.py::test_root_only"],
    }


def test_sorted_dirs_orders_known_before_unknown() -> None:
    """Known directories should keep canonical order before unknown directories."""
    groups = {
        "launch": ["x"],
        "alpha": ["y"],
        "cli": ["z"],
        "projects": ["w"],
    }
    dir_order = ("cli", "projects", "tasks", "setup", "gate", "launch")

    assert _sorted_dirs(groups, dir_order) == [
        "cli",
        "projects",
        "launch",
        "alpha",
    ]


@pytest.mark.parametrize(
    ("test_id", "expected"),
    [
        pytest.param(
            "tests/integration/tasks/test_lifecycle.py::TestLifecycle::test_create",
            "| `test_create` | `TestLifecycle` | `tests/integration/tasks/test_lifecycle.py` |",
            id="class-test",
        ),
        pytest.param(
            "tests/integration/test_root.py::test_root_only",
            "| `test_root_only` | `` | `tests/integration/test_root.py` |",
            id="module-test",
        ),
    ],
)
def test_format_test_row(test_id: str, expected: str) -> None:
    """Formatted rows should expose test, class, and file columns (no markers)."""
    assert _test_row(test_id, {}, Path.cwd(), show_markers=False) == expected


def test_generate_test_map_uses_collect_tests_when_needed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The generator should collect tests on demand when none are provided."""
    test_ids = ["tests/integration/cli/test_cli.py::test_help"]
    config = TestMapConfig(show_markers=False)

    class FixedDateTime:
        """Minimal datetime stub returning a deterministic UTC timestamp."""

        @staticmethod
        def now(_tz: object) -> datetime:
            return datetime(2026, 3, 15, 12, 0, tzinfo=UTC)

    monkeypatch.setattr("mkdocs_terok.test_map.datetime", FixedDateTime)

    with patch("mkdocs_terok.test_map.collect_tests", return_value=test_ids):
        report = generate_test_map(config=config)

    assert "*Generated: 2026-03-15 12:00 UTC*" in report
    assert "**1 tests** across **1 directories**" in report
    assert "## `cli/`" in report
    assert "| `test_help` | `` | `tests/integration/cli/test_cli.py` |" in report


def test_generate_test_map_renders_directory_descriptions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Directory descriptions should appear in their matching Markdown sections."""
    test_ids = [
        "tests/integration/tasks/test_lifecycle.py::test_create",
        "tests/integration/cli/test_cli.py::TestCLI::test_help",
    ]
    # Create README.md files for directory descriptions
    integration_dir = tmp_path / "tests" / "integration"
    for subdir, desc in [("cli", "CLI smoke coverage"), ("tasks", "Task lifecycle coverage")]:
        d = integration_dir / subdir
        d.mkdir(parents=True)
        (d / "README.md").write_text(f"# {subdir}\n{desc}\n")

    config = TestMapConfig(
        root=tmp_path,
        dir_order=("cli", "tasks"),
        show_markers=False,
    )

    class FixedDateTime:
        """Minimal datetime stub returning a deterministic UTC timestamp."""

        @staticmethod
        def now(_tz: object) -> datetime:
            return datetime(2026, 3, 15, 13, 30, tzinfo=UTC)

    monkeypatch.setattr("mkdocs_terok.test_map.datetime", FixedDateTime)

    report = generate_test_map(test_ids, config=config)

    assert report.startswith("# Integration Test Map\n\n*Generated: 2026-03-15 13:30 UTC*")
    assert report.index("## `cli/`") < report.index("## `tasks/`")
    assert "CLI smoke coverage" in report
    assert "Task lifecycle coverage" in report
    assert "| `test_help` | `TestCLI` | `tests/integration/cli/test_cli.py` |" in report
    assert "| `test_create` | `` | `tests/integration/tasks/test_lifecycle.py` |" in report
