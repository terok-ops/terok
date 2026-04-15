# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""In-container health checks via the layered doctor protocol.

Collects checks from ``terok_sandbox.doctor`` (network, shield),
``terok_executor.doctor`` (bridges, credentials, env), and adds terok-level
checks (git identity, remote URL).  Executes probes inside running
containers via ``podman exec`` and optionally applies fixes.

All checks run from the host — the container cannot tamper with the
diagnostic process.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from terok_executor import agent_doctor_checks, get_roster
from terok_sandbox import (
    get_container_state,
    get_proxy_port,
    get_ssh_agent_port,
    make_shield,
    sandbox_exec,
)
from terok_sandbox.doctor import CheckVerdict, DoctorCheck, sandbox_doctor_checks

from ..core.config import make_sandbox_config
from ..core.projects import load_project
from ..util.logging_utils import _log_debug
from .tasks import container_name, load_task_meta, tasks_meta_dir

# Type alias matching sickbay.py convention
_CheckResult = tuple[str, str, str]

_SHIELD_STATE_FILENAME = "shield_desired_state"
_CONTAINER_WORKSPACE = "/workspace"  # nosec B108 — standard workspace mount point
_SHIELD_STATE_LABEL = "Shield state"


# ---------------------------------------------------------------------------
# podman exec helper
# ---------------------------------------------------------------------------


def _exec_in_container(
    cname: str,
    cmd: list[str],
    *,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* inside *cname* via the sandbox exec API."""
    return sandbox_exec(cname, cmd, timeout=timeout)


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
        probe_cmd=["git", "-C", _CONTAINER_WORKSPACE, "config", git_key],
        evaluate=_eval,
        fix_cmd=["git", "-C", _CONTAINER_WORKSPACE, "config", git_key, fix_value],
        fix_description=f"Set git {git_key} to {fix_value!r}.",
    )


_PORT_DRIFT_HINT = (
    " — ports were re-allocated since this container was created;"
    " re-create with 'terok task delete' + 'terok task run'"
)


def _git_remote_check(security_class: str, gate_port: int | None) -> DoctorCheck:
    """Check that git origin remote matches the expected pattern for the security class."""

    def _eval(rc: int, stdout: str, stderr: str) -> CheckVerdict:
        """Compare remote URL against expected pattern."""
        url = stdout.strip()
        if rc != 0 or not url:
            return CheckVerdict("warn", "git origin: no remote configured")

        parsed = urlparse(url)
        try:
            port = parsed.port
        except ValueError:
            port = None
        netloc = parsed.hostname or parsed.netloc
        if port is not None and parsed.hostname:
            netloc = f"{parsed.hostname}:{port}"
        safe_url = urlunparse(parsed._replace(netloc=netloc))

        if security_class == "gatekeeping":
            # Gate URL: http://<token>@host.containers.internal:<port>/<name>
            if parsed.hostname != "host.containers.internal":
                return CheckVerdict(
                    "error",
                    f"git origin: {safe_url!r} bypasses gate — should use host.containers.internal",
                    fixable=False,
                )
            if gate_port is not None and port != gate_port:
                return CheckVerdict(
                    "error",
                    f"git origin: port {port} does not match gate port {gate_port}"
                    + _PORT_DRIFT_HINT,
                    fixable=False,
                )
            return CheckVerdict("ok", "git origin: routed through gate")
        # Online mode: any URL is acceptable
        return CheckVerdict("ok", f"git origin: {safe_url}")

    return DoctorCheck(
        category="git",
        label="Git remote URL",
        probe_cmd=["git", "-C", _CONTAINER_WORKSPACE, "remote", "get-url", "origin"],
        evaluate=_eval,
    )


def _port_drift_check(env_var: str, label: str, expected: int) -> DoctorCheck:
    """Check that a baked-in port env var still matches the resolved port."""

    def _eval(rc: int, stdout: str, stderr: str) -> CheckVerdict:
        baked = stdout.strip()
        if rc != 0 or not baked:
            return CheckVerdict("ok", f"{label}: env not set (pre-registry container)")
        try:
            baked_port = int(baked)
        except ValueError:
            return CheckVerdict("warn", f"{label}: {env_var}={baked!r} is not a port number")
        if baked_port == expected:
            return CheckVerdict("ok", f"{label}: port {expected} matches")
        return CheckVerdict(
            "error",
            f"{label}: container has {baked_port}, host has {expected}{_PORT_DRIFT_HINT}",
            fixable=False,
        )

    return DoctorCheck(
        category="network",
        label=label,
        probe_cmd=["printenv", env_var],
        evaluate=_eval,
    )


def _terok_doctor_checks(
    project_id: str,
    gate_port: int,
    proxy_port: int,
    ssh_agent_port: int,
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

    checks.append(_git_remote_check(project.security_class, gate_port))
    checks.append(_port_drift_check("TEROK_PROXY_PORT", "Proxy port drift", proxy_port))
    checks.append(_port_drift_check("TEROK_SSH_AGENT_PORT", "SSH agent port drift", ssh_agent_port))

    return checks


# ---------------------------------------------------------------------------
# Shield state helper (host-side)
# ---------------------------------------------------------------------------


def _read_desired_shield_state(task_dir: Path) -> str | None:
    """Read the ``shield_desired_state`` file, or ``None`` if absent.

    Raises :class:`OSError` if the file exists but cannot be read so
    callers can distinguish "absent" from "unreadable".
    """
    path = task_dir / _SHIELD_STATE_FILENAME
    return path.read_text(encoding="utf-8").strip() if path.is_file() else None


def _check_shield_state(task_dir: Path, cname: str) -> _CheckResult:
    """Run the host-side shield state check."""
    try:
        desired = _read_desired_shield_state(task_dir)
    except OSError as exc:
        return ("warn", _SHIELD_STATE_LABEL, f"cannot read desired state — {exc}")
    if desired is None:
        return ("ok", _SHIELD_STATE_LABEL, "no desired state — not managed")

    try:
        shield = make_shield(task_dir)
        actual_status = shield.status()
        actual = "up" if actual_status.get("active", False) else "down"
    except Exception as exc:  # noqa: BLE001
        return ("warn", _SHIELD_STATE_LABEL, f"status check failed: {exc}")

    if actual == desired or (desired == "down_all" and actual == "down"):
        return ("ok", _SHIELD_STATE_LABEL, f"matches desired ({desired})")
    return ("warn", _SHIELD_STATE_LABEL, f"mismatch: actual={actual!r}, desired={desired!r}")


# ---------------------------------------------------------------------------
# Orchestrator helpers
# ---------------------------------------------------------------------------


def _collect_all_checks(
    project_id: str,
    task_dir: Path,
) -> list[DoctorCheck]:
    """Gather health checks from sandbox, agent, and terok layers."""
    cfg = make_sandbox_config()
    proxy_port = get_proxy_port(cfg)
    ssh_agent_port = get_ssh_agent_port(cfg)
    desired_shield = _read_desired_shield_state(task_dir)

    checks: list[DoctorCheck] = []
    checks.extend(
        sandbox_doctor_checks(
            proxy_port=proxy_port,
            ssh_agent_port=ssh_agent_port,
            desired_shield_state=desired_shield,
        )
    )
    checks.extend(agent_doctor_checks(get_roster(), proxy_port=proxy_port))
    checks.extend(_terok_doctor_checks(project_id, cfg.gate_port, proxy_port, ssh_agent_port))
    return checks


def _run_probe(cname: str, check: DoctorCheck) -> CheckVerdict:
    """Execute a single probe inside *cname* and evaluate the result."""
    try:
        proc = _exec_in_container(cname, check.probe_cmd)
    except subprocess.TimeoutExpired:
        return CheckVerdict("warn", f"{check.label}: probe timed out")
    except OSError as exc:
        return CheckVerdict("warn", f"{check.label}: exec failed — {exc}")

    try:
        return check.evaluate(proc.returncode, proc.stdout, proc.stderr)
    except Exception as exc:  # noqa: BLE001
        return CheckVerdict("warn", f"{check.label}: evaluate failed — {exc}")


def _apply_fix(cname: str, check: DoctorCheck) -> _CheckResult:
    """Attempt to apply a fix command and return the result tuple."""
    _log_debug(f"container_doctor: fixing {check.label}")
    try:
        fix_proc = _exec_in_container(cname, check.fix_cmd)  # type: ignore[arg-type]
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ("warn", f"  fix: {check.label}", f"fix failed: {exc}")
    if fix_proc.returncode == 0:
        return ("ok", f"  fix: {check.label}", check.fix_description)
    return ("warn", f"  fix: {check.label}", f"fix failed (rc={fix_proc.returncode})")


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _resolve_running_container(
    project_id: str, task_id: str
) -> tuple[str, Path, list[_CheckResult]]:
    """Resolve a task to its running container name and task directory.

    Returns ``(cname, task_dir, [])`` on success.  If the task cannot be
    checked, *cname* is empty and the list holds the skip/warning result.
    """
    label = f"Task {project_id}/{task_id}"
    meta_dir = tasks_meta_dir(project_id)
    if not (meta_dir / f"{task_id}.yml").is_file():
        return ("", Path(), [("warn", label, "metadata not found")])

    meta, _ = load_task_meta(project_id, task_id)
    mode = meta.get("mode")
    if not mode:
        return ("", Path(), [("warn", label, "never started (no mode)")])

    cname = container_name(project_id, mode, task_id)
    state = get_container_state(cname)
    if state != "running":
        return ("", Path(), [("info", label, f"not running ({state}) — skipped")])

    task_dir = load_project(project_id).tasks_root / str(task_id)
    return (cname, task_dir, [])


def _dispatch_host_side(check: DoctorCheck, task_dir: Path, cname: str) -> _CheckResult:
    """Handle a host-side check that cannot use podman exec."""
    if check.category == "shield":
        return _check_shield_state(task_dir, cname)
    return ("warn", check.label, "unknown host-side check — skipped")


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
    cname, task_dir, early = _resolve_running_container(project_id, task_id)
    if early:
        return early

    results: list[_CheckResult] = []
    for check in _collect_all_checks(project_id, task_dir):
        if check.host_side:
            results.append(_dispatch_host_side(check, task_dir, cname))
            continue

        verdict = _run_probe(cname, check)
        results.append((verdict.severity, check.label, verdict.detail))

        if fix and verdict.fixable and check.fix_cmd:
            results.append(_apply_fix(cname, check))

    return results
