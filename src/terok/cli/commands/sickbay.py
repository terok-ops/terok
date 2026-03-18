# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Health check and reconciliation command (DS9-themed diagnostic bay).

Runs a series of checks and reports their status.  With ``--fix``,
auto-remediates issues like unfired post_stop hooks.

Scoping:
- ``terokctl sickbay`` — all projects
- ``terokctl sickbay <project>`` — single project
- ``terokctl sickbay <project> <task>`` — single task

Exit codes:
- 0: all checks passed
- 1: warnings present
- 2: errors present
"""

from __future__ import annotations

import argparse
import sys

from ...lib.containers.hooks import run_hook
from ...lib.containers.runtime import container_name, get_container_state
from ...lib.containers.tasks import tasks_meta_dir
from ...lib.core.projects import list_projects, load_project
from ...lib.facade import check_units_outdated, get_server_status, is_systemd_available
from ...lib.util.yaml import load as _yaml_load


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``sickbay`` subcommand."""
    p = subparsers.add_parser("sickbay", help="Run health checks and reconciliation")
    p.add_argument("project", nargs="?", help="Scope to a single project")
    p.add_argument("task", nargs="?", help="Scope to a single task")
    p.add_argument("--fix", action="store_true", help="Auto-remediate issues")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the sickbay command.  Returns True if handled."""
    if args.cmd != "sickbay":
        return False
    _cmd_sickbay(
        project_id=getattr(args, "project", None),
        task_id=getattr(args, "task", None),
        fix=getattr(args, "fix", False),
    )
    return True


def _check_gate_server() -> tuple[str, str, str]:
    """Check gate server status."""
    status = get_server_status()
    label = "Gate server"
    if status.running:
        outdated = check_units_outdated()
        if outdated:
            return ("warn", label, outdated)
        return ("ok", label, f"{status.mode}, port {status.port}")
    if status.mode == "systemd":
        return ("error", label, "socket installed but not active")
    if is_systemd_available():
        return ("warn", label, "not running — run 'terokctl gate-server install'")
    return ("warn", label, "not running — run 'terokctl gate-server start'")


def _check_unfired_hooks(
    project_id: str | None, task_id: str | None, *, fix: bool
) -> list[tuple[str, str, str]]:
    """Check for stopped tasks with unfired post_stop hooks."""
    results: list[tuple[str, str, str]] = []

    if project_id:
        projects = [(project_id, load_project(project_id))]
    else:
        projects = [(p.id, p) for p in list_projects()]

    for pid, project in projects:
        if not project.hook_post_stop:
            continue

        meta_dir = tasks_meta_dir(pid)
        if not meta_dir.is_dir():
            continue

        task_ids = [f.stem for f in meta_dir.glob("*.yml")] if task_id is None else [task_id]

        for tid in task_ids:
            meta_path = meta_dir / f"{tid}.yml"
            if not meta_path.is_file():
                continue

            try:
                meta = _yaml_load(meta_path.read_text()) or {}
            except Exception:
                results.append(("warn", f"Task {pid}/{tid}", f"bad metadata: {meta_path}"))
                continue
            mode = meta.get("mode")
            if not mode:
                continue

            # Only flag if the container is actually stopped/gone
            cname = container_name(pid, mode, tid)
            state = get_container_state(cname)
            if state == "running":
                continue

            fired = meta.get("hooks_fired") or []
            if "post_stop" in fired:
                continue

            label = f"Task {pid}/{tid}"
            if fix:
                try:
                    run_hook(
                        "post_stop",
                        project.hook_post_stop,
                        project_id=pid,
                        task_id=tid,
                        mode=mode,
                        cname=cname,
                        task_dir=project.tasks_root / str(tid),
                        meta_path=meta_path,
                    )
                    results.append(("ok", label, "post_stop hook reconciled"))
                except Exception as exc:
                    results.append(("error", label, f"post_stop hook failed: {exc}"))
            else:
                results.append(
                    (
                        "warn",
                        label,
                        "stopped without post_stop hook — run with --fix to reconcile",
                    )
                )

    return results


_GLOBAL_CHECKS = [
    _check_gate_server,
]

_STATUS_MARKERS = {
    "ok": "ok",
    "warn": "WARN",
    "error": "ERROR",
}


def _cmd_sickbay(
    project_id: str | None = None,
    task_id: str | None = None,
    fix: bool = False,
) -> None:
    """Run health checks and report results."""
    worst = "ok"

    # Global checks (skip if scoped to a specific task)
    if not task_id:
        for check in _GLOBAL_CHECKS:
            status, label, detail = check()
            marker = _STATUS_MARKERS.get(status, status)
            print(f"  {label} .... {marker} ({detail})")
            if status == "error":
                worst = "error"
            elif status == "warn" and worst != "error":
                worst = "warn"

    # Hook reconciliation
    hook_results = _check_unfired_hooks(project_id, task_id, fix=fix)
    for status, label, detail in hook_results:
        marker = _STATUS_MARKERS.get(status, status)
        print(f"  {label} .... {marker} ({detail})")
        if status == "error":
            worst = "error"
        elif status == "warn" and worst != "error":
            worst = "warn"

    if not hook_results and task_id:
        print(f"  Task {project_id}/{task_id} .... ok (consistent)")

    if worst == "error":
        sys.exit(2)
    elif worst == "warn":
        sys.exit(1)
