# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Infrastructure setup commands: generate, build, ssh-init, gate-sync, auth."""

from __future__ import annotations

import argparse

from ...lib.core.projects import load_project
from ...lib.facade import (
    AUTH_PROVIDERS,
    GitGate,
    SSHManager,
    authenticate,
    build_images,
    generate_dockerfiles,
    maybe_pause_for_ssh_key_registration,
)
from ._completers import complete_project_ids as _complete_project_ids, set_completer


def _add_project_arg(parser: argparse.ArgumentParser, **kwargs: object) -> None:
    """Add a ``project_id`` positional with project-ID completion."""
    set_completer(parser.add_argument("project_id", **kwargs), _complete_project_ids)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register infrastructure setup subcommands."""
    # generate
    p_gen = subparsers.add_parser("generate", help="Generate Dockerfiles for a project")
    _add_project_arg(p_gen)

    # build
    p_build = subparsers.add_parser("build", help="Build images for a project")
    _add_project_arg(p_build)
    p_build.add_argument(
        "--agents",
        action="store_true",
        help="Rebuild L0+L1+L2 with fresh agent installs (codex, claude, opencode, vibe)",
    )
    p_build.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Full rebuild with no cache (includes base image pull and apt packages)",
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
    if args.cmd == "generate":
        generate_dockerfiles(args.project_id)
        return True
    if args.cmd == "build":
        build_images(
            args.project_id,
            include_dev=getattr(args, "dev", False),
            rebuild_agents=getattr(args, "agents", False),
            full_rebuild=getattr(args, "full_rebuild", False),
        )
        return True
    if args.cmd == "ssh-init":
        SSHManager(load_project(args.project_id)).init(
            key_type=getattr(args, "key_type", "ed25519"),
            key_name=getattr(args, "key_name", None),
            force=getattr(args, "force", False),
        )
        return True
    if args.cmd == "gate-sync":
        res = GitGate(load_project(args.project_id)).sync(
            force_reinit=getattr(args, "force_reinit", False),
        )
        if not res["success"]:
            raise SystemExit(f"Gate sync failed: {', '.join(res['errors'])}")
        print(
            f"Gate ready at {res['path']} "
            f"(upstream: {res['upstream_url']}; created: {res['created']})"
        )
        return True
    if args.cmd == "project-init":
        cmd_project_init(args.project_id)
        return True
    if args.cmd == "auth":
        authenticate(args.project_id, args.provider)
        return True
    return False


def cmd_project_init(project_id: str) -> None:
    """Full project setup: ssh-init, generate, build, gate-sync."""
    project = load_project(project_id)

    print("==> Initializing SSH...")
    SSHManager(project).init()
    maybe_pause_for_ssh_key_registration(project_id)

    print("==> Generating Dockerfiles...")
    generate_dockerfiles(project_id)

    print("==> Building images...")
    build_images(project_id)

    print("==> Syncing git gate...")
    res = GitGate(project).sync()
    if not res["success"]:
        raise SystemExit(f"Gate sync failed: {', '.join(res['errors'])}")
    print(f"Gate ready at {res['path']}")
