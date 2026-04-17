# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Infrastructure setup commands: global bootstrap plus per-project init.

``terok setup`` — non-interactive, idempotent global bootstrap that installs
shield hooks, credential proxy, and gate server (user-local, no root).

Per-project commands (generate, build, ssh-init, gate-sync, auth) live
alongside for backward compatibility.
"""

from __future__ import annotations

import argparse
import shutil
import sys

from terok_executor import AUTH_PROVIDERS

from ...lib.core.images import require_agent_installed
from ...lib.core.projects import load_project
from ...lib.domain.facade import (
    authenticate,
    build_images,
    generate_dockerfiles,
    maybe_pause_for_ssh_key_registration,
    register_ssh_key,
)
from ...lib.domain.project import make_git_gate, make_ssh_manager
from ...lib.util.ansi import bold, green, red, supports_color, yellow
from ._completers import complete_project_ids as _complete_project_ids, set_completer


def _add_project_arg(parser: argparse.ArgumentParser, **kwargs: object) -> None:
    """Add a ``project_id`` positional with project-ID completion."""
    set_completer(parser.add_argument("project_id", **kwargs), _complete_project_ids)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register infrastructure setup subcommands."""
    # setup (global bootstrap)
    p_setup = subparsers.add_parser(
        "setup",
        help="Global bootstrap: install shield, credential proxy, and gate server",
        description=(
            "Non-interactive, idempotent host-level setup.  Installs mandatory "
            "services (shield hooks, credential proxy, gate server) to user-local "
            "directories — no root needed.  Safe to re-run."
        ),
    )
    p_setup.add_argument(
        "--check",
        action="store_true",
        help="Dry-run: report status without installing anything",
    )

    # generate
    p_gen = subparsers.add_parser("generate", help="Generate Dockerfiles for a project")
    _add_project_arg(p_gen)

    # build
    p_build = subparsers.add_parser("build", help="Build images for a project")
    _add_project_arg(p_build)
    p_build.add_argument(
        "--refresh-agents",
        dest="refresh_agents",
        action="store_true",
        help="Rebuild from L0 with fresh agent installs (cache bust)",
    )
    p_build.add_argument(
        "--agents",
        dest="agents",
        default=None,
        metavar="LIST",
        help=(
            'Comma-separated roster entries to install in L1, or "all". '
            "Overrides the project's image.agents for this build only."
        ),
    )
    p_build.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Rebuild from L0 (no cache) (includes base image pull and apt packages)",
    )
    p_build.add_argument(
        "--dev",
        action="store_true",
        help="Also build a manual dev image from L0 (tagged as <project>:l2-dev)",
    )

    # ssh-init
    p_ssh = subparsers.add_parser(
        "ssh-init", help="Initialize shared SSH dir and generate a keypair for a project"
    )
    _add_project_arg(p_ssh)
    p_ssh.add_argument(
        "--key-type",
        choices=["ed25519", "rsa"],
        default="ed25519",
        help="Key algorithm (default: ed25519)",
    )
    p_ssh.add_argument(
        "--key-name",
        default=None,
        help="Key file name (without .pub). Default: id_<type>_<project>",
    )
    p_ssh.add_argument("--force", action="store_true", help="Overwrite existing key and config")

    # gate-sync
    p_gate = subparsers.add_parser(
        "gate-sync",
        help=(
            "Sync the host-side git gate for a project (creates it if missing). "
            "For SSH upstreams this uses ONLY the project's ssh dir created by "
            "'ssh-init' (not ~/.ssh)."
        ),
    )
    _add_project_arg(p_gate)
    p_gate.add_argument(
        "--force-reinit",
        dest="force_reinit",
        action="store_true",
        help="Recreate the mirror from scratch",
    )

    # project-init
    p_pinit = subparsers.add_parser(
        "project-init",
        help="Full project setup: ssh-init + generate + build + gate-sync",
    )
    _add_project_arg(p_pinit)

    # auth
    provider_names = list(AUTH_PROVIDERS)
    providers_help = ", ".join(f"{p.name} ({p.label})" for p in AUTH_PROVIDERS.values())
    p_auth = subparsers.add_parser(
        "auth",
        help="Authenticate an agent/tool for a project",
        description=f"Available providers: {providers_help}",
    )
    p_auth.add_argument("provider", choices=provider_names, metavar="provider")
    _add_project_arg(p_auth)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle infrastructure setup commands.  Returns True if handled."""
    if args.cmd == "setup":
        cmd_setup(check_only=getattr(args, "check", False))
        return True
    if args.cmd == "generate":
        generate_dockerfiles(args.project_id)
        return True
    if args.cmd == "build":
        build_images(
            args.project_id,
            include_dev=getattr(args, "dev", False),
            refresh_agents=getattr(args, "refresh_agents", False),
            full_rebuild=getattr(args, "full_rebuild", False),
            agents=getattr(args, "agents", None),
        )
        return True
    if args.cmd == "ssh-init":
        project = load_project(args.project_id)
        result = make_ssh_manager(project).init(
            key_type=getattr(args, "key_type", "ed25519"),
            key_name=getattr(args, "key_name", None),
            force=getattr(args, "force", False),
        )
        register_ssh_key(project.id, result)
        return True
    if args.cmd == "gate-sync":
        res = make_git_gate(load_project(args.project_id)).sync(
            force_reinit=getattr(args, "force_reinit", False),
        )
        if not res["success"]:
            raise SystemExit(f"Gate sync failed: {', '.join(res['errors'])}")
        cache_note = " (clone cache refreshed)" if res.get("cache_refreshed") else ""
        print(
            f"Gate ready at {res['path']} "
            f"(upstream: {res['upstream_url']}; created: {res['created']}){cache_note}"
        )
        return True
    if args.cmd == "project-init":
        cmd_project_init(args.project_id)
        return True
    if args.cmd == "auth":
        require_agent_installed(load_project(args.project_id), args.provider, noun="Provider")
        authenticate(args.project_id, args.provider)
        return True
    return False


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


def _ensure_proxy(*, check_only: bool, color: bool) -> bool:
    """Install credential proxy and verify it is reachable.  Returns True on success."""
    from terok_sandbox import (
        ProxyUnreachableError,
        ensure_proxy_reachable,
        get_proxy_status,
        install_proxy_systemd,
        is_proxy_socket_active,
        stop_proxy,
        uninstall_proxy_systemd,
    )

    from ...lib.core.config import make_sandbox_config

    cfg = make_sandbox_config()

    if check_only:
        # Check-only: just probe reachability
        try:
            ensure_proxy_reachable(cfg)
            mode = get_proxy_status().mode or "active"
            print(f"  Credential proxy {_status_label(True, color)} ({mode}, reachable)")
            return True
        except (ProxyUnreachableError, SystemExit):
            installed = is_proxy_socket_active()
            state = "installed but NOT reachable" if installed else "not installed"
            print(f"  Credential proxy {_status_label(False, color)} ({state})")
            return False

    # Clean reinstall: stop → uninstall → install → verify reachability
    try:
        stop_proxy(cfg=cfg)
    except Exception:  # noqa: BLE001 — best-effort, may not be running
        pass
    try:
        uninstall_proxy_systemd(cfg=cfg)
    except Exception:  # noqa: BLE001 — best-effort, may not be installed
        pass

    from ...lib.core.config import get_services_mode

    transport = get_services_mode()

    try:
        from terok_executor import ensure_proxy_routes

        ensure_proxy_routes(cfg=cfg)
        install_proxy_systemd(cfg=cfg, transport=transport)
    except Exception as exc:  # noqa: BLE001 — best-effort
        print(f"  Credential proxy {_status_label(False, color)} (install failed: {exc})")
        return False

    # Verify actual TCP reachability (triggers systemd start)
    try:
        ensure_proxy_reachable(cfg)
        mode = get_proxy_status().mode or "active"
        print(f"  Credential proxy {_status_label(True, color)} ({mode}, reachable)")
        return True
    except (ProxyUnreachableError, SystemExit) as exc:
        print(f"  Credential proxy {_status_label(False, color)} (installed but NOT reachable)")
        print(f"                   {exc}")
        print("                   Check: journalctl --user -u terok-credential-proxy")
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
                print(f"  Gate server      {_status_label(True, color)} ({status.mode}, reachable)")
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
        print(f"  Gate server      {_status_label(True, color)} (systemd, reachable)")
        return True
    except SystemExit as exc:
        print(f"  Gate server      {_status_label(False, color)} (installed but NOT reachable)")
        print(f"                   {exc}")
        return False


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
            if result.missing_policy_tools:
                tools = ", ".join(result.missing_policy_tools)
                print(f"                   Policy tools missing: {tools}")
                print(
                    f"                   Fix: "
                    f"{bold('sudo dnf install selinux-policy-devel policycoreutils', color)}, "
                    f"then {bold(install_cmd, color)}"
                )
            else:
                print("                   Containers cannot connect to service sockets.")
                print(f"                   Fix: {bold(install_cmd, color)}")
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


def cmd_setup(*, check_only: bool = False) -> None:
    """Global bootstrap: install shield, credential proxy, and gate server.

    Non-interactive and idempotent — safe to re-run.  Installs to user-local
    directories (no root needed).  With ``--check``, only reports status.
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
    proxy_ok = _ensure_proxy(check_only=check_only, color=color)
    gate_ok = _ensure_gate(check_only=check_only, color=color)
    print()

    # Summary + next steps
    all_ok = binaries_ok and shield_ok and proxy_ok and gate_ok and selinux_ok
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
        f"  terok auth <provider> <project>    Authenticate agents ({providers})\n"
        f"  terok project-wizard               Create your first project\n"
    )

    if not binaries_ok:
        sys.exit(2)
    if not all_ok:
        sys.exit(1)


# ── Per-project setup ──────────────────────────────────────────────────


def cmd_project_init(project_id: str) -> None:
    """Full project setup: ssh-init, generate, build, gate-sync."""
    project = load_project(project_id)

    print("==> Initializing SSH...")
    result = make_ssh_manager(project).init()
    register_ssh_key(project_id, result)
    maybe_pause_for_ssh_key_registration(project_id)

    print("==> Generating Dockerfiles...")
    generate_dockerfiles(project_id)

    print("==> Building images...")
    build_images(project_id)

    print("==> Syncing git gate...")
    res = make_git_gate(project).sync()
    if not res["success"]:
        raise SystemExit(f"Gate sync failed: {', '.join(res['errors'])}")
    print(f"Gate ready at {res['path']}")
