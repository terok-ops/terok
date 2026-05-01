# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Inspect terok configuration: resolution paths, the resolved agent stack, and OpenCode imports.

Exposes the ``terok config`` subcommand group:

- ``config paths`` — list every directory terok reads from or writes to,
  with existence flags and any active environment-variable overrides.
- ``config resolved`` — render the per-project agent config with the
  scope provenance that produced each key (optionally under a preset).
- ``config schema`` — render every available key for ``global`` (config.yml)
  or ``project`` (project.yml), introspected directly from the Pydantic
  models, with types/defaults/descriptions.  ``--json`` emits raw JSON Schema.
- ``config import-opencode`` — copy a user's ``opencode.json`` into the
  shared vault mount so plain ``opencode`` works inside task containers.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from importlib import resources
from pathlib import Path
from typing import Any

from ...lib.core.config import (
    build_dir as _build_dir,
    bundled_presets_dir as _bundled_presets_dir,
    gate_repos_dir as _gate_repos_dir,
    global_config_path as _global_config_path,
    global_config_search_paths as _global_config_search_paths,
    projects_dir as _projects_dir,
    user_presets_dir as _user_presets_dir,
    user_projects_dir as _user_projects_dir,
    vault_dir as _vault_dir,
)
from ...lib.core.paths import core_state_dir as _core_state_dir
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
    """Register the ``config`` subcommand group."""
    p_config = subparsers.add_parser("config", help="Configuration: paths, resolved agent, imports")
    config_sub = p_config.add_subparsers(dest="config_cmd", required=True)

    # config paths — overview of configuration, template and output paths
    config_sub.add_parser("paths", help="Show configuration, template and output paths")

    # config resolved — resolved agent config with provenance
    p_resolved = config_sub.add_parser(
        "resolved",
        help="Show resolved agent config for a project (with provenance per level)",
    )
    set_completer(p_resolved.add_argument("project_id", help="Project ID"), _complete_project_ids)
    from ._completers import complete_preset_names

    set_completer(
        p_resolved.add_argument("--preset", help="Apply a preset before showing resolved config"),
        complete_preset_names,
    )

    # config schema — available keys for global / project YAML
    p_schema = config_sub.add_parser(
        "schema",
        help="Show every available config key (with types, defaults, descriptions)",
    )
    p_schema.add_argument(
        "scope",
        choices=("global", "project"),
        help="``global`` (config.yml) or ``project`` (project.yml)",
    )
    p_schema.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit raw JSON Schema instead of the human-readable tree",
    )

    # config import-opencode — unchanged semantics, now under the group
    p_import_oc = config_sub.add_parser(
        "import-opencode",
        help="Import an OpenCode config file into the shared opencode mount",
    )
    p_import_oc.add_argument("file", help="Path to an opencode.json file to import")


def dispatch(args: argparse.Namespace) -> bool:
    """Handle the ``config`` group.  Returns True if handled."""
    if args.cmd != "config":
        return False
    match args.config_cmd:
        case "paths":
            _print_config()
        case "resolved":
            _cmd_config_resolved(args.project_id, getattr(args, "preset", None))
        case "schema":
            _cmd_config_schema(args.scope, args.as_json)
        case "import-opencode":
            _cmd_import_opencode(args.file)
        case _:  # pragma: no cover — required=True makes argparse enforce this
            return False
    return True


# ── config paths ───────────────────────────────────────────────────────


def _print_config() -> None:
    """Display every configuration, template and output path terok touches."""
    color = _supports_color()
    _print_read_paths(color)
    _print_package_resources(color)
    _print_writable_paths(color)
    _print_environment_overrides(color)
    _print_completion_status(color)


def _print_read_paths(color: bool) -> None:
    """Configuration sources: global config search order, vault, projects, presets."""
    print("Configuration (read):")

    gcfg = _global_config_path()
    print(
        f"- Global config file: {_gray(str(gcfg), color)} "
        f"(exists: {_yes_no(Path(gcfg).is_file(), color)})"
    )
    paths = _global_config_search_paths()
    if paths:
        print("- Config merge order (lowest → highest priority):")
        for p in paths:
            print(f"  • {_gray(str(p), color)} (exists: {_yes_no(Path(p).is_file(), color)})")

    try:
        print(f"- Vault dir: {_gray(str(_vault_dir()), color)}")
    except OSError as e:
        print(f"- Vault dir: error: {e}")

    uproj = _user_projects_dir()
    sproj = _projects_dir()
    print(
        f"- Projects (user): {_gray(str(uproj), color)} "
        f"(exists: {_yes_no(Path(uproj).is_dir(), color)})"
    )
    print(
        f"- Projects (system): {_gray(str(sproj), color)} "
        f"(exists: {_yes_no(Path(sproj).is_dir(), color)})"
    )

    gpresets = _user_presets_dir()
    print(
        f"- Presets (user): {_gray(str(gpresets), color)} "
        f"(exists: {_yes_no(Path(gpresets).is_dir(), color)})"
    )
    bpresets = _bundled_presets_dir()
    print(f"- Presets (bundled): {_gray(str(bpresets), color)}")
    for name in _list_bundled_preset_names(bpresets):
        print(f"  • {name}")

    projs = list_projects()
    if projs:
        print("- Project configs:")
        for p in projs:
            print(f"  • {_violet(str(p.id), color)}: {_gray(str(p.root / 'project.yml'), color)}")
    else:
        print("- Project configs: none found")


def _print_package_resources(color: bool) -> None:
    """Bundled package resources: project Dockerfile templates and helper scripts."""
    print("Templates (read):")
    tmpl_pkg = resources.files("terok") / "resources" / "templates"
    print(f"- Package templates dir: {_gray(str(tmpl_pkg), color)}")
    for name in _list_resource_names(tmpl_pkg, suffix=".template", warn_label="templates"):
        print(f"  • {_gray(str(name), color)}")

    scr_pkg = resources.files("terok") / "resources" / "scripts"
    print(f"Scripts (read):\n- Package scripts dir: {_gray(str(scr_pkg), color)}")
    for name in _list_resource_names(scr_pkg, suffix=None, warn_label="scripts"):
        print(f"  • {_gray(str(name), color)}")


def _print_writable_paths(color: bool) -> None:
    """Locations terok creates or modifies: state dir, gate repos, build outputs."""
    print("Writable locations (write):")
    sdir = _core_state_dir()
    print(f"- State dir: {_gray(str(sdir), color)} (exists: {_yes_no(Path(sdir).is_dir(), color)})")
    gbase = _gate_repos_dir()
    print(
        f"- Gate repos dir: {_gray(str(gbase), color)} (exists: {_yes_no(gbase.is_dir(), color)})"
    )
    bdir = _build_dir()
    print(f"- Build dir: {_gray(str(bdir), color)}")

    projs = list_projects()
    if not projs:
        return
    print("- Expected generated files per project:")
    for p in projs:
        base = bdir / p.id
        for fname in (
            "L0.Dockerfile",
            "L1.cli.Dockerfile",
            "L1.ui.Dockerfile",
            "L2.Dockerfile",
        ):
            path = base / fname
            print(
                f"  • {_violet(str(p.id), color)}: "
                f"{_gray(str(path), color)} "
                f"(exists: {_yes_no(path.is_file(), color)})"
            )


def _print_environment_overrides(color: bool) -> None:
    """Active environment-variable overrides for path resolution, if any."""
    print("Environment overrides (if set):")
    for var in (
        "TEROK_CONFIG_FILE",
        "TEROK_CONFIG_DIR",
        "TEROK_VAULT_DIR",
        "TEROK_STATE_DIR",
        "TEROK_RUNTIME_DIR",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
    ):
        val = os.environ.get(var)
        if val is not None:
            print(f"- {var}={_gray(val, color)}")


def _print_completion_status(color: bool) -> None:
    """Whether shell completions are installed, with a hint to enable them."""
    installed = _is_completion_installed()
    suffix = "" if installed else "  (run: terok completions install)"
    print(f"Shell completions: {_yes_no(installed, color)}{suffix}")


def _list_bundled_preset_names(bpresets: Path) -> list[str]:
    """YAML file stems under the bundled presets directory."""
    try:
        return sorted(
            p.stem for p in bpresets.iterdir() if p.is_file() and p.suffix in (".yml", ".yaml")
        )
    except FileNotFoundError:
        return []
    except OSError as e:
        print(f"  Warning: could not list bundled presets: {e}")
        return []


def _list_resource_names(pkg: Any, *, suffix: str | None, warn_label: str) -> list[str]:
    """Sorted file names under a package resource directory, filtered by *suffix*."""
    try:
        names = [
            child.name
            for child in pkg.iterdir()
            if child.is_file() and (suffix is None or child.name.endswith(suffix))
        ]
    except FileNotFoundError:
        return []
    except OSError as e:
        print(f"  Warning: could not list {warn_label}: {e}")
        return []
    return sorted(names)


# ── config resolved ────────────────────────────────────────────────────


def _cmd_config_resolved(project_id: str, preset: str | None) -> None:
    """Show resolved agent config with provenance annotations."""
    from ...lib.core.projects import load_project
    from ...lib.orchestration.agent_config import build_agent_config_stack

    color = _supports_color()

    project = load_project(project_id)
    stack = build_agent_config_stack(
        project_id,
        agent_config=project.agent_config,
        project_root=project.root,
        preset=preset,
    )
    resolved = stack.resolve()
    scopes = stack.scopes

    if not scopes and not resolved:
        print(f"No agent config defined for project '{project_id}'")
        return

    print(f"Resolved agent config for '{project_id}':")
    if preset:
        print(f"  (with preset: {preset})")
    print()

    for scope in scopes:
        keys = ", ".join(sorted(scope.data.keys()))
        print(f"  [{_gray(scope.level, color)}] keys: {keys}")

    print()
    print(json.dumps(resolved, indent=2, default=str))


# ── config schema ──────────────────────────────────────────────────────


def _cmd_config_schema(scope: str, as_json: bool) -> None:
    """Render every available key of the chosen YAML config surface.

    Pydantic's [`model_json_schema()`][pydantic.BaseModel.model_json_schema] does the
    introspection; this command only formats the result.  ``--json`` is the
    machine-readable escape hatch for jq / docs pipelines; the default human
    rendering is a [`rich.tree.Tree`][rich.tree.Tree] keyed by field path.
    """
    from ...lib.core.yaml_schema import RawGlobalConfig, RawProjectYaml

    model = RawGlobalConfig if scope == "global" else RawProjectYaml
    schema = model.model_json_schema()

    if as_json:
        print(json.dumps(schema, indent=2, default=str))
        return

    from rich.console import Console
    from rich.tree import Tree

    title = f"{scope} config — {model.__name__}"
    tree = Tree(f"[bold]{title}[/]")
    _walk_schema(tree, schema, schema.get("$defs", {}), frozenset())
    # ``soft_wrap`` keeps long descriptions on one line so a wide terminal
    # can show them without rich re-wrapping into the tree gutter.
    Console(soft_wrap=True).print(tree)


def _walk_schema(
    tree: Any,
    node: dict[str, Any],
    defs: dict[str, Any],
    stack: frozenset[str],
) -> None:
    """Add every property of *node* as a child of *tree*, recursing into nested objects."""
    required = set(node.get("required", []))
    for key, prop in node.get("properties", {}).items():
        _add_field(tree, key, prop, defs, stack, key in required)


def _add_field(
    tree: Any,
    key: str,
    prop: dict[str, Any],
    defs: dict[str, Any],
    stack: frozenset[str],
    is_required: bool,
) -> None:
    """Render one ``key: type [= default] — description`` row and recurse if it's an object."""
    from rich.markup import escape

    inner = prop["allOf"][0] if prop.get("allOf") and len(prop["allOf"]) == 1 else prop
    ref_name = inner["$ref"].rsplit("/", 1)[-1] if "$ref" in inner else None
    resolved = defs.get(ref_name, inner) if ref_name else inner

    type_str = escape(_format_type(prop, defs))
    default = _format_default(prop, is_required)
    raw_desc = prop.get("description") or resolved.get("description") or ""
    # Collapse multi-paragraph docstrings to a single line so the tree gutter
    # stays aligned; the JSON Schema export preserves the original formatting.
    desc = " ".join(raw_desc.split())

    label = f"[cyan]{escape(key)}[/]: [yellow]{type_str}[/]{default}"
    if desc:
        label += f"  [dim]— {escape(desc)}[/]"
    sub = tree.add(label)

    if "properties" in resolved:
        if ref_name and ref_name in stack:
            sub.add("[dim](recursive)[/]")
            return
        new_stack = stack | {ref_name} if ref_name else stack
        _walk_schema(sub, resolved, defs, new_stack)


def _format_type(prop: dict[str, Any], defs: dict[str, Any]) -> str:
    """Render a JSON-Schema fragment as a Python-ish type string."""
    if prop.get("allOf") and len(prop["allOf"]) == 1:
        return _format_type(prop["allOf"][0], defs)
    if "$ref" in prop:
        return prop["$ref"].rsplit("/", 1)[-1]
    if "anyOf" in prop:
        return " | ".join(_format_type(p, defs) for p in prop["anyOf"])
    if "enum" in prop:
        return " | ".join(repr(v) for v in prop["enum"])
    if "const" in prop:
        return repr(prop["const"])
    t = prop.get("type")
    if t == "array":
        return f"list[{_format_type(prop.get('items', {}), defs)}]"
    if t == "object":
        ap = prop.get("additionalProperties")
        if isinstance(ap, dict):
            return f"dict[str, {_format_type(ap, defs)}]"
        if ap is True:
            return "dict[str, Any]"
        return "dict"
    if t == "null":
        return "None"
    return t or "Any"


def _format_default(prop: dict[str, Any], is_required: bool) -> str:
    """Render the ``= <default>`` suffix, suppressing noise (None / empty container)."""
    from rich.markup import escape

    if "default" in prop:
        d = prop["default"]
        if d is None or d == {} or d == []:
            return ""
        return f" = [magenta]{escape(repr(d))}[/]"
    if is_required:
        return " [red](required)[/]"
    return ""


# ── config import-opencode ─────────────────────────────────────────────


def _cmd_import_opencode(file_path: str) -> None:
    """Copy an ``opencode.json`` into the shared vault mount used by task containers."""
    src = Path(file_path)
    if not src.is_file():
        raise SystemExit(f"File not found: {src}")

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        raise SystemExit(f"Cannot read config: {e}")
    if not isinstance(data, dict):
        raise SystemExit("Invalid OpenCode config: expected a JSON object")

    dest_dir = _vault_dir() / "_opencode-config"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "opencode.json"
    shutil.copy2(str(src), str(dest))
    print(f"Imported OpenCode config to: {dest}")
    print("This config will be used by plain 'opencode' inside task containers.")
    print(f"To edit further: $EDITOR {dest}")
