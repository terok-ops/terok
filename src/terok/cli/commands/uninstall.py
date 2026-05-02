# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Mirror of ``terok setup``: tears down everything the bootstrap installs.

Reverse install order: desktop entry first (most user-visible), then
the sandbox aggregator's symmetric uninstall.  The aggregator owns
every piece of the service stack — clearance hub/verdict/notifier,
gate, vault, and the shield hook pair (which now installs both nft
and bridge hooks together, so a single shield-uninstall covers the
event wire too).  Terok's wrapper is a thin delegating call.

The standalone NFLOG reader script under XDG_DATA_HOME survives an
uninstall on purpose: it's harmless without the hooks that feed it,
and the next ``terok setup`` overwrites it.

The vault credential DB is also left on disk so a re-install picks up
the operator's tokens and SSH keys without a fresh auth cycle;
``--purge-credentials`` deletes it.
"""

from __future__ import annotations

import argparse

from terok_sandbox import bold, stage_line, yellow

# ── CLI wiring ─────────────────────────────────────────────────────────


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``uninstall`` top-level command."""
    p = subparsers.add_parser(
        "uninstall",
        help="Remove everything `terok setup` installed",
        description=(
            "Symmetric teardown of `terok setup` — removes desktop entry "
            "plus the full sandbox stack (clearance, gate, vault, shield "
            "hooks) via the sandbox aggregator.  The standalone NFLOG "
            "reader script under XDG_DATA_HOME is preserved (harmless "
            "without the hooks that feed it; the next setup overwrites "
            "it).  The vault credential DB is also preserved unless "
            "--purge-credentials is passed."
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
    print(bold("\nUninstalling terok host services\n"))

    all_ok = True
    if not no_desktop_entry:
        all_ok &= _uninstall_desktop_entry()
    if not no_sandbox:
        all_ok &= _uninstall_sandbox_stack(root=root)
    if purge_credentials:
        all_ok &= _purge_credential_db()

    print()
    if all_ok:
        print(bold("Uninstall complete."))
    else:
        print(bold(yellow("Some uninstall phases reported errors (see above).")))
        raise SystemExit(1)


# ── Phase helpers (reverse install order) ──────────────────────────────


def _uninstall_desktop_entry() -> bool:
    """Remove the XDG desktop entry + application icon."""
    from ._desktop_entry import uninstall_desktop_entry

    with stage_line("Desktop entry") as s:
        try:
            uninstall_desktop_entry()
        except Exception as exc:  # noqa: BLE001
            s.fail(str(exc))
            return False
        s.ok("removed")
        return True


def _uninstall_sandbox_stack(*, root: bool) -> bool:
    """Delegate the full teardown to the sandbox aggregator.

    The sandbox aggregator now teardowns both hook pairs (nft + bridge)
    in one shot — shield's ``setup_global_hooks`` installs them
    together since the dossier-in-events refactor — so terok's wrapper
    is a thin delegating call.  The standalone NFLOG reader script
    survives an uninstall on purpose: it's harmless without the hooks
    that feed it, and the reinstall path overwrites it.
    """
    from terok_sandbox import sandbox_uninstall

    with stage_line("Sandbox stack") as s:
        try:
            sandbox_uninstall(root=root)
        except (SystemExit, Exception) as exc:  # noqa: BLE001 — aggregator may raise
            s.fail(str(exc))
            return False
        s.ok("clearance + gate + vault + shield removed")
        return True


def _purge_credential_db() -> bool:
    """Delete the vault credential database — agents will need re-auth."""
    from terok_sandbox import SandboxConfig

    with stage_line("Credential DB") as s:
        db_path = SandboxConfig().db_path
        if not db_path.exists():
            s.ok("already absent")
            return True
        try:
            db_path.unlink()
        except OSError as exc:
            s.fail(str(exc))
            return False
        s.ok(f"removed {db_path}")
        return True
