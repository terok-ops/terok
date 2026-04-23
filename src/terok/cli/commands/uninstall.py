# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Mirror of ``terok setup``: tears down everything the bootstrap installs.

Runs the phases in reverse install order — desktop entry first (most
user-visible), then the D-Bus bridge, then the sandbox aggregator
(gate → vault → shield hooks).  The vault credential DB is left on
disk so a re-install picks up the operator's tokens and SSH keys
without a fresh auth cycle; ``--purge-credentials`` deletes it.
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
            "Symmetric teardown of `terok setup` — removes desktop entry, "
            "D-Bus bridge, gate, vault, and shield hooks from user-local "
            "directories.  The vault credential DB is preserved unless "
            "--purge-credentials is passed.  Safe to re-run."
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
        "--no-dbus-bridge",
        action="store_true",
        help="Skip the D-Bus clearance bridge removal",
    )
    p.add_argument(
        "--no-sandbox",
        action="store_true",
        help="Skip the shield+vault+gate teardown",
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
        no_dbus_bridge=getattr(args, "no_dbus_bridge", False),
        no_sandbox=getattr(args, "no_sandbox", False),
        purge_credentials=getattr(args, "purge_credentials", False),
    )
    return True


# ── Orchestrator ───────────────────────────────────────────────────────


def cmd_uninstall(
    *,
    root: bool = False,
    no_desktop_entry: bool = False,
    no_dbus_bridge: bool = False,
    no_sandbox: bool = False,
    purge_credentials: bool = False,
) -> None:
    """Tear down every phase ``terok setup`` installs.

    Phase order is the reverse of install: user-visible surfaces first
    (desktop entry), then operator-visible surfaces (D-Bus bridge),
    then the sandbox stack.  A running container survives a gate/vault
    teardown more gracefully than it survives losing its shield hooks,
    so shield-hooks go last.
    """
    print(_bold("\nUninstalling terok host services\n"))

    all_ok = True

    if not no_desktop_entry:
        all_ok &= _uninstall_desktop_entry()

    if not no_dbus_bridge:
        all_ok &= _uninstall_dbus_bridge()

    if not no_sandbox:
        all_ok &= _uninstall_sandbox_services(root=root)

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


def _uninstall_dbus_bridge() -> bool:
    """Remove the NFLOG reader resource + clearance hub/verdict pair.

    ``terok_clearance.uninstall_service`` owns the systemctl teardown
    + unlink + daemon-reload sequence for both units — including
    migration of a legacy pre-split ``terok-dbus.service`` — so this
    stage is thin on top of it.
    """
    from terok_clearance import uninstall_service
    from terok_sandbox import uninstall_shield_bridge

    _stage_begin("Clearance bridge")
    try:
        uninstall_shield_bridge()
    except Exception as exc:  # noqa: BLE001
        print(f"{_status_label(False)} (reader: {exc})")
        return False

    try:
        uninstall_service()
    except Exception as exc:  # noqa: BLE001
        print(f"{_status_label(False)} (hub/verdict teardown: {exc})")
        return False

    print(f"{_status_label(True)} (removed)")
    return True


def _uninstall_sandbox_services(*, root: bool) -> bool:
    """Delegate gate + vault + shield-hooks teardown to the sandbox aggregator."""
    from terok_sandbox.commands import _handle_sandbox_uninstall

    _stage_begin("Sandbox services")
    try:
        _handle_sandbox_uninstall(root=root)
    except (SystemExit, Exception) as exc:  # noqa: BLE001
        print(f"{_status_label(False)} ({exc})")
        return False
    print(f"  {_status_label(True)} (shield + vault + gate removed)")
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
