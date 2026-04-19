# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Health check and reconciliation command (DS9-themed diagnostic bay).

Runs a series of checks and reports their status.  With ``--fix``,
auto-remediates issues like unfired post_stop hooks.

Scoping:
- ``terok sickbay`` — all projects
- ``terok sickbay <project>`` — single project
- ``terok sickbay <project> <task>`` — single task

Exit codes:
- 0: all checks passed
- 1: warnings present
- 2: errors present
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from pathlib import Path

from terok_sandbox import (
    check_environment,
    check_units_outdated,
    get_server_status,
    get_vault_status,
    is_systemd_available,
    is_vault_socket_active,
    is_vault_systemd_available,
)

from ...lib.core import runtime as _rt
from ...lib.core.config import get_services_mode, global_config_path, make_sandbox_config
from ...lib.core.project_model import ProjectConfig
from ...lib.core.projects import list_projects, load_project
from ...lib.core.yaml_schema import SERVICES_TCP_OPTOUT_YAML
from ...lib.orchestration.container_doctor import run_container_doctor
from ...lib.orchestration.hooks import run_hook
from ...lib.orchestration.tasks import container_name, tasks_meta_dir
from ...lib.util.yaml import load as _yaml_load

# Type alias for check results: (severity, label, detail)
_CheckResult = tuple[str, str, str]


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``sickbay`` subcommand."""
    p = subparsers.add_parser("sickbay", help="Run health checks and reconciliation")
    # dest=project_id / task_id matches the rest of the CLI so the shared
    # completers work; metavar keeps the display ``<project>``/``<task>``.
    from ._completers import add_project_id, add_task_id

    add_project_id(p, nargs="?", metavar="project", help="Scope to a single project")
    add_task_id(p, nargs="?", metavar="task", help="Scope to a single task")
    p.add_argument("--fix", action="store_true", help="Auto-remediate issues")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the sickbay command.  Returns True if handled."""
    if args.cmd != "sickbay":
        return False
    _cmd_sickbay(
        project_id=getattr(args, "project_id", None),
        task_id=getattr(args, "task_id", None),
        fix=getattr(args, "fix", False),
    )
    return True


def _check_gate_server() -> _CheckResult:
    """Check gate server status."""
    cfg = make_sandbox_config()
    status = get_server_status(cfg)
    configured = get_services_mode()
    label = "Gate server"
    if status.running:
        outdated = check_units_outdated(cfg)
        if outdated:
            return ("warn", label, f"{outdated} Run 'terok gate start' to update.")
        detail = f"{status.mode}, {status.transport or 'tcp'}"
        if configured != (status.transport or "tcp"):
            return (
                "warn",
                label,
                f"{detail} — config says services.mode: {configured}",
            )
        return ("ok", label, detail)
    if status.mode == "systemd":
        return ("error", label, "socket installed but not active")
    if is_systemd_available():
        return ("warn", label, "not running — run 'terok gate start'")
    return ("warn", label, "not running — run 'terok gate start'")


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
        return ("warn", label, "hooks outdated — run 'terok shield install-hooks --user'")
    if ec.health == "setup-needed":
        hint = (
            ec.setup_hint.splitlines()[0]
            if ec.setup_hint
            else "run 'terok shield install-hooks --user'"
        )
        return ("warn", label, f"{ec.issues[0] if ec.issues else 'setup needed'} — {hint}")
    if ec.health != "ok":
        return ("warn", label, f"unexpected health: {ec.health}")
    dns = getattr(ec, "dns_tier", "unknown")
    return ("ok", label, f"active ({ec.hooks}, {dns} DNS)")


def _check_vault() -> _CheckResult:
    """Check vault status."""
    label = "Vault"
    try:
        status = get_vault_status()
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"check failed — {exc}")
    if status.running:
        configured = get_services_mode()
        creds = len(status.credentials_stored) if status.credentials_stored else 0
        detail = f"{status.mode}, {status.transport or 'tcp'}, {creds} credential(s) stored"
        if configured != (status.transport or "tcp"):
            return (
                "warn",
                label,
                f"{detail} — config says services.mode: {configured}",
            )
        return ("ok", label, detail)
    if status.mode == "systemd":
        if is_vault_socket_active():
            return ("ok", label, "systemd, socket active — service starts on first connection")
        return (
            "error",
            label,
            "socket installed but not active — run 'terok vault start'",
        )
    if is_vault_systemd_available():
        return ("warn", label, "not running — run 'terok vault install'")
    return ("warn", label, "not running — run 'terok vault start'")


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
    if _rt.get_runtime().container(cname).state == "running":
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


def _sanitize_id(value: str) -> str:
    """Strip C0/C1 control characters from a project ID for safe terminal output."""
    import unicodedata

    return "".join(
        " " if ch in "\n\r\t" else f"\\x{ord(ch):02x}" if unicodedata.category(ch)[0] == "C" else ch
        for ch in value
    )


def _abbreviate(ids: list[str], limit: int = 3) -> str:
    """Join project IDs with a '+N more' suffix when the list is long."""
    suffix = f" (+{len(ids) - limit} more)" if len(ids) > limit else ""
    return ", ".join(_sanitize_id(i) for i in ids[:limit]) + suffix


def _check_ssh_signer() -> _CheckResult:
    """Check SSH signer key registration against known projects."""
    from ...lib.domain.facade import vault_db

    label = "SSH signer"
    projects = list_projects()
    if not projects:
        return ("ok", label, "no projects configured")

    try:
        with vault_db() as db:
            assigned_scopes = set(db.list_scopes_with_ssh_keys())
    except Exception as exc:  # noqa: BLE001 — surface any vault failure as a warning
        return ("warn", label, f"vault unreachable — {exc}")

    unregistered = [p.id for p in projects if p.id not in assigned_scopes]
    registered = len(projects) - len(unregistered)
    total = len(projects)

    if unregistered:
        return (
            "warn",
            label,
            f"{registered}/{total} project(s) have SSH keys — missing: "
            f"{_abbreviate(unregistered)}. Run 'terok project ssh-init <project>'",
        )
    return ("ok", label, f"{total}/{total} project(s) have SSH keys")


_KEYRING_DOC_URL = "https://terok-ai.github.io/terok/kernel-keyring/"

# Podman containers.conf lookup order (rootless).  The first existing file wins.
_CONTAINERS_CONF_PATHS = (
    Path.home() / ".config" / "containers" / "containers.conf",
    Path("/etc/containers/containers.conf"),
)


def _find_containers_conf() -> Path | None:
    """Return the effective containers.conf path, respecting ``$CONTAINERS_CONF``."""
    env = os.environ.get("CONTAINERS_CONF")
    if env:
        p = Path(env)
        if p.is_file():
            return p
        # Invalid env var — fall through to standard paths
    return next((p for p in _CONTAINERS_CONF_PATHS if p.is_file()), None)


def _check_keyring() -> _CheckResult:
    """Check that the kernel keyring is disabled in containers.conf."""
    label = "Keyring"
    conf = _find_containers_conf()
    if conf is None:
        return (
            "warn",
            label,
            f"no containers.conf found — add keyring = false, see {_KEYRING_DOC_URL}",
        )
    try:
        data = tomllib.loads(conf.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return ("warn", label, f"cannot parse {conf} — {exc}")
    containers_section = data.get("containers")
    keyring = (
        containers_section.get("keyring", True) if isinstance(containers_section, dict) else True
    )
    if keyring is False:
        return ("ok", label, f"disabled in {conf}")
    return (
        "warn",
        label,
        f"not disabled — add [containers] keyring = false to {conf}, see {_KEYRING_DOC_URL}",
    )


def _check_containers(
    project_id: str | None,
    task_id: str | None,
    *,
    fix: bool,
) -> list[_CheckResult]:
    """Run in-container health checks for running tasks.

    The per-task running-state check is handled inside
    ``run_container_doctor`` — it returns an informational result for
    non-running containers, so we simply forward all tasks and let the
    orchestrator decide.
    """
    results: list[_CheckResult] = []

    if project_id and task_id:
        # Single task scope
        results.extend(run_container_doctor(project_id, task_id, fix=fix))
        return results

    # Project or global scope — iterate all known tasks
    if project_id:
        projects = [(project_id, load_project(project_id))]
    else:
        projects = [(p.id, p) for p in list_projects()]

    for pid, _project in projects:
        meta_dir = tasks_meta_dir(pid)
        if not meta_dir.is_dir():
            continue
        for meta_file in meta_dir.glob("*.yml"):
            tid = meta_file.stem
            for severity, label, detail in run_container_doctor(pid, tid, fix=fix):
                # Prefix bare check labels with task context so multi-task
                # output is unambiguous (early-return labels already include it)
                if not label.startswith(("Task ", "  fix:")):
                    label = f"Task {pid}/{tid}: {label}"
                results.append((severity, label, detail))

    return results


def _check_selinux_policy() -> _CheckResult:
    """Render ``check_selinux_status`` as a sickbay check result tuple.

    The decision tree (tcp vs socket, enforcing vs permissive, policy
    installed, libselinux loadable) lives in
    :func:`terok_sandbox.check_selinux_status` so sickbay and
    ``terok setup`` share one source of truth; this function only
    translates the structured result into sickbay's output shape.
    """
    from terok_sandbox import (
        SelinuxStatus,
        check_selinux_status,
        selinux_install_command,
        selinux_install_script,
    )

    label = "SELinux policy"
    result = check_selinux_status(services_mode=get_services_mode())

    match result.status:
        case SelinuxStatus.NOT_APPLICABLE_TCP_MODE:
            return ("ok", label, "not needed (services.mode: tcp)")
        case SelinuxStatus.NOT_APPLICABLE_PERMISSIVE:
            return ("ok", label, "not needed (SELinux not enforcing)")
        case SelinuxStatus.POLICY_MISSING:
            install_cmd = selinux_install_command()
            opt_out = f"or opt out: {SERVICES_TCP_OPTOUT_YAML} in {global_config_path()}"
            if result.missing_policy_tools:
                tools = ", ".join(result.missing_policy_tools)
                return (
                    "warn",
                    label,
                    f"terok_socket_t NOT installed; policy tools missing ({tools}). "
                    "Fix (pick one): sudo dnf install selinux-policy-devel policycoreutils, "
                    f"then {install_cmd}; {opt_out}",
                )
            return (
                "warn",
                label,
                "terok_socket_t NOT installed — containers cannot connect to sockets. "
                f"Fix (pick one): {install_cmd}; {opt_out}",
            )
        case SelinuxStatus.LIBSELINUX_MISSING:
            return (
                "warn",
                label,
                "libselinux.so.1 not loadable — sockets will bind as unconfined_t "
                "and containers will be denied even with the policy installed. "
                "Fix: sudo dnf install libselinux",
            )
        case SelinuxStatus.OK:
            return (
                "ok",
                label,
                "terok_socket_t installed, binding functional "
                f"(installer: {selinux_install_script()})",
            )


def _check_vault_migration() -> _CheckResult:
    """Check for leftover pre-vault credentials directory."""
    label = "Vault migration"
    try:
        from terok_sandbox.paths import namespace_state_dir

        old_dir = namespace_state_dir("credentials")
        new_dir = namespace_state_dir("vault")
        if old_dir.is_dir() and not new_dir.is_dir():
            return (
                "warn",
                label,
                f"legacy credentials/ dir exists at {old_dir} — "
                "run 'python3 tools/terok-migrate-vault.py' to migrate to vault/",
            )
        if old_dir.is_dir() and new_dir.is_dir():
            return (
                "info",
                label,
                f"legacy credentials/ dir still present at {old_dir} — "
                "safe to remove after verifying vault/ works",
            )
    except Exception as exc:  # noqa: BLE001
        return ("warn", label, f"check failed — {exc}")
    return ("ok", label, "no legacy directory")


def _check_dbus_hub_state_dir() -> _CheckResult:
    """Check that the installed D-Bus hub unit agrees with the shell's state-dir env.

    Three forms of drift are surfaced:

    - **env set but unit has none**: the hub will fall back to the XDG default,
      which differs from what the shell's ``terok-shield`` resolves.
    - **unit baked but env unset**: the hub points at a custom state root the
      interactive shell no longer sees — probably stale.
    - **both set, different values**: explicit mismatch, verdicts land in the
      wrong state dir.

    No mismatch (both unset, or both set to the same value) reports ``ok``.
    The check is skipped cleanly when the unit isn't installed at all.
    """
    from terok_dbus._install import STATE_DIR_ENV, extract_baked_state_dir, read_installed_unit

    label = "D-Bus hub state_dir"
    unit = read_installed_unit()
    if unit is None:
        return ("ok", label, "hub not installed — nothing to audit")
    baked = extract_baked_state_dir(unit)
    env = os.environ.get(STATE_DIR_ENV)
    if baked == env:
        if baked is None:
            return ("ok", label, "using XDG default on both sides")
        return ("ok", label, f"env and unit agree on {baked}")
    if env and not baked:
        return (
            "warn",
            label,
            f"{STATE_DIR_ENV}={env} in shell but absent from unit — "
            "re-run `terok setup` to bake it in",
        )
    if baked and not env:
        return (
            "warn",
            label,
            f"unit baked with {STATE_DIR_ENV}={baked} but shell env unset — "
            "export it in your shell or re-run `terok setup` without it",
        )
    return (
        "warn",
        label,
        f"{STATE_DIR_ENV} mismatch: shell={env!r}, unit={baked!r} — re-run `terok setup` to sync",
    )


_GLOBAL_CHECKS = [
    _check_gate_server,
    _check_shield,
    _check_vault,
    _check_vault_migration,
    _check_ssh_signer,
    _check_keyring,
    _check_selinux_policy,
    _check_dbus_hub_state_dir,
]

_STATUS_MARKERS = {
    "ok": "ok",
    "info": "info",
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

    # In-container diagnostics for running tasks
    container_results = _check_containers(project_id, task_id, fix=fix)
    for status, label, detail in container_results:
        print(f"  {label} .... {_STATUS_MARKERS.get(status, status)} ({detail})")
        worst = _update_worst(worst, status)

    # Print "ok (consistent)" only when scoped to a single task and every result is "ok"
    all_ok = all(s == "ok" for s, _, _ in hook_results + container_results)
    if task_id and all_ok:
        print(f"  Task {project_id}/{task_id} .... ok (consistent)")

    if worst == "error":
        sys.exit(2)
    elif worst == "warn":
        sys.exit(1)
