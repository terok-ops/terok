# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate a config reference page from the Pydantic YAML schema models.

Runs during ``mkdocs build`` via the mkdocs-gen-files plugin.  Introspects
[`RawProjectYaml`][terok.lib.core.yaml_schema.RawProjectYaml] and
[`RawGlobalConfig`][terok.lib.core.yaml_schema.RawGlobalConfig] to produce:

- Per-section Markdown tables with field name, type, default, and description
- A full annotated YAML example for each config file

Every ``Field(description=...)`` in the Pydantic models is the **single source
of truth** — if a field exists in the schema, it appears in the docs
automatically.  Table and YAML rendering is delegated to
``mkdocs_terok.config_reference``.
"""

from __future__ import annotations

import io

import mkdocs_gen_files
from mkdocs_terok.config_reference import (
    render_json_schema,
    render_model_tables,
    render_yaml_example,
)

from terok.lib.core.yaml_schema import RawGlobalConfig, RawProjectYaml

_MD_RULE = "---\n\n"
"""Markdown horizontal rule with trailing blank line."""

# ---------------------------------------------------------------------------
# Main: assemble the page
# ---------------------------------------------------------------------------


def _generate() -> str:
    """Generate the full config-reference.md content."""
    buf = io.StringIO()
    buf.write("# Configuration Reference\n\n")
    buf.write(
        "This page is **auto-generated** from the Pydantic schema models in "
        "[`yaml_schema.py`][terok.lib.core.yaml_schema].  "
        "Every field listed here is validated at load time — unknown keys are rejected, "
        "catching typos before they silently do nothing.\n\n"
    )

    buf.write(
        "**JSON Schema files** (for editor autocompletion and validation):\n"
        "[:material-download: project.schema.json](schemas/project.schema.json){: .md-button }\n"
        "[:material-download: config.schema.json](schemas/config.schema.json){: .md-button }\n\n"
    )

    # --- project.yml ---
    buf.write(_MD_RULE)
    buf.write("## project.yml\n\n")
    buf.write(
        "Per-project configuration.  Located at "
        "`<projects-root>/<id>/project.yml`, where the projects root is "
        "discovered via `user_projects_root()` (default "
        "`~/.config/terok/projects`, overridable via `paths.user_projects_root` "
        "in config.yml) or the system config root.\n\n"
    )

    buf.write(render_model_tables(RawProjectYaml))

    buf.write("### Full example\n\n")
    buf.write('```yaml title="project.yml"\n')
    buf.write(render_yaml_example(RawProjectYaml))
    buf.write("```\n\n")

    # --- config.yml ---
    buf.write(_MD_RULE)
    buf.write("## config.yml\n\n")
    buf.write(
        "Global configuration shared by all terok ecosystem packages "
        "(terok, terok-sandbox, terok-executor).  Each package reads only the "
        "sections it understands — terok validates the full file, while "
        "lower-level packages silently ignore sections they don't own.\n\n"
        "Search order:\n\n"
        "1. `$TEROK_CONFIG_FILE` (explicit override)\n"
        "2. `${XDG_CONFIG_HOME:-~/.config}/terok/config.yml`\n"
        "3. `sys.prefix/etc/terok/config.yml`\n"
        "4. `/etc/terok/config.yml`\n\n"
    )

    buf.write(render_model_tables(RawGlobalConfig))

    buf.write("### Full example\n\n")
    buf.write('```yaml title="config.yml"\n')
    buf.write(render_yaml_example(RawGlobalConfig))
    buf.write("```\n\n")

    # --- Validation ---
    buf.write(_MD_RULE)
    buf.write("## Validation behavior\n\n")
    buf.write(
        'All config models use Pydantic v2 with `extra="forbid"`.  This means:\n\n'
        "- **Typos are caught at load time** — e.g. `projecct:` instead of `project:` "
        "produces a clear error with the field path.\n"
        '- **Type mismatches are reported** — e.g. `shutdown_timeout: "ten"` fails '
        "with a descriptive message.\n"
        "- **Enum values are validated** — `security_class` must be `online` or `gatekeeping`.\n"
        "- **Null sections get defaults** — writing `git:` with no sub-keys is equivalent "
        "to omitting the section entirely.\n\n"
        "!!! note\n"
        "    **project.yml** validation is strict: errors produce a clear message and "
        "abort the operation.  **config.yml** validation is lenient: errors are logged "
        "as warnings and the file falls back to defaults, so a typo in global config "
        "never prevents the TUI or CLI from starting.\n\n"
        "### Multi-reader pattern\n\n"
        "config.yml follows the "
        "[Podman model](https://docs.podman.io/en/latest/markdown/podman.1.html"
        "#configuration-files): "
        "one config file, multiple readers.\n\n"
        "| Package | Reads | Validation |\n"
        "| --- | --- | --- |\n"
        '| **terok** | All sections | Pydantic `extra="forbid"` — catches typos everywhere |\n'
        "| **terok-sandbox** | `paths:` only | Raw YAML — ignores unknown top-level keys |\n"
        "| **terok-executor** | Delegates to sandbox | No direct config file reading |\n\n"
        "This means sandbox and executor never reject terok-only sections "
        "(`ui:`, `tui:`, `hooks:`, etc.), while terok catches all typos.\n"
    )

    return buf.getvalue()


_SCHEMA_TITLES: dict[str, str] = {
    "project.schema.json": "terok project.yml",
    "config.schema.json": "terok config.yml",
}

_SCHEMAS = {
    "project.schema.json": RawProjectYaml,
    "config.schema.json": RawGlobalConfig,
}

with mkdocs_gen_files.open("config-reference.md", "w") as f:
    f.write(_generate())

for filename, model in _SCHEMAS.items():
    with mkdocs_gen_files.open(f"schemas/{filename}", "w") as f:
        f.write(render_json_schema(model, title=_SCHEMA_TITLES.get(filename, "")))
