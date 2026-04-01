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

# Template variants: (label, filename)
TEMPLATES: list[tuple[str, str]] = [
    ("Online – Ubuntu 24.04", "online-ubuntu.yml"),
    ("Online – NVIDIA CUDA (GPU)", "online-nvidia.yml"),
    ("Gatekeeping – Ubuntu 24.04", "gatekeeping-ubuntu.yml"),
    ("Gatekeeping – NVIDIA CUDA (GPU)", "gatekeeping-nvidia.yml"),
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


def _prompt_template() -> int | None:
    """Show numbered template menu and return the 0-based index, or ``None`` on bad input."""
    if not TEMPLATES:
        print("No templates available.", file=sys.stderr)
        return None

    print("\nSelect a project template:")
    for i, (label, _filename) in enumerate(TEMPLATES, 1):
        print(f"  {i}) {label}")

    max_choice = len(TEMPLATES)
    choice = input(f"\nChoice [1-{max_choice}]: ").strip()
    if not choice.isdigit():
        return None
    idx = int(choice) - 1
    if 0 <= idx < max_choice:
        return idx
    return None


def _prompt_docker_snippet() -> str:
    """Optionally open an editor for a custom Docker snippet.

    Returns the snippet text (may be empty if the user skips or the file is empty).
    """
    answer = input("\nAdd a custom Docker snippet? [y/N]: ").strip().lower()
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

    Returns a dict with keys: ``template_index``, ``project_id``,
    ``upstream_url``, ``default_branch``, ``user_snippet``.
    Returns ``None`` if the user cancels (Ctrl+C).
    """
    try:
        # Template selection
        template_idx = _prompt_template()
        if template_idx is None:
            print("Invalid template selection.", file=sys.stderr)
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

        # Docker snippet
        user_snippet = _prompt_docker_snippet()

        return {
            "template_index": template_idx,
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
    _label, filename = TEMPLATES[values["template_index"]]
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
