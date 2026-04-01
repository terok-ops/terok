# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Project management commands: list, derive, wizard, presets, delete."""

from __future__ import annotations

import argparse

from ...lib.core.projects import derive_project, list_presets, list_projects, load_project
from ...lib.domain.facade import delete_project, find_projects_sharing_gate
from ...lib.domain.wizards.new_project import run_wizard
from ._completers import complete_project_ids as _complete_project_ids, set_completer
from .setup import cmd_project_init


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register project management subcommands."""
    # projects
    subparsers.add_parser("projects", help="List all known projects")

    # project-wizard
    subparsers.add_parser(
        "project-wizard",
        help="Interactive wizard to create a new project configuration",
    )

    # project-derive
    p_derive = subparsers.add_parser(
        "project-derive",
        help="Create a new project derived from an existing one (shared infra, fresh agent config)",
    )
    set_completer(
        p_derive.add_argument("source_id", help="Source project ID to derive from"),
        _complete_project_ids,
    )
    p_derive.add_argument("new_id", help="New project ID")

    # project-delete
    p_delete = subparsers.add_parser(
        "project-delete",
        help="Delete a project and all its associated data (non-recoverable)",
    )
    set_completer(
        p_delete.add_argument("project_id", help="Project ID to delete"),
        _complete_project_ids,
    )
    p_delete.add_argument("--force", action="store_true", help="Skip confirmation prompt")

    # presets
    p_presets = subparsers.add_parser("presets", help="Manage agent config presets")
    presets_sub = p_presets.add_subparsers(dest="presets_cmd", required=True)
    p_presets_list = presets_sub.add_parser("list", help="List available presets for a project")
    set_completer(
        p_presets_list.add_argument("project_id", help="Project ID"), _complete_project_ids
    )


def dispatch(args: argparse.Namespace) -> bool:
    """Handle project management commands.  Returns True if handled."""
    if args.cmd == "projects":
        projs = list_projects()
        if not projs:
            print("No projects found")
        else:
            print("Known projects:")
            for p in projs:
                upstream = p.upstream_url or "-"
                print(f"- {p.id} [{p.security_class}] upstream={upstream} config_root={p.root}")
        return True
    if args.cmd == "project-derive":
        target = derive_project(args.source_id, args.new_id)
        print(f"Derived project '{args.new_id}' from '{args.source_id}' at {target}")
        print("Next steps:")
        print(f"  1. Edit {target / 'project.yml'} (customize agent: section)")
        print(f"  2. Initialize: terok project-init {args.new_id}")
        print("  Tip: global presets are shared across projects (see terok config)")
        return True
    if args.cmd == "project-delete":
        _cmd_project_delete(args.project_id, force=args.force)
        return True
    if args.cmd == "project-wizard":
        run_wizard(init_fn=cmd_project_init)
        return True
    if args.cmd == "presets":
        if args.presets_cmd == "list":
            presets = list_presets(args.project_id)
            if not presets:
                print(f"No presets found for project '{args.project_id}'")
            else:
                print(f"Presets for '{args.project_id}':")
                for info in presets:
                    print(f"  - {info.name} ({info.source})")
            return True
        return False
    return False


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
