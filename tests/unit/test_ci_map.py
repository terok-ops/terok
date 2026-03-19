# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the CI workflow map generator (mkdocs_terok.ci_map)."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from mkdocs_terok.ci_map import (
    _artifact_names,
    _render,
    _trigger_summary,
    generate_ci_map,
    load_workflows,
)

from terok.lib.util.yaml import load as yaml_load


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        pytest.param({"on": "push"}, "`push`", id="string-on"),
        pytest.param({"on": ["push", "pull_request"]}, "`push`, `pull_request`", id="list-on"),
        pytest.param(
            yaml_load("on:\n  push:\n    branches: [master]\n"),
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
    data: dict[object, object],
    expected: str,
) -> None:
    """Trigger summaries compactly describe the workflow trigger shape."""
    assert _trigger_summary(data) == expected


def test_artifact_names_extract_named_and_default_artifacts() -> None:
    """Artifact extraction returns explicit names and a fallback marker."""
    steps = [
        {"uses": "actions/upload-artifact@v4", "with": {"name": "coverage"}},
        {"uses": "actions/upload-artifact@v4", "with": {}},
        {"uses": "actions/download-artifact@v5", "with": {"name": "ruff"}},
        {"uses": "actions/download-artifact@v5"},
    ]

    assert _artifact_names(steps, "actions/upload-artifact") == (
        "coverage",
        "(all artifacts)",
    )
    assert _artifact_names(steps, "actions/download-artifact") == (
        "ruff",
        "(all artifacts)",
    )


def test_render_formats_cells() -> None:
    """Rendered table cells join values with Markdown line breaks."""
    assert _render(()) == "—"
    assert _render(("unit-tests", "ruff")) == "`unit-tests`<br>`ruff`"


def test_load_workflows_normalizes_jobs_and_artifacts(tmp_path: Path) -> None:
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

    workflows = load_workflows(workflows_dir)

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


def test_generate_ci_map_renders_tables() -> None:
    """Generated Markdown includes workflow and job rows."""
    report = generate_ci_map(
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
