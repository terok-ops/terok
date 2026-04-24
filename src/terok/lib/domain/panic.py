# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Emergency panic — immediately cut all resource access across all projects.

Two-phase sequence: Phase 1 raises shields, stops the vault and gate
server — all in parallel, all reversible.  Phase 2 optionally stops
the containers themselves (slow on some platforms, so user-prompted).

Token revocation is deliberately excluded — it is irreversible and
shields + stopped services already cut access.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..core.config import get_shield_bypass_firewall_no_protection
from ..core.paths import core_state_dir
from ..core.projects import list_projects
from ..orchestration.tasks import (
    CONTAINER_MODES,
    container_name,
    get_all_task_states,
    get_tasks,
)

logger = logging.getLogger(__name__)

_LOCK_FILENAME = "panic.lock"

# (project_id, task_id, mode, cname, task_dir)
type _Target = tuple[str, str, str, str, object]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class PanicResult:
    """Outcome of an :func:`execute_panic` invocation."""

    shields_raised: list[str] = field(default_factory=list)
    shield_errors: list[tuple[str, str]] = field(default_factory=list)
    vault_stopped: bool = False
    vault_error: str | None = None
    gate_stopped: bool = False
    gate_error: str | None = None
    containers_stopped: list[str] = field(default_factory=list)
    container_stop_errors: list[tuple[str, str]] = field(default_factory=list)
    shield_bypassed: bool = False
    total_running: int = 0

    @property
    def has_errors(self) -> bool:
        """Return whether any operation failed."""
        return bool(
            self.shield_errors or self.vault_error or self.gate_error or self.container_stop_errors
        )


def execute_panic(
    *,
    stop_containers: bool = False,
) -> PanicResult:
    """Execute the full panic sequence.

    Discovers every running container, then raises shields, stops vault
    and gate — all in parallel.  If *stop_containers*, also stops the
    containers afterwards.
    """
    result = PanicResult()
    targets = _discover_targets()
    result.total_running = len(targets)
    result.shield_bypassed = get_shield_bypass_firewall_no_protection()

    _phase1_lockdown(result, targets)
    _write_panic_lock()

    # Phase 2: optional container stop
    if stop_containers and targets:
        result.containers_stopped, result.container_stop_errors = _stop_containers(targets)

    return result


def panic_stop_containers() -> tuple[list[str], list[tuple[str, str]]]:
    """Discover and stop all running containers (Phase 2 standalone)."""
    return _stop_containers(_discover_targets())


def is_panicked() -> bool:
    """Return whether the panic lock file exists."""
    return (core_state_dir() / _LOCK_FILENAME).is_file()


def clear_panic_lock() -> None:
    """Remove the panic lock file if it exists."""
    (core_state_dir() / _LOCK_FILENAME).unlink(missing_ok=True)


def format_panic_report(result: PanicResult) -> str:
    """Format a human-readable summary of the panic result."""
    lines = [
        f"Containers found: {result.total_running}",
        _format_shield_status(result),
        f"Vault: {'stopped' if result.vault_stopped else 'FAILED'}",
        f"Gate:  {'stopped' if result.gate_stopped else 'FAILED'}",
    ]

    if result.containers_stopped:
        lines.append(f"Containers stopped: {len(result.containers_stopped)}")

    if result.has_errors:
        lines += ["", "Errors:", *_format_errors(result)]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _phase1_lockdown(result: PanicResult, targets: list[_Target]) -> None:
    """Run Phase 1: shields + vault/gate stop in parallel."""
    with ThreadPoolExecutor(max_workers=max(len(targets) + 2, 4)) as pool:
        futs: dict = {}

        if not result.shield_bypassed:
            for t in targets:
                futs[pool.submit(_raise_shield, t)] = ("shield", t[3])

        futs[pool.submit(_stop_vault)] = ("vault", "")
        futs[pool.submit(_stop_gate)] = ("gate", "")

        for fut in as_completed(futs):
            kind, label = futs[fut]
            _collect_phase1_result(result, kind, label, fut)


def _collect_phase1_result(result: PanicResult, kind: str, label: str, fut) -> None:
    """Collect a single Phase 1 future result into the PanicResult."""
    try:
        res = fut.result(timeout=60)
    except Exception as exc:
        res = (label or False, str(exc))

    if kind == "shield":
        cname, err = res
        (result.shield_errors if err else result.shields_raised).append(
            (cname, err) if err else cname
        )
    elif kind == "vault":
        result.vault_stopped, result.vault_error = res
    else:
        result.gate_stopped, result.gate_error = res


def _format_shield_status(result: PanicResult) -> str:
    """Format the shield status line for the panic report."""
    if result.shield_bypassed:
        return "Shields: BYPASSED (firewall protection disabled)"
    s = f"Shields raised: {len(result.shields_raised)}"
    if result.shield_errors:
        s += f" ({len(result.shield_errors)} failed)"
    return s


def _format_errors(result: PanicResult) -> list[str]:
    """Collect all error lines for the panic report."""
    lines = [f"  shield {cname}: {err}" for cname, err in result.shield_errors]
    if result.vault_error:
        lines.append(f"  vault: {result.vault_error}")
    if result.gate_error:
        lines.append(f"  gate: {result.gate_error}")
    lines += [f"  stop {cname}: {err}" for cname, err in result.container_stop_errors]
    return lines


def _discover_targets() -> list[_Target]:
    """Find every running or paused container across all projects."""
    targets: list[_Target] = []
    for cfg in list_projects():
        try:
            tasks = get_tasks(cfg.id)
            if not tasks:
                continue
            states = get_all_task_states(cfg.id, tasks)
        except Exception:
            logger.debug("panic: failed to list tasks for %s", cfg.id, exc_info=True)
            continue
        targets.extend(
            (
                cfg.id,
                t.task_id,
                t.mode,
                container_name(cfg.id, t.mode, t.task_id),
                cfg.tasks_root / str(t.task_id),
            )
            for t in tasks
            if t.mode and states.get(t.task_id) in ("running", "paused")
        )
    return targets


def _raise_shield(target: _Target) -> tuple[str, str | None]:
    """Block all traffic for one container (total blackout)."""
    from terok_sandbox import block as shield_block

    _, _, _, cname, task_dir = target
    try:
        shield_block(cname, task_dir)
        return cname, None
    except Exception as exc:
        return cname, str(exc)


def _stop_vault() -> tuple[bool, str | None]:
    """Stop the vault daemon."""
    from terok_sandbox import stop_vault

    try:
        stop_vault()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _stop_gate() -> tuple[bool, str | None]:
    """Stop the gate server daemon."""
    from terok_sandbox import stop_daemon

    try:
        stop_daemon()
        return True, None
    except Exception as exc:
        return False, str(exc)


def _stop_containers(targets: list[_Target]) -> tuple[list[str], list[tuple[str, str]]]:
    """Stop all container modes for each target."""
    from ..core import runtime as _rt

    runtime = _rt.get_runtime()
    names = [container_name(pid, m, tid) for pid, tid, _, _, _ in targets for m in CONTAINER_MODES]
    if not names:
        return [], []
    try:
        runtime.force_remove([runtime.container(n) for n in names])
        return names, []
    except Exception as exc:
        return [], [(n, str(exc)) for n in names]


def _write_panic_lock() -> None:
    """Write the panic lock file with current timestamp."""
    path = core_state_dir() / _LOCK_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now(UTC).isoformat() + "\n")
