# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""``project`` subcommand group — all per-project operations."""

from __future__ import annotations

import argparse

from ...lib.core.projects import list_presets, list_projects, load_project
from ...lib.domain.facade import (
    build_images,
    delete_project,
    derive_project,
    find_projects_sharing_gate,
    generate_dockerfiles,
    register_ssh_key,
)
from ...lib.domain.project import make_git_gate, make_ssh_manager
from ...lib.domain.wizards.new_project import offer_edit_then_init, run_wizard
from ._completers import complete_project_ids as _complete_project_ids, set_completer
from .setup import cmd_project_init


def _add_project_arg(parser: argparse.ArgumentParser, **kwargs: object) -> None:
    """Add a ``project_id`` positional with project-ID completion."""
    set_completer(parser.add_argument("project_id", **kwargs), _complete_project_ids)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the ``project`` subcommand group."""
    p = subparsers.add_parser("project", help="Create, configure, and manage projects")
    sub = p.add_subparsers(dest="project_cmd", required=True)

    # list
    sub.add_parser("list", help="List all known projects")

    # wizard
    sub.add_parser(
        "wizard",
        help="Interactive wizard to create a new project configuration",
    )

    # derive
    p_derive = sub.add_parser(
        "derive",
        help="Create a new project derived from an existing one (shared infra, fresh agent config)",
    )
    set_completer(
        p_derive.add_argument("source_id", help="Source project ID to derive from"),
        _complete_project_ids,
    )
    p_derive.add_argument("new_id", help="New project ID")

    # delete
    p_delete = sub.add_parser(
        "delete",
        help="Delete a project and all its associated data (non-recoverable)",
    )
    _add_project_arg(p_delete, help="Project ID to delete")
    p_delete.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # init — full setup
    p_init = sub.add_parser(
        "init",
        help="Full project setup: ssh-init + generate + build + gate-sync",
    )
    _add_project_arg(p_init)

    # generate
    p_gen = sub.add_parser("generate", help="Generate Dockerfiles for a project")
    _add_project_arg(p_gen)

    # build
    p_build = sub.add_parser("build", help="Build images for a project")
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
    p_ssh = sub.add_parser(
        "ssh-init",
        help="Initialize shared SSH dir and generate a keypair for a project",
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
    p_gate = sub.add_parser(
        "gate-sync",
        help=(
            "Sync the host-side git gate for a project (creates it if missing). "
            "For SSH upstreams this uses ONLY the project's ssh dir created by "
            "'project ssh-init' (not ~/.ssh)."
        ),
    )
    _add_project_arg(p_gate)
    p_gate.add_argument(
        "--force-reinit",
        dest="force_reinit",
        action="store_true",
        help="Recreate the mirror from scratch",
    )

    # presets — subgroup so future preset ops (add/remove/edit) have a home
    p_presets = sub.add_parser("presets", help="Manage agent-config presets for a project")
    presets_sub = p_presets.add_subparsers(dest="presets_cmd", required=True)
    p_presets_list = presets_sub.add_parser("list", help="List available presets for a project")
    _add_project_arg(p_presets_list)


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the ``project`` group.  Returns True if handled."""
    if args.cmd != "project":
        return False
    match args.project_cmd:
        case "list":
            _cmd_project_list()
        case "wizard":
            run_wizard(init_fn=cmd_project_init)
        case "derive":
            _cmd_project_derive(args.source_id, args.new_id)
        case "delete":
            _cmd_project_delete(args.project_id, force=args.force)
        case "init":
            cmd_project_init(args.project_id)
        case "generate":
            generate_dockerfiles(args.project_id)
        case "build":
            build_images(
                args.project_id,
                include_dev=getattr(args, "dev", False),
                refresh_agents=getattr(args, "refresh_agents", False),
                full_rebuild=getattr(args, "full_rebuild", False),
                agents=getattr(args, "agents", None),
            )
        case "ssh-init":
            _cmd_ssh_init(args)
        case "gate-sync":
            _cmd_gate_sync(args)
        case "presets":
            if args.presets_cmd == "list":
                _cmd_presets(args.project_id)
        case _:  # pragma: no cover — required=True makes argparse enforce this
            return False
    return True


# ── Handlers ───────────────────────────────────────────────────────────


def _cmd_project_list() -> None:
    """List all known projects."""
    projs = list_projects()
    if not projs:
        print("No projects found")
        return
    print("Known projects:")
    for p in projs:
        upstream = p.upstream_url or "-"
        shared = f" shared={p.shared_dir}" if p.shared_dir else ""
        print(f"- {p.id} [{p.security_class}] upstream={upstream}{shared} config_root={p.root}")


def _cmd_project_derive(source_id: str, new_id: str) -> None:
    """Derive a new project from an existing one."""
    project = derive_project(source_id, new_id)
    config_path = project.config.root / "project.yml"
    print(
        f"Derived project '{new_id}' from '{source_id}' — "
        f"shares git gate and SSH key with source.\n"
        f"Config: {config_path}"
    )
    offer_edit_then_init(config_path, new_id, init_fn=cmd_project_init)


def _cmd_project_delete(project_id: str, *, force: bool = False) -> None:
    """Delete a project after confirmation (unless --force)."""
    project = load_project(project_id)
    pid = project.id

    print(f"Project: {pid}")
    print(f"  Config root: {project.root}")
    print(f"  Security class: {project.security_class}")
    if project.upstream_url:
        print(f"  Upstream: {project.upstream_url}")

    sharing = find_projects_sharing_gate(project.gate_path, exclude_project=pid)
    if sharing:
        names = ", ".join(p for p, _ in sharing)
        print(f"\n  Note: gate is shared with: {names} (will NOT be deleted)")

    from ...lib.core.config import archive_dir as _archive_dir

    archive_path = _archive_dir()
    print("\nWARNING: All project data will be permanently deleted.")
    print("Project config, task data, and build artifacts will be archived at:")
    print(f"{archive_path}")

    if not force:
        try:
            answer = input(f"\nType '{pid}' to confirm deletion: ").strip()
        except EOFError:
            print("Deletion cancelled (no interactive stdin). Use --force to skip confirmation.")
            return
        if answer != pid:
            print("Deletion cancelled.")
            return

    result = delete_project(pid)

    print(f"\nProject '{pid}' deleted.")
    if result.get("archive"):
        print(f"Archive: {result['archive']}")
    if result["deleted"]:
        print("Removed:")
        for path in result["deleted"]:
            print(f"  - {path}")
    if result["skipped"]:
        print("Skipped:")
        for reason in result["skipped"]:
            print(f"  - {reason}")


def _cmd_ssh_init(args: argparse.Namespace) -> None:
    """Initialise the project's SSH directory and generate a keypair."""
    project = load_project(args.project_id)
    result = make_ssh_manager(project).init(
        key_type=getattr(args, "key_type", "ed25519"),
        key_name=getattr(args, "key_name", None),
        force=getattr(args, "force", False),
    )
    register_ssh_key(project.id, result)


def _cmd_gate_sync(args: argparse.Namespace) -> None:
    """Sync the host-side git gate for a project."""
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


def _cmd_presets(project_id: str) -> None:
    """List available agent-config presets for a project."""
    presets = list_presets(project_id)
    if not presets:
        print(f"No presets found for project '{project_id}'")
        return
    print(f"Presets for '{project_id}':")
    for info in presets:
        print(f"  - {info.name} ({info.source})")
