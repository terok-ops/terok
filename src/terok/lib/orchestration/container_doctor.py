# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""In-container health checks via the layered doctor protocol.

Collects checks from ``terok_sandbox.doctor`` (network, shield),
``terok_agent.doctor`` (bridges, credentials, env), and adds terok-level
checks (git identity, remote URL).  Executes probes inside running
containers via ``podman exec`` and optionally applies fixes.

All checks run from the host — the container cannot tamper with the
diagnostic process.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from terok_agent import agent_doctor_checks, get_roster
from terok_sandbox import get_container_state, get_proxy_port, get_ssh_agent_port, make_shield
from terok_sandbox.doctor import CheckVerdict, DoctorCheck, sandbox_doctor_checks

from ..core.config import make_sandbox_config
from ..core.projects import load_project
from ..util.logging_utils import _log_debug
from .tasks import container_name, load_task_meta, tasks_meta_dir

# Type alias matching sickbay.py convention
_CheckResult = tuple[str, str, str]

_SHIELD_STATE_FILENAME = "shield_desired_state"


# ---------------------------------------------------------------------------
# podman exec helper
# ---------------------------------------------------------------------------


def _exec_in_container(
    cname: str,
    cmd: list[str],
    *,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* inside *cname* via ``podman exec`` and return the result."""
    return subprocess.run(
        ["podman", "exec", cname, *cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Terok-level checks
# ---------------------------------------------------------------------------


def _git_identity_check(
    expected_name: str,
    expected_email: str,
    field: str,
) -> DoctorCheck:
    """Check a git identity field (user.name or user.email) against expected."""
    git_key = f"user.{field}"

    def _eval(rc: int, stdout: str, stderr: str) -> CheckVerdict:
        """Compare git config value against expected."""
        actual = stdout.strip()
        expected = expected_name if field == "name" else expected_email
        if rc != 0 or not actual:
            return CheckVerdict("warn", f"git {git_key}: not set")
        if actual == expected:
            return CheckVerdict("ok", f"git {git_key}: {actual}")
        return CheckVerdict(
            "warn",
            f"git {git_key}: {actual!r} (expected {expected!r})",
            fixable=True,
        )

    fix_value = expected_name if field == "name" else expected_email
    return DoctorCheck(
        category="git",
        label=f"Git {git_key}",
        probe_cmd=["git", "-C", "/workspace", "config", git_key],
        evaluate=_eval,
        fix_cmd=["git", "-C", "/workspace", "config", git_key, fix_value],
        fix_description=f"Set git {git_key} to {fix_value!r}.",
    )


def _git_remote_check(security_class: str, gate_port: int | None) -> DoctorCheck:
    """Check that git origin remote matches the expected pattern for the security class."""

    def _eval(rc: int, stdout: str, stderr: str) -> CheckVerdict:
        """Compare remote URL against expected pattern."""
        url = stdout.strip()
        if rc != 0 or not url:
            return CheckVerdict("warn", "git origin: no remote configured")
        if security_class == "gatekeeping":
            # Gate URL: http://<token>@host.containers.internal:<port>/<name>
            if "host.containers.internal" in url:
                return CheckVerdict("ok", "git origin: routed through gate")
            return CheckVerdict(
                "error",
                f"git origin: {url!r} bypasses gate — should use host.containers.internal",
                fixable=False,
            )
        # Online mode: any URL is acceptable
        return CheckVerdict("ok", f"git origin: {url}")

    return DoctorCheck(
        category="git",
        label="Git remote URL",
        probe_cmd=["git", "-C", "/workspace", "remote", "get-url", "origin"],
        evaluate=_eval,
    )


def _terok_doctor_checks(
    project_id: str,
    task_id: str,
) -> list[DoctorCheck]:
    """Build terok-level health checks from project config."""
    project = load_project(project_id)

    checks: list[DoctorCheck] = []

    # Git identity checks — use human identity as baseline for the check
    # (the actual author/committer depends on authorship mode + agent, but
    # user.name/email in git config is set by the init script)
    human_name = project.human_name or "Nobody"
    human_email = project.human_email or "nobody@localhost"
    checks.append(_git_identity_check(human_name, human_email, "name"))
    checks.append(_git_identity_check(human_name, human_email, "email"))

    # Git remote URL check
    from ..core.config import get_gate_server_port as _get_gate_port

    gate_port = _get_gate_port()
    checks.append(_git_remote_check(project.security_class, gate_port))

    return checks


# ---------------------------------------------------------------------------
# Shield state helper (host-side)
# ---------------------------------------------------------------------------


def _read_desired_shield_state(task_dir: Path) -> str | None:
    """Read the ``shield_desired_state`` file, or ``None`` if absent."""
    path = task_dir / _SHIELD_STATE_FILENAME
    try:
        return path.read_text(encoding="utf-8").strip() if path.is_file() else None
    except OSError:
        return None


def _check_shield_state(task_dir: Path, cname: str) -> _CheckResult | None:
    """Run the host-side shield state check.  Returns None if no desired state."""
    desired = _read_desired_shield_state(task_dir)
    if desired is None:
        return ("ok", "Shield state", "no desired state — not managed")

    try:
        shield = make_shield(task_dir)
        actual_status = shield.status(cname)
        actual = "up" if actual_status.get("active", False) else "down"
    except Exception as exc:  # noqa: BLE001
        return ("warn", "Shield state", f"status check failed: {exc}")

    if actual == desired or (desired == "down_all" and actual == "down"):
        return ("ok", "Shield state", f"matches desired ({desired})")
    return ("warn", "Shield state", f"mismatch: actual={actual!r}, desired={desired!r}")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_container_doctor(
    project_id: str,
    task_id: str,
    *,
    fix: bool = False,
) -> list[_CheckResult]:
    """Run all layered in-container health checks for a specific task.

    Collects checks from sandbox, agent, and terok layers, executes probes
    via ``podman exec``, evaluates results, and optionally applies fixes.

    Returns a list of ``(severity, label, detail)`` tuples for display.
    """
    # Load task metadata
    meta_dir = tasks_meta_dir(project_id)
    meta_path = meta_dir / f"{task_id}.yml"
    if not meta_path.is_file():
        return [("warn", f"Task {project_id}/{task_id}", "metadata not found")]

    meta, _ = load_task_meta(project_id, task_id)
    mode = meta.get("mode")
    if not mode:
        return [("warn", f"Task {project_id}/{task_id}", "never started (no mode)")]

    cname = container_name(project_id, mode, task_id)
    state = get_container_state(cname)
    if state != "running":
        return [("info", f"Task {project_id}/{task_id}", f"not running ({state}) — skipped")]

    # Collect checks from all layers
    cfg = make_sandbox_config()
    proxy_port = get_proxy_port(cfg)
    ssh_agent_port = get_ssh_agent_port(cfg)

    project = load_project(project_id)
    task_dir = project.tasks_root / str(task_id)
    desired_shield = _read_desired_shield_state(task_dir)

    all_checks: list[DoctorCheck] = []
    all_checks.extend(
        sandbox_doctor_checks(
            proxy_port=proxy_port,
            ssh_agent_port=ssh_agent_port,
            desired_shield_state=desired_shield,
        )
    )
    all_checks.extend(
        agent_doctor_checks(
            get_roster(),
            proxy_port=proxy_port,
        )
    )
    all_checks.extend(_terok_doctor_checks(project_id, task_id))

    results: list[_CheckResult] = []

    for check in all_checks:
        # Host-side checks (like shield) don't use podman exec
        if check.host_side:
            shield_result = _check_shield_state(task_dir, cname)
            if shield_result:
                results.append(shield_result)
            continue

        # Execute probe inside container
        try:
            proc = _exec_in_container(cname, check.probe_cmd)
            verdict = check.evaluate(proc.returncode, proc.stdout, proc.stderr)
        except subprocess.TimeoutExpired:
            verdict = CheckVerdict("warn", f"{check.label}: probe timed out")
        except (FileNotFoundError, OSError) as exc:
            verdict = CheckVerdict("warn", f"{check.label}: exec failed — {exc}")

        results.append((verdict.severity, check.label, verdict.detail))

        # Apply fix if requested and available
        if fix and verdict.fixable and check.fix_cmd:
            _log_debug(f"container_doctor: fixing {check.label}")
            try:
                fix_proc = _exec_in_container(cname, check.fix_cmd)
                if fix_proc.returncode == 0:
                    results.append(("ok", f"  fix: {check.label}", check.fix_description))
                else:
                    results.append(
                        ("warn", f"  fix: {check.label}", f"fix failed (rc={fix_proc.returncode})")
                    )
            except (subprocess.TimeoutExpired, OSError) as exc:
                results.append(("warn", f"  fix: {check.label}", f"fix failed: {exc}"))

    return results
