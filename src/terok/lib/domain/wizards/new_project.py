# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Interactive project wizard for creating new project configurations."""

import sys
import tempfile
from collections.abc import Callable
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from terok.ui_utils.editor import open_in_editor

from ...core.config import user_projects_dir
from ...core.project_model import validate_project_id
from ...util.fs import ensure_dir_writable
from ...util.template_utils import render_template

# The wizard picks a project template by asking two independent
# questions (security mode + base image) instead of one combinatorial
# menu.  Template files on disk follow ``{security}-{base}.yml``.
SECURITY_CLASSES: list[tuple[str, str]] = [
    ("online", "Online (agent pushes directly to upstream)"),
    ("gatekeeping", "Gatekeeping (changes staged for human review)"),
]
BASES: list[tuple[str, str]] = [
    ("ubuntu", "Ubuntu 24.04"),
    ("fedora", "Fedora 43"),
    ("podman", "Podman (Fedora-based)"),
    ("nvidia", "NVIDIA CUDA (GPU)"),
]

_TEMPLATE_DIR: Traversable = resources.files("terok") / "resources" / "templates" / "projects"


def _validate_project_id(project_id: str) -> str | None:
    """Return an error message if *project_id* is invalid, else ``None``."""
    if not project_id:
        return "Project ID cannot be empty."
    try:
        validate_project_id(project_id)
    except SystemExit as exc:
        return str(exc)
    return None


def _prompt(message: str, default: str = "") -> str:
    """Prompt the user for input with an optional default value."""
    suffix = f" [{default}]" if default else ""
    value = input(f"{message}{suffix}: ").strip()
    return value or default


def _prompt_choice(title: str, options: list[tuple[str, str]]) -> str | None:
    """Show a numbered menu and return the selected slug, or ``None`` on bad input."""
    print(f"\n{title}")
    for i, (_slug, label) in enumerate(options, 1):
        print(f"  {i}) {label}")

    choice = input(f"\nChoice [1-{len(options)}]: ").strip()
    if not choice.isdigit():
        return None
    idx = int(choice) - 1
    if 0 <= idx < len(options):
        return options[idx][0]
    return None


def _prompt_image_snippet() -> str:
    """Optionally open an editor for a custom image snippet.

    Returns the snippet text (may be empty if the user skips or the file is empty).
    """
    answer = input("\nAdd a custom image snippet? [y/N]: ").strip().lower()
    if answer not in ("y", "yes"):
        return ""

    with tempfile.NamedTemporaryFile(
        suffix=".dockerfile", prefix="terok-snippet-", mode="w", delete=False
    ) as tmp:
        tmp.write("# Add custom Dockerfile commands below.\n# Empty file = no snippet.\n")
        tmp_path = Path(tmp.name)

    try:
        if not open_in_editor(tmp_path):
            print("Editor could not be opened. Skipping snippet.", file=sys.stderr)
            return ""
        content = tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)

    # Strip comment-only preamble that the user didn't edit, but preserve
    # the original structure (including indentation and internal comments)
    raw_lines = content.splitlines()

    # Skip leading blank or comment-only lines (the boilerplate preamble)
    start_idx = 0
    while start_idx < len(raw_lines):
        stripped = raw_lines[start_idx].strip()
        if stripped and not stripped.startswith("#"):
            break
        start_idx += 1

    trimmed = raw_lines[start_idx:]

    # Optionally strip trailing blank lines to avoid meaningless whitespace
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()

    return "\n".join(trimmed)


def collect_wizard_inputs() -> dict | None:
    """Run the interactive prompt flow and return collected values.

    Returns a dict with keys: ``security_class``, ``base``, ``project_id``,
    ``upstream_url``, ``default_branch``, ``user_snippet``.
    Returns ``None`` if the user cancels (Ctrl+C) or makes an invalid selection.
    """
    try:
        security_class = _prompt_choice("Select security mode:", SECURITY_CLASSES)
        if security_class is None:
            print("Invalid mode selection.", file=sys.stderr)
            return None

        base = _prompt_choice("Select base image:", BASES)
        if base is None:
            print("Invalid base image selection.", file=sys.stderr)
            return None

        # Project ID
        while True:
            project_id = _prompt("\nProject ID")
            lowered = project_id.lower()
            if lowered != project_id:
                print(f"Note: project ID lowercased to '{lowered}'")
                project_id = lowered
            error = _validate_project_id(project_id)
            if error is None:
                break
            print(error, file=sys.stderr)

        # Upstream URL
        while True:
            upstream_url = _prompt("Upstream git URL")
            if upstream_url:
                break
            print("Upstream URL is required.", file=sys.stderr)

        # Default branch (empty = use remote's default branch)
        default_branch = _prompt("Default branch (empty → remote default)")

        # Image snippet
        user_snippet = _prompt_image_snippet()

        return {
            "security_class": security_class,
            "base": base,
            "project_id": project_id,
            "upstream_url": upstream_url,
            "default_branch": default_branch,
            "user_snippet": user_snippet,
        }
    except (KeyboardInterrupt, EOFError):
        print("\nWizard cancelled.")
        return None


def generate_config(values: dict) -> Path:
    """Render the chosen template and write ``project.yml``.

    *values* is the dict returned by :func:`collect_wizard_inputs`.
    Returns the path to the created ``project.yml`` file.
    """
    filename = f"{values['security_class']}-{values['base']}.yml"
    traversable = _TEMPLATE_DIR / filename

    with resources.as_file(traversable) as template_path:
        rendered = render_template(
            template_path,
            {
                "PROJECT_ID": values["project_id"],
                "UPSTREAM_URL": values["upstream_url"],
                "DEFAULT_BRANCH": values["default_branch"],
                "USER_SNIPPET": values["user_snippet"],
            },
        )

    project_dir = user_projects_dir() / values["project_id"]
    ensure_dir_writable(project_dir, "Project")

    config_path = project_dir / "project.yml"

    if config_path.exists():
        try:
            while True:
                answer = (
                    input(
                        f"Configuration for project '{values['project_id']}' already exists "
                        f"at {config_path}. Overwrite? [y/N]: "
                    )
                    .strip()
                    .lower()
                )
                if answer in ("", "n", "no"):
                    print("Keeping existing configuration; no file was overwritten.")
                    return config_path
                if answer in ("y", "yes"):
                    break
                print("Please answer 'y' or 'n'.")
        except (KeyboardInterrupt, EOFError):
            print("\nKeeping existing configuration; no file was overwritten.")
            return config_path

    config_path.write_text(rendered, encoding="utf-8")
    return config_path


def run_wizard(init_fn: Callable[[str], None] | None = None) -> Path | None:
    """Top-level wizard entry point called by the CLI.

    *init_fn* is an optional callable accepting a project ID string that
    performs project initialisation (ssh-init, generate, build, gate-sync).
    When ``None`` (the default), no automatic initialisation is offered.

    Returns the path to the generated config file, or ``None`` on cancellation.
    """
    print("=== terok project wizard ===")
    values = collect_wizard_inputs()
    if values is None:
        return None

    config_path = generate_config(values)
    project_id = values["project_id"]
    print(f"\nProject configuration created: {config_path}")

    try:
        # Offer to edit the generated config before setup
        edit_answer = input("Edit configuration file before setup? [Y/n]: ").strip().lower()
        if edit_answer not in ("n", "no"):
            if not open_in_editor(config_path):
                print(
                    f"Warning: could not open editor — edit file manually: {config_path}",
                    file=sys.stderr,
                )

        # Offer to run project-init if a handler was provided
        if init_fn is not None:
            init_answer = input("Run project initialization? [Y/n]: ").strip().lower()
            if init_answer not in ("n", "no"):
                init_fn(project_id)
                print(f"\nProject '{project_id}' is ready.")
                return config_path

        print(f"Next step: terok project-init {project_id}")
    except (KeyboardInterrupt, EOFError):
        print(f"\nSkipped. Run manually: terok project-init {project_id}")

    return config_path
