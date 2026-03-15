#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate a Markdown map of GitHub workflows and jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS_DIR = ROOT / ".github" / "workflows"


def _artifact_names(steps: object, prefix: str) -> tuple[str, ...]:
    """Collect upload/download artifact names from a list of steps."""
    if not isinstance(steps, list):
        return ()
    names: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        uses = step.get("uses")
        if not isinstance(uses, str) or not uses.startswith(prefix):
            continue
        with_section = step.get("with", {})
        if isinstance(with_section, dict):
            name = with_section.get("name")
            names.append(str(name) if name else "(all artifacts)")
    return tuple(names)


def _trigger_summary(data: dict[object, object]) -> str:
    """Render the top-level ``on`` section as a compact string."""
    on_section = data.get("on", data.get(True, {}))
    if isinstance(on_section, str):
        return f"`{on_section}`"
    if isinstance(on_section, list):
        return ", ".join(f"`{item}`" for item in on_section)
    if not isinstance(on_section, dict):
        return "—"

    parts: list[str] = []
    for name, value in on_section.items():
        suffix = ""
        if isinstance(value, dict):
            if name in {"push", "pull_request", "pull_request_target"}:
                branches = value.get("branches")
                if isinstance(branches, list) and branches:
                    suffix = f"({', '.join(str(branch) for branch in branches)})"
            elif name == "workflow_run":
                workflows = value.get("workflows")
                if isinstance(workflows, list) and workflows:
                    suffix = f"({', '.join(str(workflow) for workflow in workflows)})"
        parts.append(f"`{name}{suffix}`")
    return ", ".join(parts) if parts else "—"


def load_workflows() -> list[dict[str, object]]:
    """Load workflow and job facts from ``.github/workflows/*.yml``."""
    workflows: list[dict[str, object]] = []
    for path in sorted(WORKFLOWS_DIR.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            continue

        jobs: list[dict[str, object]] = []
        jobs_section = data.get("jobs", {})
        if isinstance(jobs_section, dict):
            for job_id, job_data in jobs_section.items():
                if not isinstance(job_data, dict):
                    continue
                needs = job_data.get("needs", ())
                needs_tuple = (
                    (needs,)
                    if isinstance(needs, str)
                    else tuple(str(item) for item in needs)
                    if isinstance(needs, list)
                    else ()
                )
                jobs.append(
                    {
                        "name": str(job_data.get("name", job_id)),
                        "needs": needs_tuple,
                        "uploads": _artifact_names(
                            job_data.get("steps"), "actions/upload-artifact"
                        ),
                        "downloads": _artifact_names(
                            job_data.get("steps"), "actions/download-artifact"
                        ),
                    }
                )

        workflows.append(
            {
                "file_name": path.name,
                "name": str(data.get("name", path.stem)),
                "triggers": _trigger_summary(data),
                "jobs": jobs,
            }
        )
    return workflows


def _render(values: tuple[str, ...]) -> str:
    """Render a tuple of values into one Markdown table cell."""
    return "<br>".join(f"`{value}`" for value in values) if values else "—"


def generate_ci_map(workflows: list[dict[str, object]] | None = None) -> str:
    """Generate the Markdown CI map."""
    workflows = load_workflows() if workflows is None else workflows
    job_count = sum(len(workflow["jobs"]) for workflow in workflows)
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# CI Workflow Map\n\n",
        f"*Generated: {now}*\n\n",
        f"**{len(workflows)} workflows** with **{job_count} jobs**\n\n",
        "## Workflows\n\n",
        "| Workflow | File | Triggers | Jobs |\n",
        "|---|---|---|---|\n",
    ]
    for workflow in workflows:
        lines.append(
            f"| `{workflow['name']}` | `{workflow['file_name']}` | "
            f"{workflow['triggers']} | {len(workflow['jobs'])} |\n"
        )

    lines.extend(
        [
            "\n## Jobs\n\n",
            "| Workflow | Job | Needs | Uploads | Downloads |\n",
            "|---|---|---|---|---|\n",
        ]
    )
    for workflow in workflows:
        for job in workflow["jobs"]:
            lines.append(
                f"| `{workflow['name']}` | `{job['name']}` | {_render(job['needs'])} | "
                f"{_render(job['uploads'])} | {_render(job['downloads'])} |\n"
            )

    lines.append("\n")
    return "".join(lines)


if __name__ == "__main__":
    out_path = ROOT / "docs" / "ci_map.md"
    out_path.write_text(generate_ci_map(), encoding="utf-8")
    print(f"Wrote {out_path}")
