# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Global bootstrap: ``terok setup`` installs host services.

Non-interactive, idempotent install of shield hooks, vault, and gate
server.  Per-project operations live under the ``project`` group in
:mod:`project.py`; ``cmd_project_init`` stays here because
``project.py`` (and its tests) import it.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from terok_executor import AUTH_PROVIDERS

from ...lib.core.config import global_config_path
from ...lib.core.projects import load_project
from ...lib.core.yaml_schema import SERVICES_TCP_OPTOUT_YAML
from ...lib.domain.facade import (
    build_images,
    generate_dockerfiles,
    maybe_pause_for_ssh_key_registration,
    provision_ssh_key,
    summarize_ssh_init,
)
from ...lib.domain.project import make_git_gate
from ...lib.util.ansi import bold, green, red, supports_color, yellow


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``setup`` top-level command."""
    p_setup = subparsers.add_parser(
        "setup",
        help="Global bootstrap: install shield, vault, and gate server",
        description=(
            "Non-interactive, idempotent host-level setup.  Installs mandatory "
            "services (shield hooks, vault, gate server) to user-local "
            "directories — no root needed.  Safe to re-run."
        ),
    )
    p_setup.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: report status without installing anything",
    )
    p_setup.add_argument(
        "--no-dbus-bridge",
        action="store_true",
        help=(
            "Skip the optional D-Bus clearance bridge (NFLOG reader resource + "
            "terok-dbus hub unit).  Use on hosts with no session D-Bus or when "
            "auditability of the hook surface is the priority."
        ),
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle ``setup``.  Returns True if handled."""
    if args.cmd != "setup":
        return False
    cmd_setup(
        check_only=getattr(args, "check", False),
        no_dbus_bridge=getattr(args, "no_dbus_bridge", False),
    )
    return True


# ── Global bootstrap (terok setup) ──────────────────────────────────────


_MANDATORY_BINARIES = ("podman", "git", "ssh-keygen")
_RECOMMENDED_BINARIES = ("nft", "dnsmasq", "dig")


def _status_label(ok: bool, color: bool) -> str:
    """Return a coloured status marker."""
    return green("ok", color) if ok else red("FAIL", color)


def _warn_label(color: bool) -> str:
    """Return a coloured warning marker."""
    return yellow("WARN", color)


def _check_host_binaries(color: bool) -> bool:
    """Verify mandatory and recommended host binaries.  Returns True if all mandatory found."""
    all_ok = True

    for name in _MANDATORY_BINARIES:
        found = shutil.which(name) is not None
        status = _status_label(found, color)
        print(f"  {name:<16} {status}")
        if not found:
            all_ok = False

    for name in _RECOMMENDED_BINARIES:
        found = shutil.which(name) is not None
        if found:
            print(f"  {name:<16} {_status_label(True, color)}")
        else:
            print(f"  {name:<16} {_warn_label(color)} (recommended but not required)")

    return all_ok


def _ensure_shield(*, check_only: bool, color: bool) -> bool:
    """Install shield OCI hooks (user-local).  Returns True on success."""
    from terok_sandbox import check_environment, setup_hooks_direct

    ec = check_environment()
    if ec.health == "ok":
        print(f"  Shield hooks     {_status_label(True, color)} (active)")
        return True
    if ec.health == "bypass":
        print(f"  Shield hooks     {_warn_label(color)} (bypass_firewall_no_protection is active)")
        return True
    if check_only:
        hint = ec.setup_hint.splitlines()[0] if ec.setup_hint else "needs setup"
        print(f"  Shield hooks     {_status_label(False, color)} ({hint})")
        return False

    # Force-reinstall to ensure hooks match the current package version
    try:
        setup_hooks_direct(root=False)
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(f"  Shield hooks     {_status_label(False, color)} ({exc})")
        return False

    # Verify installation took effect
    ec = check_environment()
    if ec.health == "ok":
        print(f"  Shield hooks     {_status_label(True, color)} (installed)")
        return True

    print(
        f"  Shield hooks     {_status_label(False, color)} (install succeeded but health: {ec.health})"
    )
    return False


def _ensure_vault(*, check_only: bool, color: bool) -> bool:
    """Install vault and verify it is reachable.  Returns True on success."""
    from terok_sandbox import (
        VaultUnreachableError,
        ensure_vault_reachable,
        get_vault_status,
        install_vault_systemd,
        is_vault_socket_active,
        stop_vault,
        uninstall_vault_systemd,
    )

    from ...lib.core.config import make_sandbox_config

    cfg = make_sandbox_config()

    if check_only:
        # Check-only: just probe reachability
        try:
            ensure_vault_reachable(cfg)
            status = get_vault_status()
            mode = status.mode or "active"
            transport = status.transport or "tcp"
            print(
                f"  Vault            {_status_label(True, color)} ({mode}, {transport}, reachable)"
            )
            return True
        except (VaultUnreachableError, SystemExit):
            installed = is_vault_socket_active()
            state = "installed but NOT reachable" if installed else "not installed"
            print(f"  Vault            {_status_label(False, color)} ({state})")
            return False

    # Clean reinstall: stop → uninstall → install → verify reachability
    try:
        stop_vault(cfg=cfg)
    except Exception:  # noqa: BLE001 — best-effort, may not be running
        pass
    try:
        uninstall_vault_systemd(cfg=cfg)
    except Exception:  # noqa: BLE001 — best-effort, may not be installed
        pass

    from ...lib.core.config import get_services_mode

    transport = get_services_mode()

    try:
        from terok_executor import ensure_vault_routes

        ensure_vault_routes(cfg=cfg)
        install_vault_systemd(cfg=cfg, transport=transport)
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(f"  Vault            {_status_label(False, color)} (install failed: {exc})")
        return False

    # Verify actual TCP reachability (triggers systemd start)
    try:
        ensure_vault_reachable(cfg)
        status = get_vault_status()
        mode = status.mode or "active"
        transport_label = status.transport or "tcp"
        print(
            f"  Vault            {_status_label(True, color)} "
            f"({mode}, {transport_label}, reachable)"
        )
        return True
    except (VaultUnreachableError, SystemExit) as exc:
        print(f"  Vault            {_status_label(False, color)} (installed but NOT reachable)")
        print(f"                   {exc}")
        print("                   Check: journalctl --user -u terok-vault")
        return False


def _ensure_gate(*, check_only: bool, color: bool) -> bool:
    """Install gate server via systemd socket activation.  Returns True on success."""
    from terok_sandbox import (
        ensure_server_reachable,
        get_server_status,
        install_systemd_units,
        is_systemd_available,
        stop_daemon,
        uninstall_systemd_units,
    )

    from ...lib.core.config import make_sandbox_config

    cfg = make_sandbox_config()

    if check_only:
        status = get_server_status(cfg)
        if status.running or status.mode == "systemd":
            # Unit exists (running or socket-activated) — probe TCP to be sure
            try:
                ensure_server_reachable(cfg)
                transport = status.transport or "tcp"
                print(
                    f"  Gate server      {_status_label(True, color)} "
                    f"({status.mode}, {transport}, reachable)"
                )
                return True
            except SystemExit:
                print(
                    f"  Gate server      {_status_label(False, color)} (installed but NOT reachable)"
                )
                return False
        print(f"  Gate server      {_status_label(False, color)} (not installed)")
        return False

    if not is_systemd_available():
        print(f"  Gate server      {_warn_label(color)} (systemd not available, skipping)")
        return True

    from ...lib.core.config import get_services_mode

    transport = get_services_mode()

    # Clean reinstall: stop → uninstall → install → verify
    try:
        stop_daemon(cfg=cfg)
    except Exception:  # noqa: BLE001
        pass
    try:
        uninstall_systemd_units(cfg=cfg)
    except Exception:  # noqa: BLE001
        pass

    try:
        install_systemd_units(cfg=cfg, transport=transport)
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(f"  Gate server      {_status_label(False, color)} (install failed: {exc})")
        return False

    # Verify reachability (triggers socket activation)
    try:
        ensure_server_reachable(cfg)
        print(f"  Gate server      {_status_label(True, color)} (systemd, {transport}, reachable)")
        return True
    except SystemExit as exc:
        print(f"  Gate server      {_status_label(False, color)} (installed but NOT reachable)")
        print(f"                   {exc}")
        return False


def _ensure_dbus_bridge(*, check_only: bool, enabled: bool, color: bool) -> bool:
    """Install the D-Bus clearance bridge: shield reader resource + dbus hub unit.

    Two moving parts: a stdlib-only NFLOG reader script (shipped as a
    terok-shield resource) and the terok-dbus systemd user unit that owns
    ``org.terok.Shield1``.  When *enabled* is ``False`` this phase removes
    any prior installation so the nft hook pair is the only thing on disk.

    Requires ``dbus-send`` on the host; if missing we emit a remediation
    hint but still proceed (the reader soft-fails at hook-fire time).

    Returns ``True`` on success (installed, or skipped intentionally).
    """
    if not enabled:
        return _disable_dbus_bridge(check_only=check_only, color=color)

    dbus_send_ok = _check_dbus_send(color)
    reader_ok = _ensure_bridge_reader(check_only=check_only, color=color)
    hub_ok = _ensure_dbus_hub(check_only=check_only, color=color)
    # dbus-send absence is a warning, not a failure — reader soft-fails on run.
    return reader_ok and hub_ok or not dbus_send_ok  # propagate reader/hub outcome


def _ensure_bridge_reader(*, check_only: bool, color: bool) -> bool:
    """Copy the NFLOG reader script out of terok-shield into the user data dir."""
    dest = Path.home() / ".local" / "share" / "terok-shield" / "nflog-reader.py"
    if check_only:
        present = dest.is_file()
        label = _status_label(present, color)
        suffix = " (installed)" if present else " (not installed)"
        print(f"  Bridge reader    {label}{suffix}")
        return present

    from terok_sandbox import install_shield_bridge

    try:
        install_shield_bridge(dest)
    except Exception as exc:  # noqa: BLE001
        print(f"  Bridge reader    {_status_label(False, color)} ({exc})")
        return False
    print(f"  Bridge reader    {_status_label(True, color)} (installed)")
    return True


def _ensure_dbus_hub(*, check_only: bool, color: bool) -> bool:
    """Install the terok-dbus systemd user unit that owns org.terok.Shield1."""
    unit_path = _user_systemd_dir() / "terok-dbus.service"
    if check_only:
        present = unit_path.is_file()
        label = _status_label(present, color)
        suffix = " (installed)" if present else " (not installed)"
        print(f"  D-Bus hub        {label}{suffix}")
        return present

    try:
        from terok_dbus._install import install_service
    except ImportError as exc:  # noqa: BLE001
        print(f"  D-Bus hub        {_status_label(False, color)} (import failed: {exc})")
        return False

    bin_path = shutil.which("terok-dbus") or f"{sys.executable} -m terok_dbus._cli"
    try:
        install_service(bin_path)
    except Exception as exc:  # noqa: BLE001
        print(f"  D-Bus hub        {_status_label(False, color)} ({exc})")
        return False
    _enable_user_service("terok-dbus")
    print(f"  D-Bus hub        {_status_label(True, color)} (installed + enabled)")
    return True


def _disable_dbus_bridge(*, check_only: bool, color: bool) -> bool:
    """Tear down the bridge installation when the operator runs ``--no-dbus-bridge``."""
    if check_only:
        print(f"  D-Bus bridge     {_warn_label(color)} (opted out via --no-dbus-bridge)")
        return True

    from terok_sandbox import uninstall_shield_bridge

    try:
        uninstall_shield_bridge()
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass

    unit_path = _user_systemd_dir() / "terok-dbus.service"
    if unit_path.is_file():
        unit_path.unlink(missing_ok=True)
        _disable_user_service("terok-dbus")
    print(f"  D-Bus bridge     {_warn_label(color)} (disabled — audit-minimal mode)")
    return True


def _check_dbus_send(color: bool) -> bool:
    """Warn the operator when ``dbus-send`` is missing; doesn't fail setup."""
    present = shutil.which("dbus-send") is not None
    if present:
        return True
    print(
        f"  dbus-send        {_warn_label(color)} "
        f"(missing — install dbus-tools / dbus for clearance signals)"
    )
    return False


def _user_systemd_dir() -> Path:
    """Resolve the user's systemd unit directory (XDG-aware)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def _enable_user_service(unit: str) -> None:
    """``systemctl --user enable --now <unit>`` — silent on hosts without systemctl."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    import subprocess as _sp

    _sp.run(
        [systemctl, "--user", "enable", "--now", unit],
        check=False,
        capture_output=True,
    )


def _disable_user_service(unit: str) -> None:
    """``systemctl --user disable --now <unit>`` — tolerate missing systemctl."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    import subprocess as _sp

    _sp.run(
        [systemctl, "--user", "disable", "--now", unit],
        check=False,
        capture_output=True,
    )


def _check_selinux_policy(*, color: bool) -> bool:
    """Print SELinux prereq status and return whether it's satisfied.

    ``True`` means containers will be able to reach service sockets
    (either because socket mode isn't in use, SELinux isn't enforcing,
    or the policy + libselinux are both ready).  ``False`` means the
    user must run a remediation step (install ``selinux-policy-devel``,
    ``sudo bash install_policy.sh``, or ``dnf install libselinux``)
    before task containers will work — ``cmd_setup`` propagates that
    into a non-zero exit so the setup run fails loudly, matching the
    runtime AVC-denial reality.

    The decision tree is shared with ``terok sickbay`` via
    :func:`terok_sandbox.check_selinux_status`; this function only
    renders it as printed setup output.
    """
    from terok_sandbox import (
        SelinuxStatus,
        check_selinux_status,
        selinux_install_command,
        selinux_install_script,
    )

    from ...lib.core.config import get_services_mode

    result = check_selinux_status(services_mode=get_services_mode())
    if result.status in (
        SelinuxStatus.NOT_APPLICABLE_TCP_MODE,
        SelinuxStatus.NOT_APPLICABLE_PERMISSIVE,
    ):
        return True

    install_cmd = selinux_install_command()
    print()
    print(bold("SELinux:", color))
    match result.status:
        case SelinuxStatus.POLICY_MISSING:
            print(f"  terok_socket_t   {_warn_label(color)} (policy NOT installed)")
            print("                   Containers cannot connect to service sockets.")
            print("                   Fix (pick one):")
            if result.missing_policy_tools:
                tools = ", ".join(result.missing_policy_tools)
                print(f"                   Policy tools missing: {tools}")
                print(
                    f"                     install policy: "
                    f"{bold('sudo dnf install selinux-policy-devel policycoreutils', color)}, "
                    f"then {bold(install_cmd, color)}"
                )
            else:
                print(f"                     install policy: {bold(install_cmd, color)}")
            print(
                f"                     or opt out:     add "
                f"{bold(SERVICES_TCP_OPTOUT_YAML, color)}"
                f" to {global_config_path()}"
            )
            print()
            return False
        case SelinuxStatus.LIBSELINUX_MISSING:
            print(f"  terok_socket_t   {_warn_label(color)} (libselinux.so.1 not loadable)")
            print("                   Sockets will bind as unconfined_t — containers denied.")
            print(f"                   Fix: {bold('sudo dnf install libselinux', color)}")
            print()
            return False
        case SelinuxStatus.OK:
            print(f"  terok_socket_t   {_status_label(True, color)} (policy installed)")
            print(f"                   Installer: {selinux_install_script()}")
            print()
            return True
    return True  # pragma: no cover — exhaustive above; defensive fallthrough for new enum members


def cmd_setup(*, check_only: bool = False, no_dbus_bridge: bool = False) -> None:
    """Global bootstrap: install shield, vault, gate server, and optional D-Bus bridge.

    Non-interactive and idempotent — safe to re-run.  Installs to user-local
    directories (no root needed).  With ``--check``, only reports status.
    ``--no-dbus-bridge`` skips the NFLOG reader resource and the terok-dbus
    hub unit so audit-minimal hosts only see the nft hook pair on disk.
    """
    color = supports_color()
    action = "Checking" if check_only else "Setting up"
    print(bold(f"\n{action} terok host services\n", color))

    # Step 1: Host binary prerequisites
    print(bold("Host binaries:", color))
    binaries_ok = _check_host_binaries(color)
    print()

    # Step 2: SELinux prereq (prints only on enforcing hosts in socket mode;
    # surfaced *before* service install so the fix hint isn't buried below
    # multi-line install output the user has to scroll past).
    selinux_ok = _check_selinux_policy(color=color)

    # Step 3: Services
    print(bold("Services:", color))
    shield_ok = _ensure_shield(check_only=check_only, color=color)

    vault_ok = _ensure_vault(check_only=check_only, color=color)

    gate_ok = _ensure_gate(check_only=check_only, color=color)

    bridge_ok = _ensure_dbus_bridge(check_only=check_only, enabled=not no_dbus_bridge, color=color)
    print()

    # Summary + next steps
    all_ok = binaries_ok and shield_ok and vault_ok and gate_ok and selinux_ok and bridge_ok
    if all_ok:
        print(bold("Setup complete.", color))
    elif not binaries_ok:
        print(bold(red("Missing mandatory binaries — install them first.", color), color))
    elif not selinux_ok:
        print(
            bold(
                yellow(
                    "SELinux prerequisites unmet — task containers will fail until fixed.",
                    color,
                ),
                color,
            )
        )
    else:
        print(bold(yellow("Some services could not be installed (see above).", color), color))

    providers = ", ".join(AUTH_PROVIDERS)
    print(
        f"\nNext steps:\n"
        f"  terok auth <provider> <project>            Authenticate agents ({providers})\n"
        f"  terok project wizard                       Create your first project\n"
    )

    if not binaries_ok:
        sys.exit(2)
    if not all_ok:
        sys.exit(1)


# ── Per-project setup ──────────────────────────────────────────────────


def cmd_project_init(project_id: str) -> None:
    """Full project setup: ssh-init, generate, build, gate-sync."""
    print("==> Initializing SSH...")
    summarize_ssh_init(provision_ssh_key(project_id))
    maybe_pause_for_ssh_key_registration(project_id)

    print("==> Generating Dockerfiles...")
    generate_dockerfiles(project_id)

    print("==> Building images...")
    build_images(project_id)

    print("==> Syncing git gate...")
    res = make_git_gate(load_project(project_id)).sync()
    if not res["success"]:
        raise SystemExit(f"Gate sync failed: {', '.join(res['errors'])}")
    print(f"Gate ready at {res['path']}")
