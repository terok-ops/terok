# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CI workflow map generator."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from textwrap import dedent
from types import ModuleType

import pytest
import yaml


def _load_ci_map_module() -> ModuleType:
    """Load ``docs/ci_map.py`` as a module for direct function testing."""
    path = Path(__file__).resolve().parents[2] / "docs" / "ci_map.py"
    spec = importlib.util.spec_from_file_location("ci_map", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ci_map_module() -> ModuleType:
    """Return the loaded CI map module."""
    return _load_ci_map_module()


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param({"on": "push"}, "`push`", id="string-on"),
        pytest.param({"on": ["push", "pull_request"]}, "`push`, `pull_request`", id="list-on"),
        pytest.param(
            yaml.safe_load("on:\n  push:\n    branches: [master]\n"),
            "`push(master)`",
            id="yaml-bool-key-push",
        ),
        pytest.param(
            {
                "on": {
                    "workflow_run": {"workflows": ["Tests & Analysis"]},
                    "pull_request": {"branches": ["master"]},
                }
            },
            "`workflow_run(Tests & Analysis)`, `pull_request(master)`",
            id="dict-on",
        ),
    ],
)
def test_trigger_summary(
    ci_map_module: ModuleType,
    data: dict[object, object],
    expected: str,
) -> None:
    """Trigger summaries compactly describe the workflow trigger shape."""
    assert ci_map_module._trigger_summary(data) == expected


def test_artifact_names_extract_named_and_default_artifacts(ci_map_module: ModuleType) -> None:
    """Artifact extraction returns explicit names and a fallback marker."""
    steps = [
        {"uses": "actions/upload-artifact@v4", "with": {"name": "coverage"}},
        {"uses": "actions/upload-artifact@v4", "with": {}},
        {"uses": "actions/download-artifact@v5", "with": {"name": "ruff"}},
        {"uses": "actions/download-artifact@v5"},
    ]

    assert ci_map_module._artifact_names(steps, "actions/upload-artifact") == (
        "coverage",
        "(all artifacts)",
    )
    assert ci_map_module._artifact_names(steps, "actions/download-artifact") == (
        "ruff",
        "(all artifacts)",
    )


def test_render_formats_cells(ci_map_module: ModuleType) -> None:
    """Rendered table cells join values with Markdown line breaks."""
    assert ci_map_module._render(()) == "—"
    assert ci_map_module._render(("unit-tests", "ruff")) == "`unit-tests`<br>`ruff`"


def test_load_workflows_normalizes_jobs_and_artifacts(
    ci_map_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Workflow loading normalizes needs and collects artifact names."""
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "analysis.yml").write_text(
        dedent(
            """
            name: Tests & Analysis
            on:
              pull_request:
                branches: [master]
            jobs:
              unit-tests:
                name: Unit tests
                steps:
                  - uses: actions/upload-artifact@v4
                    with:
                      name: coverage
              sonar:
                needs: unit-tests
                steps:
                  - uses: actions/download-artifact@v5
                    with:
                      name: coverage
              bandit:
                needs: [unit-tests, sonar]
                steps: []
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ci_map_module, "WORKFLOWS_DIR", workflows_dir)

    workflows = ci_map_module.load_workflows()

    assert workflows == [
        {
            "file_name": "analysis.yml",
            "name": "Tests & Analysis",
            "triggers": "`pull_request(master)`",
            "jobs": [
                {
                    "name": "Unit tests",
                    "needs": (),
                    "uploads": ("coverage",),
                    "downloads": (),
                },
                {
                    "name": "sonar",
                    "needs": ("unit-tests",),
                    "uploads": (),
                    "downloads": ("coverage",),
                },
                {
                    "name": "bandit",
                    "needs": ("unit-tests", "sonar"),
                    "uploads": (),
                    "downloads": (),
                },
            ],
        }
    ]


def test_generate_ci_map_renders_tables(ci_map_module: ModuleType) -> None:
    """Generated Markdown includes workflow and job rows."""
    report = ci_map_module.generate_ci_map(
        [
            {
                "file_name": "analysis.yml",
                "name": "Tests & Analysis",
                "triggers": "`pull_request(master)`",
                "jobs": [
                    {
                        "name": "Unit tests",
                        "needs": (),
                        "uploads": ("coverage",),
                        "downloads": (),
                    },
                    {
                        "name": "SonarQube Cloud",
                        "needs": ("unit-tests", "ruff", "bandit"),
                        "uploads": (),
                        "downloads": ("coverage", "ruff", "bandit"),
                    },
                ],
            }
        ]
    )

    assert "# CI Workflow Map" in report
    assert "**1 workflows** with **2 jobs**" in report
    assert "| `Tests & Analysis` | `analysis.yml` | `pull_request(master)` | 2 |" in report
    assert "| `Tests & Analysis` | `Unit tests` | — | `coverage` | — |" in report
    assert (
        "| `Tests & Analysis` | `SonarQube Cloud` | "
        "`unit-tests`<br>`ruff`<br>`bandit` | — | "
        "`coverage`<br>`ruff`<br>`bandit` |"
    ) in report
