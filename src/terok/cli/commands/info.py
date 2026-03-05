# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Informational CLI commands: config overview and resolved agent config."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from importlib import resources
from pathlib import Path

from ...lib.core.config import (
    build_root as _build_root,
    bundled_presets_dir as _bundled_presets_dir,
    config_root as _config_root,
    get_envs_base_dir as _get_envs_base_dir,
    get_ui_base_port as _get_ui_base_port,
    global_config_path as _global_config_path,
    global_config_search_paths as _global_config_search_paths,
    global_presets_dir as _global_presets_dir,
    state_root as _state_root,
    user_projects_root as _user_projects_root,
)
from ...lib.core.projects import list_projects
from ...ui_utils.terminal import (
    gray as _gray,
    supports_color as _supports_color,
    violet as _violet,
    yes_no as _yes_no,
)
from ._completers import complete_project_ids as _complete_project_ids, set_completer
from .completions import is_completion_installed as _is_completion_installed


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register informational subcommands (config, config-show)."""
    # config overview (with optional import-opencode subcommand)
    p_config = subparsers.add_parser("config", help="Show configuration, template and output paths")
    config_sub = p_config.add_subparsers(dest="config_cmd")
    p_import_oc = config_sub.add_parser(
        "import-opencode",
        help="Import an OpenCode config file into the shared opencode mount",
    )
    p_import_oc.add_argument("file", help="Path to an opencode.json file to import")

    # config-show (resolved agent config with provenance)
    p_config_show = subparsers.add_parser(
        "config-show",
        help="Show resolved agent config for a project (with provenance per level)",
    )
    set_completer(
        p_config_show.add_argument("project_id", help="Project ID"), _complete_project_ids
    )
    p_config_show.add_argument("--preset", help="Apply a preset before showing resolved config")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle config and config-show commands.  Returns True if handled."""
    if args.cmd == "config":
        config_cmd = getattr(args, "config_cmd", None)
        if config_cmd == "import-opencode":
            _cmd_import_opencode(args.file)
        else:
            _print_config()
        return True
    if args.cmd == "config-show":
        _cmd_config_show(args.project_id, getattr(args, "preset", None))
        return True
    return False


def _cmd_config_show(project_id: str, preset: str | None) -> None:
    """Show resolved agent config with provenance annotations."""
    import json

    from ...lib.containers.agent_config import build_agent_config_stack

    color_enabled = _supports_color()

    stack = build_agent_config_stack(project_id, preset=preset)
    resolved = stack.resolve()
    scopes = stack.scopes

    # Print provenance per level
    if not scopes and not resolved:
        print(f"No agent config defined for project '{project_id}'")
        return

    print(f"Resolved agent config for '{project_id}':")
    if preset:
        print(f"  (with preset: {preset})")
    print()

    for scope in scopes:
        keys = ", ".join(sorted(scope.data.keys()))
        print(f"  [{_gray(scope.level, color_enabled)}] keys: {keys}")

    print()
    print(json.dumps(resolved, indent=2, default=str))


def _cmd_import_opencode(file_path: str) -> None:
    """Import an OpenCode config file into the shared opencode mount."""
    src = Path(file_path)
    if not src.is_file():
        raise SystemExit(f"File not found: {src}")

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise SystemExit(f"Cannot read config: {e}")
    if not isinstance(data, dict):
        raise SystemExit("Invalid OpenCode config: expected a JSON object")

    dest_dir = _get_envs_base_dir() / "_opencode-config"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "opencode.json"
    shutil.copy2(str(src), str(dest))
    print(f"Imported OpenCode config to: {dest}")
    print("This config will be used by plain 'opencode' inside task containers.")
    print(f"To edit further: $EDITOR {dest}")


def _print_config() -> None:
    """Display all configuration, template and output paths."""
    color_enabled = _supports_color()
    # READ PATHS
    print("Configuration (read):")
    gcfg = _global_config_path()
    gcfg_exists = Path(gcfg).is_file()
    print(
        f"- Global config file: {_gray(str(gcfg), color_enabled)} "
        f"(exists: {_yes_no(gcfg_exists, color_enabled)})"
    )
    paths = _global_config_search_paths()
    if paths:
        print("- Global config search order:")
        for p in paths:
            exists = Path(p).is_file()
            print(f"  • {_gray(str(p), color_enabled)} (exists: {_yes_no(exists, color_enabled)})")
    print(f"- Web base port: {_get_ui_base_port()}")

    # Envs base dir
    try:
        print(f"- Envs base dir (for mounts): {_gray(str(_get_envs_base_dir()), color_enabled)}")
    except OSError as e:
        print(f"- Envs base dir (for mounts): error: {e}")

    uproj = _user_projects_root()
    sproj = _config_root()
    uproj_exists = Path(uproj).is_dir()
    print(
        f"- User projects root: {_gray(str(uproj), color_enabled)} "
        f"(exists: {_yes_no(uproj_exists, color_enabled)})"
    )
    print(
        f"- System projects root: {_gray(str(sproj), color_enabled)} "
        f"(exists: {_yes_no(Path(sproj).is_dir(), color_enabled)})"
    )
    gpresets = _global_presets_dir()
    print(
        f"- Global presets dir: {_gray(str(gpresets), color_enabled)} "
        f"(exists: {_yes_no(Path(gpresets).is_dir(), color_enabled)})"
    )
    bpresets = _bundled_presets_dir()
    bpresets_names: list[str] = []
    try:
        bpresets_names = sorted(
            p.stem for p in bpresets.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
        )
    except FileNotFoundError:
        pass  # Directory may not exist in some installations
    except OSError as e:
        print(f"  Warning: could not list bundled presets: {e}")
    print(f"- Bundled presets: {_gray(str(bpresets), color_enabled)}")
    if bpresets_names:
        for n in bpresets_names:
            print(f"  • {n}")

    # Project configs discovered
    projs = list_projects()
    if projs:
        print("- Project configs:")
        for p in projs:
            print(
                f"  • {_violet(str(p.id), color_enabled)}: "
                f"{_gray(str(p.root / 'project.yml'), color_enabled)}"
            )
    else:
        print("- Project configs: none found")

    # Templates (package resources)
    print("Templates (read):")
    tmpl_pkg = resources.files("terok") / "resources" / "templates"
    try:
        names = [child.name for child in tmpl_pkg.iterdir() if child.name.endswith(".template")]
    except FileNotFoundError:
        names = []
    except OSError as e:
        names = []
        print(f"  Warning: could not list templates: {e}")
    print(f"- Package templates dir: {_gray(str(tmpl_pkg), color_enabled)}")
    if names:
        for n in sorted(names):
            print(f"  • {_gray(str(n), color_enabled)}")

    # Scripts (package resources)
    scr_pkg = resources.files("terok") / "resources" / "scripts"
    try:
        scr_names = [child.name for child in scr_pkg.iterdir() if child.is_file()]
    except FileNotFoundError:
        scr_names = []
    except OSError as e:
        scr_names = []
        print(f"  Warning: could not list scripts: {e}")
    print(f"Scripts (read):\n- Package scripts dir: {_gray(str(scr_pkg), color_enabled)}")
    if scr_names:
        for n in sorted(scr_names):
            print(f"  • {_gray(str(n), color_enabled)}")

    # WRITE PATHS
    print("Writable locations (write):")
    sroot = _state_root()
    sroot_exists = Path(sroot).is_dir()
    print(
        f"- State root: {_gray(str(sroot), color_enabled)} "
        f"(exists: {_yes_no(sroot_exists, color_enabled)})"
    )
    build_root = _build_root()
    print(f"- Build root for generated files: {_gray(str(build_root), color_enabled)}")
    if projs:
        print("- Expected generated files per project:")
        for p in projs:
            base = build_root / p.id
            for fname in (
                "L0.Dockerfile",
                "L1.cli.Dockerfile",
                "L1.ui.Dockerfile",
                "L2.Dockerfile",
            ):
                path = base / fname
                print(
                    f"  • {_violet(str(p.id), color_enabled)}: "
                    f"{_gray(str(path), color_enabled)} "
                    f"(exists: {_yes_no(path.is_file(), color_enabled)})"
                )

    # ENVIRONMENT
    print("Environment overrides (if set):")
    for var in (
        "TEROK_CONFIG_FILE",
        "TEROK_CONFIG_DIR",
        "TEROK_STATE_DIR",
        "TEROK_RUNTIME_DIR",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
    ):
        val = os.environ.get(var)
        if val is not None:
            print(f"- {var}={_gray(val, color_enabled)}")

    # Shell completions
    comp_installed = _is_completion_installed()
    print(
        f"Shell completions: {_yes_no(comp_installed, color_enabled)}"
        + ("" if comp_installed else "  (run: terokctl completions bash >> ~/.bashrc)")
    )
