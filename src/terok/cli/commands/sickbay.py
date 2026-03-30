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
from pathlib import Path

from terok_sandbox import (
    check_environment,
    check_units_outdated,
    get_container_state,
    get_server_status,
    is_systemd_available,
)

from ...lib.core.project_model import ProjectConfig
from ...lib.core.projects import list_projects, load_project
from ...lib.orchestration.hooks import run_hook
from ...lib.orchestration.tasks import container_name, tasks_meta_dir
from ...lib.util.yaml import load as _yaml_load

# Type alias for check results: (severity, label, detail)
_CheckResult = tuple[str, str, str]


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


def _check_gate_server() -> _CheckResult:
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
        return ("warn", label, "not running — run 'terokctl gate start'")
    return ("warn", label, "not running — run 'terokctl gate start'")


def _check_shield() -> _CheckResult:
    """Check egress firewall (terok-shield) environment."""
    label = "Shield"
    try:
        ec = check_environment()
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"check failed — {exc}")
    if ec.health == "bypass":
        return ("warn", label, "bypass_firewall_no_protection is active — egress disabled")
    if ec.health == "stale-hooks":
        return ("warn", label, "hooks outdated — run 'terokctl shield setup --user'")
    if ec.health == "setup-needed":
        hint = (
            ec.setup_hint.splitlines()[0] if ec.setup_hint else "run 'terokctl shield setup --user'"
        )
        return ("warn", label, f"{ec.issues[0] if ec.issues else 'setup needed'} — {hint}")
    if ec.health != "ok":
        return ("warn", label, f"unexpected health: {ec.health}")
    dns = getattr(ec, "dns_tier", "unknown")
    return ("ok", label, f"active ({ec.hooks}, {dns} DNS)")


def _check_task_hook(
    pid: str, tid: str, project: ProjectConfig, *, fix: bool
) -> _CheckResult | None:
    """Check a single task for unfired post_stop hook.  Returns None if ok."""
    meta_path = tasks_meta_dir(pid) / f"{tid}.yml"
    if not meta_path.is_file():
        return None

    try:
        meta = _yaml_load(meta_path.read_text()) or {}
    except Exception:
        return ("warn", f"Task {pid}/{tid}", f"bad metadata: {meta_path}")

    mode = meta.get("mode")
    if not mode:
        return None

    cname = container_name(pid, mode, tid)
    if get_container_state(cname) == "running":
        return None

    fired = meta.get("hooks_fired") or []
    if "post_stop" in fired:
        return None

    label = f"Task {pid}/{tid}"
    if not fix:
        return ("warn", label, "stopped without post_stop hook — run with --fix to reconcile")

    return _reconcile_post_stop(pid, tid, mode, cname, project, meta_path, label)


def _reconcile_post_stop(
    pid: str,
    tid: str,
    mode: str,
    cname: str,
    project: ProjectConfig,
    meta_path: Path,
    label: str,
) -> _CheckResult:
    """Run the missed post_stop hook and return the result."""
    try:
        run_hook(
            "post_stop",
            project.hook_post_stop,
            project_id=pid,
            task_id=tid,
            mode=mode,
            cname=cname,
            task_dir=project.tasks_root / tid,
            meta_path=meta_path,
        )
        return ("ok", label, "post_stop hook reconciled")
    except Exception as exc:
        return ("error", label, f"post_stop hook failed: {exc}")


def _check_unfired_hooks(
    project_id: str | None, task_id: str | None, *, fix: bool
) -> list[_CheckResult]:
    """Check for stopped tasks with unfired post_stop hooks."""
    results: list[_CheckResult] = []

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
            result = _check_task_hook(pid, tid, project, fix=fix)
            if result:
                results.append(result)

    return results


_GLOBAL_CHECKS = [
    _check_gate_server,
    _check_shield,
]

_STATUS_MARKERS = {
    "ok": "ok",
    "warn": "WARN",
    "error": "ERROR",
}


def _update_worst(current: str, status: str) -> str:
    """Return the more severe of *current* and *status*."""
    if status == "error" or current == "error":
        return "error"
    if status == "warn" or current == "warn":
        return "warn"
    return "ok"


def _cmd_sickbay(
    project_id: str | None = None,
    task_id: str | None = None,
    fix: bool = False,
) -> None:
    """Run health checks and report results."""
    worst = "ok"

    if not task_id:
        for check in _GLOBAL_CHECKS:
            status, label, detail = check()
            print(f"  {label} .... {_STATUS_MARKERS.get(status, status)} ({detail})")
            worst = _update_worst(worst, status)

    hook_results = _check_unfired_hooks(project_id, task_id, fix=fix)
    for status, label, detail in hook_results:
        print(f"  {label} .... {_STATUS_MARKERS.get(status, status)} ({detail})")
        worst = _update_worst(worst, status)

    if not hook_results and task_id:
        print(f"  Task {project_id}/{task_id} .... ok (consistent)")

    if worst == "error":
        sys.exit(2)
    elif worst == "warn":
        sys.exit(1)
