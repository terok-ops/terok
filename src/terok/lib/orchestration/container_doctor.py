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
    ExecResult,
    get_ssh_signer_port,
    get_token_broker_port,
    make_shield,
)
from terok_sandbox.doctor import CheckVerdict, DoctorCheck, sandbox_doctor_checks

from ..core import runtime as _rt
from ..core.config import make_sandbox_config
from ..core.projects import load_project
from ..util.check_reporter import CheckReporter
from ..util.logging_utils import _log_debug
from .tasks import _has_task_meta, container_name, load_task_meta, tasks_meta_dir

# Type alias matching sickbay.py convention
_CheckResult = tuple[str, str, str]

_SHIELD_STATE_FILENAME = "shield_desired_state"
_CONTAINER_WORKSPACE = "/workspace"  # nosec B108 — standard workspace mount point
_SHIELD_STATE_LABEL = "Shield state"

#: Map from per-check ``(category, label-prefix)`` → human-readable group
#: heading.  Checks that match a row here are coalesced under one
#: heading line; everything else streams individually.  The label-prefix
#: match is "label starts with this string followed by a space and an
#: opening paren" so ``Credential file (claude)`` maps to ``Credential
#: file`` but not a hypothetical ``Credential file cache``.  ``None`` as
#: the prefix matches any label within that category.
_GROUP_HEADINGS: tuple[tuple[str, str | None, str], ...] = (
    ("mount", "Credential file", "Credential files"),
    ("env", "Phantom token", "Phantom tokens"),
    ("env", "Base URL", "Base URLs"),
    ("bridge", None, "Bridges"),
    ("git", None, "Git config"),
    ("network", None, "Port drift"),
)


# ---------------------------------------------------------------------------
# podman exec helper
# ---------------------------------------------------------------------------


def _exec_in_container(
    cname: str,
    cmd: list[str],
    *,
    timeout: int = 10,
) -> ExecResult:
    """Run *cmd* inside *cname* via the container runtime."""
    runtime = _rt.get_runtime()
    return runtime.exec(runtime.container(cname), cmd, timeout=timeout)


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
    token_broker_port: int,
    ssh_signer_port: int,
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
    checks.append(
        _port_drift_check("TEROK_TOKEN_BROKER_PORT", "Token broker port drift", token_broker_port)
    )
    checks.append(
        _port_drift_check("TEROK_SSH_SIGNER_PORT", "SSH signer port drift", ssh_signer_port)
    )

    return checks


# ---------------------------------------------------------------------------
# Shield state helper (host-side)
# ---------------------------------------------------------------------------


def _read_desired_shield_state(task_dir: Path) -> str | None:
    """Read the ``shield_desired_state`` file, or ``None`` if absent.

    Raises [`OSError`][OSError] if the file exists but cannot be read so
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
    token_broker_port = get_token_broker_port(cfg)
    ssh_signer_port = get_ssh_signer_port(cfg)
    desired_shield = _read_desired_shield_state(task_dir)

    checks: list[DoctorCheck] = []
    checks.extend(
        sandbox_doctor_checks(
            token_broker_port=token_broker_port,
            ssh_signer_port=ssh_signer_port,
            desired_shield_state=desired_shield,
        )
    )
    checks.extend(agent_doctor_checks(get_roster(), token_broker_port=token_broker_port))
    checks.extend(
        _terok_doctor_checks(project_id, cfg.gate_port, token_broker_port, ssh_signer_port)
    )
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
        return check.evaluate(proc.exit_code, proc.stdout, proc.stderr)
    except Exception as exc:  # noqa: BLE001
        return CheckVerdict("warn", f"{check.label}: evaluate failed — {exc}")


def _apply_fix(cname: str, check: DoctorCheck) -> _CheckResult:
    """Attempt to apply a fix command and return the result tuple."""
    _log_debug(f"container_doctor: fixing {check.label}")
    try:
        fix_proc = _exec_in_container(cname, check.fix_cmd)  # type: ignore[arg-type]
    except (subprocess.TimeoutExpired, OSError) as exc:
        return ("warn", f"  fix: {check.label}", f"fix failed: {exc}")
    if fix_proc.ok:
        return ("ok", f"  fix: {check.label}", check.fix_description)
    return ("warn", f"  fix: {check.label}", f"fix failed (rc={fix_proc.exit_code})")


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
    if not _has_task_meta(meta_dir, task_id):
        return ("", Path(), [("warn", label, "metadata not found")])

    meta, _ = load_task_meta(project_id, task_id)
    mode = meta.get("mode")
    if not mode:
        return ("", Path(), [("warn", label, "never started (no mode)")])

    cname = container_name(project_id, mode, task_id)
    state = _rt.get_runtime().container(cname).state
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
    reporter: CheckReporter | None = None,
    label_prefix: str = "",
) -> list[_CheckResult]:
    """Run all layered in-container health checks for a specific task.

    Collects checks from sandbox, agent, and terok layers, executes probes
    via ``podman exec``, evaluates results, and optionally applies fixes.

    When *reporter* is supplied, progress streams line-by-line through it
    and noisy categories (``Credential file (...)``, ``Phantom token
    (...)``, …) coalesce into a single group heading.  The returned list
    is also populated for backwards-compatible callers that want the
    aggregate; it is empty when *reporter* handled the streaming so the
    caller doesn't re-print.

    *label_prefix* is prepended to every emitted label — used by the
    sickbay command to tag multi-task runs with ``"Task pid/tid: "``.
    """
    cname, task_dir, early = _resolve_running_container(project_id, task_id)
    if early:
        if reporter is not None:
            for status, label, detail in early:
                reporter.emit(status, label, detail)
            return []
        return early

    checks = list(_collect_all_checks(project_id, task_dir))

    if reporter is None:
        # Legacy path: collect-and-return.  No streaming, no grouping.
        results: list[_CheckResult] = []
        for check in checks:
            results.extend(_execute_check(check, cname, task_dir, fix=fix))
        return results

    # Streaming path with grouping.
    _stream_checks(checks, cname, task_dir, fix=fix, reporter=reporter, label_prefix=label_prefix)
    return []


def _execute_check(
    check: DoctorCheck,
    cname: str,
    task_dir: Path,
    *,
    fix: bool,
) -> list[_CheckResult]:
    """Run one check and return ``[result]`` or ``[result, fix_result]``."""
    if check.host_side:
        return [_dispatch_host_side(check, task_dir, cname)]

    verdict = _run_probe(cname, check)
    out: list[_CheckResult] = [(verdict.severity, check.label, verdict.detail)]
    if fix and verdict.fixable and check.fix_cmd:
        out.append(_apply_fix(cname, check))
    return out


def _group_key(check: DoctorCheck) -> tuple[str | None, str]:
    """Return ``(heading, member_label)`` — ``heading`` is ``None`` for ungrouped checks."""
    for category, prefix, heading in _GROUP_HEADINGS:
        if check.category != category:
            continue
        if prefix is None:
            return (heading, check.label)
        # Match on "prefix (" so "Credential file (claude)" matches but
        # something like "Credential file cache" would not.
        if check.label.startswith(f"{prefix} ("):
            return (heading, check.label)
    return (None, check.label)


def _stream_checks(
    checks: list[DoctorCheck],
    cname: str,
    task_dir: Path,
    *,
    fix: bool,
    reporter: CheckReporter,
    label_prefix: str,
) -> None:
    """Emit check progress through *reporter* with per-heading grouping.

    Groups appear at the position of their *first* member and absorb any
    later checks that share the same heading — so a category like
    ``network`` that's contributed to by both the sandbox (TCP
    reachability) and the terok layer (port-drift checks) produces a
    single "Port drift" line instead of two separate ones.  Individual
    (ungrouped) checks stream at the position where they appear.
    """
    # Build an ordered action list: each entry is either an individual
    # check or a "slot" that a group will be accumulated into.  Group
    # slots are keyed by heading so later checks with the same heading
    # fall into the same list.
    slots: dict[str, list[DoctorCheck]] = {}
    plan: list[tuple[str, DoctorCheck | str]] = []  # ("one", check) | ("group", heading)
    for check in checks:
        heading, _ = _group_key(check)
        if heading is None:
            plan.append(("one", check))
            continue
        if heading not in slots:
            slots[heading] = []
            plan.append(("group", heading))
        slots[heading].append(check)

    for kind, payload in plan:
        if kind == "one":
            _emit_individual(
                payload,  # type: ignore[arg-type]
                cname,
                task_dir,
                fix=fix,
                reporter=reporter,
                label_prefix=label_prefix,
            )
        else:
            heading = payload  # type: ignore[assignment]
            _emit_group(
                slots[heading],
                heading,
                cname,
                task_dir,
                fix=fix,
                reporter=reporter,
                label_prefix=label_prefix,
            )


def _emit_individual(
    check: DoctorCheck,
    cname: str,
    task_dir: Path,
    *,
    fix: bool,
    reporter: CheckReporter,
    label_prefix: str,
) -> None:
    """Stream one check through the reporter (begin → run → end)."""
    label = f"{label_prefix}{check.label}"
    reporter.begin(label)
    results = _execute_check(check, cname, task_dir, fix=fix)
    # First tuple is the check itself; any extra is a fix follow-up.
    status, _, detail = results[0]
    reporter.end(status, detail)
    for fix_status, fix_label, fix_detail in results[1:]:
        reporter.emit(fix_status, f"{label_prefix}{fix_label}", fix_detail)


def _emit_group(
    members: list[DoctorCheck],
    heading: str,
    cname: str,
    task_dir: Path,
    *,
    fix: bool,
    reporter: CheckReporter,
    label_prefix: str,
) -> None:
    """Run *members* silently under one heading line, then summarise."""
    full_heading = f"{label_prefix}{heading}"
    fix_followups: list[_CheckResult] = []
    with reporter.group(full_heading) as g:
        for check in members:
            results = _execute_check(check, cname, task_dir, fix=fix)
            status, _, detail = results[0]
            g.track(status, check.label, detail)
            # Fix follow-ups don't belong inside the group summary —
            # they're separate informational lines.  Buffer and emit
            # after the group closes.
            fix_followups.extend(results[1:])
    for fix_status, fix_label, fix_detail in fix_followups:
        reporter.emit(fix_status, f"{label_prefix}{fix_label}", fix_detail)
