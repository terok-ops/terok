# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Declarative wizard schema shared by the CLI prompt loop and the TUI modal.

The wizard asks a fixed set of questions to build a new project config.
Declaring them as :class:`Question` records keeps two presenters — the
CLI's sequential prompts and the TUI's multi-field form — using one
source of truth: same labels, same validation, same transforms.

A presenter's only job is to elicit a raw string per question.  The
shared :func:`validate_answer` then normalises it, runs the question's
validator, and returns either the accepted value or an error the
presenter can display.  When every question has an accepted answer, the
collected values go to :func:`generate_config`, which writes the
``project.yml`` template and returns the path.

:func:`collect_wizard_inputs` is the CLI presenter (uses ``input()``);
the TUI presenter lives in :mod:`terok.tui.wizard_screens`.
"""

from __future__ import annotations

import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from terok.ui_utils.editor import open_in_editor

from ...core.config import user_projects_dir
from ...core.project_model import validate_project_id
from ...util.fs import ensure_dir_writable
from ...util.template_utils import render_template

# ── Vocabulary ────────────────────────────────────────────────────────

# The wizard picks a project template by asking two independent
# questions (security mode + base image) instead of one combinatorial
# menu.  Template files on disk follow ``{security}-{base}.yml``.
SECURITY_CLASSES: list[tuple[str, str]] = [
    ("online", "Online (agent pushes directly to upstream)"),
    ("gatekeeping", "Gatekeeping (changes staged for human review)"),
]
BASES: list[tuple[str, str]] = sorted(
    [
        ("ubuntu", "Ubuntu 24.04"),
        ("fedora", "Fedora 43"),
        ("podman", "Podman (Fedora-based)"),
        ("nvidia", "NVIDIA CUDA (GPU)"),
    ],
    key=lambda b: b[1].casefold(),
)

_TEMPLATE_DIR: Traversable = resources.files("terok") / "resources" / "templates" / "projects"


# ── Question declarations ─────────────────────────────────────────────

QuestionKind = Literal["choice", "text", "editor"]


@dataclass(frozen=True)
class Question:
    """One wizard prompt — what to ask, how to validate, what shape the answer takes.

    The presenter decides the visual treatment (numbered menu vs radio
    buttons, ``input()`` vs Textual ``Input``, ``$EDITOR`` vs ``TextArea``);
    the declaration here drives everything else.
    """

    key: str
    """Name of this field in the collected-values dict."""

    kind: QuestionKind
    """Shape of the input — drives which widget / prompt style a presenter uses."""

    prompt: str
    """Short one-line question, used as both CLI prompt and TUI label."""

    help: str = ""
    """Longer explanation, rendered next to the input in the TUI; unused in CLI."""

    choices: tuple[tuple[str, str], ...] = ()
    """Allowed values for ``kind="choice"`` as ``(value, label)`` pairs."""

    required: bool = False
    """Reject empty answers with ``"<prompt> is required."``"""

    transform: Callable[[str], str] | None = None
    """Optional normalisation applied before validation (e.g. ``str.lower``)."""

    validate: Callable[[str], str | None] | None = None
    """Optional validator returning an error string or ``None`` when accepted."""

    placeholder: str = ""
    """Hint string, rendered inside the Textual ``Input``; unused in CLI."""

    default_visible: bool = False
    """When True, CLI prompt shows ``"(optional)"`` to telegraph "Enter is fine"."""


def _validate_project_id(project_id: str) -> str | None:
    """Return an error message if *project_id* is invalid, else ``None``."""
    if not project_id:
        return "Project ID cannot be empty."
    try:
        validate_project_id(project_id)
    except SystemExit as exc:
        return str(exc)
    return None


_SLUG_ALLOWED = re.compile(r"[a-z0-9_-]+")
_SLUG_RUNS = re.compile(r"-{2,}")


def _slugify_project_id(raw: str) -> str:
    """Best-effort-normalise *raw* into a valid project ID.

    Meets users halfway: ``"terok pages"`` → ``"terok-pages"`` rather than
    bouncing them back with a regex error.  Drops characters outside the
    project-ID alphabet (``[a-z0-9_-]``), collapses runs of hyphens, and
    strips leading/trailing punctuation.  When the input is already
    hopeless (e.g. ``"!!!"``) the result is empty and validation gives
    the user the usual "must start with a lowercase letter…" message.
    """
    lowered = raw.casefold()
    # Whitespace → single hyphen before dropping out-of-alphabet chars so
    # word boundaries survive ("terok pages" shouldn't glue into "terokpages").
    hyphenated = re.sub(r"\s+", "-", lowered)
    kept = "".join(_SLUG_ALLOWED.findall(hyphenated))
    collapsed = _SLUG_RUNS.sub("-", kept)
    return collapsed.strip("-_")


QUESTIONS: tuple[Question, ...] = (
    Question(
        key="security_class",
        kind="choice",
        prompt="Select security mode",
        choices=tuple(SECURITY_CLASSES),
        required=True,
    ),
    Question(
        key="base",
        kind="choice",
        prompt="Select base image",
        choices=tuple(BASES),
        required=True,
    ),
    Question(
        key="project_id",
        kind="text",
        prompt="Project ID",
        required=True,
        transform=_slugify_project_id,
        validate=_validate_project_id,
        placeholder="lowercase; letters, digits, hyphens, underscores",
    ),
    Question(
        key="upstream_url",
        kind="text",
        prompt="Upstream git URL",
        help="Leave empty for a local-only project (no remote).",
        placeholder="git@github.com:org/repo.git or https://…",
        default_visible=True,
    ),
    Question(
        key="default_branch",
        kind="text",
        prompt="Default branch",
        help="Leave empty to use the remote's default (or ``main`` when no remote).",
        placeholder="main",
        default_visible=True,
    ),
    Question(
        key="user_snippet",
        kind="editor",
        prompt="Custom image snippet",
        help=(
            "Optional Dockerfile fragment appended to the project image.  "
            "Use for extra packages, env vars, or setup commands."
        ),
        default_visible=True,
    ),
)


def validate_answer(question: Question, raw: str) -> tuple[str, str | None]:
    """Normalise and validate a raw answer for *question*.

    Returns ``(value, error_or_None)`` — the normalised value and an
    error message if the answer was rejected.  Both presenters call
    this so validation semantics stay identical regardless of UI.

    Normalisation, in order:

    1. Strip surrounding whitespace (copy-paste leftovers, accidental
       trailing spaces).  All-whitespace input is indistinguishable
       from empty for the required/optional check.
    2. Apply ``question.transform`` if set (e.g. ``str.lower``).
    3. Enforce the required flag against the final value.
    4. For ``kind="choice"``, the value must be one of the declared
       slugs — defensive against presenter bugs that might submit a
       label, index, or free-form typo.
    5. Run ``question.validate`` for field-specific rules.
    """
    value = raw.strip()
    if question.transform:
        value = question.transform(value)
    if question.required and not value:
        return value, f"{question.prompt} is required."
    if question.kind == "choice" and value:
        valid_slugs = {slug for slug, _label in question.choices}
        if value not in valid_slugs:
            allowed = ", ".join(sorted(valid_slugs))
            return value, f"{question.prompt} must be one of: {allowed}"
    if question.validate:
        err = question.validate(value)
        if err:
            return value, err
    return value, None


# ── CLI presenter ─────────────────────────────────────────────────────


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


#: Exact text terok writes into the snippet tempfile before handing it to
#: ``$EDITOR``.  Matching this verbatim keeps the trimmer from eating
#: intentional user comments at the top of the file — only *our* boilerplate
#: goes away, any other leading ``#`` lines the user types survive.
_SNIPPET_PREAMBLE = "# Add custom Dockerfile commands below.\n# Empty file = no snippet.\n"


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
        tmp.write(_SNIPPET_PREAMBLE)
        tmp_path = Path(tmp.name)

    try:
        if not open_in_editor(tmp_path):
            print("Editor could not be opened. Skipping snippet.", file=sys.stderr)
            return ""
        content = tmp_path.read_text(encoding="utf-8")
    finally:
        tmp_path.unlink(missing_ok=True)

    return _trim_snippet_preamble(content)


def _trim_snippet_preamble(content: str) -> str:
    """Strip exactly the injected preamble and trailing blanks.

    The earlier implementation pruned every leading comment line, which
    would eat user-intended ``# TODO`` or copyright notices.  We instead
    match :data:`_SNIPPET_PREAMBLE` verbatim — if the user didn't
    remove it, drop it; if they did, leave the rest alone.
    """
    if content.startswith(_SNIPPET_PREAMBLE):
        content = content[len(_SNIPPET_PREAMBLE) :]
    # Trailing blanks stay stripped — meaningful-whitespace policy
    # is the same regardless of whether the preamble was intact.
    return content.rstrip("\n").rstrip()


def _ask_cli(question: Question) -> str | None:
    """Elicit a raw string from the terminal for *question*.

    ``None`` means the user's first interaction was structurally invalid
    for a choice (e.g. non-numeric input to the menu) — the CLI treats
    that as a cancel signal, matching the pre-refactor behaviour.
    """
    match question.kind:
        case "choice":
            return _prompt_choice(question.prompt + ":", list(question.choices))
        case "editor":
            return _prompt_image_snippet()
        case "text":
            # Text prompts allow blank retry; the caller loops until
            # ``validate_answer`` accepts the input.
            return _prompt(
                f"\n{question.prompt}" if question.required else question.prompt,
            )


def collect_wizard_inputs() -> dict | None:
    """Drive the CLI prompt loop for every question in :data:`QUESTIONS`.

    Returns a dict keyed by ``Question.key`` when all answers are
    accepted, or ``None`` if the user cancels (Ctrl+C, EOF, or an
    invalid choice-menu selection).
    """
    values: dict[str, str] = {}
    try:
        for question in QUESTIONS:
            while True:
                raw = _ask_cli(question)
                if raw is None:
                    # Choice menus return None on structurally bad input,
                    # which the pre-refactor flow treated as cancellation.
                    print(f"Invalid {question.key} selection.", file=sys.stderr)
                    return None
                value, error = validate_answer(question, raw)
                if error is None:
                    if question.transform and value != raw.strip():
                        # Surface the normalisation so the user sees *what*
                        # their answer became (e.g. ``"My Proj"`` → ``"my-proj"``).
                        print(f"Note: {question.prompt.lower()} normalised to '{value}'")
                    values[question.key] = value
                    break
                print(error, file=sys.stderr)
        return values
    except (KeyboardInterrupt, EOFError):
        print("\nWizard cancelled.")
        return None


# ── Config rendering ──────────────────────────────────────────────────


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


def render_project_yaml(values: dict) -> str:
    """Render ``project.yml`` without writing it — used by the TUI review screen."""
    filename = f"{values['security_class']}-{values['base']}.yml"
    traversable = _TEMPLATE_DIR / filename
    with resources.as_file(traversable) as template_path:
        return render_template(
            template_path,
            {
                "PROJECT_ID": values["project_id"],
                "UPSTREAM_URL": values["upstream_url"],
                "DEFAULT_BRANCH": values["default_branch"],
                "USER_SNIPPET": values["user_snippet"],
            },
        )


def write_project_yaml(project_id: str, rendered: str, *, overwrite: bool = False) -> Path:
    """Write *rendered* YAML to ``<user_projects_dir>/<project_id>/project.yml``.

    The TUI reviews YAML in a ``TextArea`` before writing, so this is the
    write half of :func:`generate_config` — kept separate so the TUI can
    pass tweaked content without re-rendering the template.
    """
    project_dir = user_projects_dir() / project_id
    ensure_dir_writable(project_dir, "Project")
    config_path = project_dir / "project.yml"
    if config_path.exists() and not overwrite:
        return config_path
    config_path.write_text(rendered, encoding="utf-8")
    return config_path


# ── CLI edit-and-init follow-up ───────────────────────────────────────


def offer_edit_then_init(
    config_path: Path,
    project_id: str,
    init_fn: Callable[[str], None] | None,
) -> None:
    """Interactively review and commission a newly-created project configuration.

    Opens the config in the user's editor (skippable), then offers to run the
    initialisation routine.  On ``KeyboardInterrupt`` or ``EOFError`` the
    half-finished sequence is abandoned cleanly — the config file is kept
    and a manual next-step hint is printed so the user can resume later.
    """
    try:
        edit_answer = input("Edit configuration file before setup? [Y/n]: ").strip().lower()
        if edit_answer not in ("n", "no") and not open_in_editor(config_path):
            print(
                f"Warning: could not open editor — edit file manually: {config_path}",
                file=sys.stderr,
            )

        if init_fn is not None:
            init_answer = input("Run project initialization? [Y/n]: ").strip().lower()
            if init_answer not in ("n", "no"):
                init_fn(project_id)
                print(f"\nProject '{project_id}' is ready.")
                return

        print(f"Next step: terok project init {project_id}")
    except (KeyboardInterrupt, EOFError):
        print(f"\nSkipped. Run manually: terok project init {project_id}")


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

    offer_edit_then_init(config_path, project_id, init_fn)
    return config_path


__all__ = [
    "BASES",
    "QUESTIONS",
    "Question",
    "QuestionKind",
    "SECURITY_CLASSES",
    "collect_wizard_inputs",
    "generate_config",
    "offer_edit_then_init",
    "render_project_yaml",
    "run_wizard",
    "validate_answer",
    "write_project_yaml",
]
