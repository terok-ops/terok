# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Mirror of ``terok setup``: tears down everything the bootstrap installs.

Reverse install order: desktop entry first (most user-visible), then
the sandbox aggregator's symmetric uninstall (clearance hub/verdict/
notifier → gate → vault → shield hooks).  The NFLOG reader script is
a shield artefact that the sandbox aggregator's shield phase doesn't
clean up today, so we tear it down alongside the aggregator call.

The vault credential DB is left on disk so a re-install picks up the
operator's tokens and SSH keys without a fresh auth cycle;
``--purge-credentials`` deletes it.
"""

from __future__ import annotations

import argparse

from ._setup_ui import _bold, _stage_begin, _status_label, _yellow

# ── CLI wiring ─────────────────────────────────────────────────────────


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``uninstall`` top-level command."""
    p = subparsers.add_parser(
        "uninstall",
        help="Remove everything `terok setup` installed",
        description=(
            "Symmetric teardown of `terok setup` — removes desktop entry "
            "plus the full sandbox stack (clearance, gate, vault, shield "
            "hooks, NFLOG reader) via the sandbox aggregator.  The vault "
            "credential DB is preserved unless --purge-credentials is passed."
        ),
    )
    p.add_argument(
        "--root",
        action="store_true",
        help="Also remove shield hooks from the system hooks directory (requires sudo)",
    )
    p.add_argument(
        "--no-desktop-entry",
        action="store_true",
        help="Skip the XDG desktop entry removal",
    )
    p.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Skip the sandbox stack teardown (shield + vault + gate + clearance)",
    )
    p.add_argument(
        "--purge-credentials",
        action="store_true",
        help="Also delete the vault credential DB — agents will need re-auth",
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``uninstall``.  Returns True if handled."""
    if args.cmd != "uninstall":
        return False
    cmd_uninstall(
        root=getattr(args, "root", False),
        no_desktop_entry=getattr(args, "no_desktop_entry", False),
        no_sandbox=getattr(args, "no_sandbox", False),
        purge_credentials=getattr(args, "purge_credentials", False),
    )
    return True


# ── Orchestrator ───────────────────────────────────────────────────────


def cmd_uninstall(
    *,
    root: bool = False,
    no_desktop_entry: bool = False,
    no_sandbox: bool = False,
    purge_credentials: bool = False,
) -> None:
    """Tear down every phase ``terok setup`` installs.

    Desktop entry first (user-visible surface), sandbox aggregator
    next (does clearance → gate → vault → shield in reverse install
    order), credential DB last (only when ``--purge-credentials``).
    A running container survives a gate/vault teardown more gracefully
    than it survives losing its shield hooks, so the aggregator's
    order keeps shield-hooks last.
    """
    print(_bold("\nUninstalling terok host services\n"))

    all_ok = True
    if not no_desktop_entry:
        all_ok &= _uninstall_desktop_entry()
    if not no_sandbox:
        all_ok &= _uninstall_sandbox_stack(root=root)
    if purge_credentials:
        all_ok &= _purge_credential_db()

    print()
    if all_ok:
        print(_bold("Uninstall complete."))
    else:
        print(_bold(_yellow("Some uninstall phases reported errors (see above).")))
        raise SystemExit(1)


# ── Phase helpers (reverse install order) ──────────────────────────────


def _uninstall_desktop_entry() -> bool:
    """Remove the XDG desktop entry + application icon."""
    from ._desktop_entry import uninstall_desktop_entry

    _stage_begin("Desktop entry")
    try:
        uninstall_desktop_entry()
    except Exception as exc:  # noqa: BLE001
        print(f"{_status_label(False)} ({exc})")
        return False
    print(f"{_status_label(True)} (removed)")
    return True


def _uninstall_sandbox_stack(*, root: bool) -> bool:
    """Remove the NFLOG reader script, then delegate the rest to the aggregator.

    The aggregator's shield-hooks teardown doesn't touch the reader
    script today — a shield-side bug that's filed as deferred work.
    Handle it here until shield's ``run_uninstall`` subsumes it; the
    reader is harmless without the hooks that feed it, but leaving
    orphans on disk is the wrong default.
    """
    from terok_sandbox import sandbox_uninstall, uninstall_shield_bridge

    _stage_begin("Sandbox stack")
    try:
        uninstall_shield_bridge()
    except Exception as exc:  # noqa: BLE001 — soft-fail, next step is authoritative
        print(f"{_status_label(False)} (reader: {exc})")
        return False
    try:
        sandbox_uninstall(root=root)
    except (SystemExit, Exception) as exc:  # noqa: BLE001 — aggregator may raise
        print(f"{_status_label(False)} ({exc})")
        return False
    print(f"  {_status_label(True)} (clearance + gate + vault + shield removed)")
    return True


def _purge_credential_db() -> bool:
    """Delete the vault credential database — agents will need re-auth."""
    from terok_sandbox import SandboxConfig

    _stage_begin("Credential DB")
    db_path = SandboxConfig().db_path
    if not db_path.exists():
        print(f"{_status_label(True)} (already absent)")
        return True
    try:
        db_path.unlink()
    except OSError as exc:
        print(f"{_status_label(False)} ({exc})")
        return False
    print(f"{_status_label(True)} (removed {db_path})")
    return True
