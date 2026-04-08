# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Generate a config reference page from the Pydantic YAML schema models.

Runs during ``mkdocs build`` via the mkdocs-gen-files plugin.  Introspects
:class:`~terok.lib.core.yaml_schema.RawProjectYaml` and
:class:`~terok.lib.core.yaml_schema.RawGlobalConfig` to produce:

- Per-section Markdown tables with field name, type, default, and description
- A full annotated YAML example for each config file

The Pydantic models are the **single source of truth** — if a field exists in
the schema, it appears in the docs automatically.  Table and YAML rendering
is delegated to ``mkdocs_terok.config_reference``.
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
# Human-friendly descriptions for fields that lack Field(description=...).
# Key format: "section.field" (dot-separated YAML path).
# ---------------------------------------------------------------------------

_FIELD_DOCS: dict[str, str] = {
    # project.yml — project section
    "project.id": "Unique project identifier (lowercase, ``[a-z0-9_-]``)",
    "project.name": "Human-readable project name (display only)",
    "project.security_class": "Security mode: ``online`` (direct push) or ``gatekeeping`` (gated mirror)",
    # git
    "git.upstream_url": "Repository URL to clone into task containers",
    "git.default_branch": "Default branch name (e.g. ``main``)",
    "git.human_name": "Human name for git committer identity",
    "git.human_email": "Human email for git committer identity",
    "git.authorship": "How agent/human map to git author/committer. Values: ``agent-human``, ``human-agent``, ``agent``, ``human``",
    # ssh
    "ssh.key_name": "SSH key filename (default: ``id_ed25519_<project_id>``)",
    "ssh.host_dir": "Host directory for SSH key storage (keys served via SSH agent proxy, not mounted)",
    "ssh.config_template": "Path to an SSH config template file (supports ``{{IDENTITY_FILE}}``, ``{{KEY_NAME}}``, ``{{PROJECT_ID}}``)",
    "ssh.allow_host_keys": "Allow fallback to ``~/.ssh`` host keys for gate operations (default: ``false``)",
    # tasks
    "tasks.root": "Override task workspace root directory",
    "tasks.name_categories": "Word categories for auto-generated task names (string or list of strings)",
    # gate
    "gate.path": "Override git gate (mirror) path",
    # gatekeeping
    "gatekeeping.staging_root": "Staging directory for gatekeeping builds",
    "gatekeeping.expose_external_remote": "Add upstream URL as ``external`` remote in gatekeeping containers",
    "gatekeeping.upstream_polling.enabled": "Poll upstream for new commits",
    "gatekeeping.upstream_polling.interval_minutes": "Polling interval in minutes",
    "gatekeeping.auto_sync.enabled": "Auto-sync branches from upstream to gate",
    "gatekeeping.auto_sync.branches": "Branch names to auto-sync",
    # run
    "run.shutdown_timeout": "Seconds to wait before SIGKILL on container stop",
    "run.gpus": 'GPU passthrough: ``true``, ``"all"``, or omit to disable',
    # shield
    "shield.drop_on_task_run": "Drop shield (bypass firewall) when task container is created",
    "shield.on_task_restart": "Shield policy on container restart: ``retain`` or ``up``",
    # docker
    "docker.base_image": "Base Docker image for container builds",
    "docker.user_snippet_inline": "Inline Dockerfile snippet injected into the project image",
    "docker.user_snippet_file": "Path to a file containing a Dockerfile snippet",
    # shared dir
    "shared_dir": "Shared directory for multi-agent IPC (``true`` = auto-create under tasks root, or absolute path)",
    # top-level
    "default_agent": "Default agent provider (e.g. ``claude``, ``codex``)",
    "agent": "Agent configuration dict (model, subagents, MCP servers, etc.)",
    # global config — ui
    "ui.base_port": "Base port for web UI task containers",
    # credentials
    "credentials.dir": "Shared credentials directory (proxy DB, agent config mounts)",
    # paths
    "paths.state_dir": "Writable state directory (tasks, caches, builds)",
    "paths.build_dir": "Build artifacts directory (generated Dockerfiles)",
    "paths.user_projects_dir": "User projects directory (per-user project configs)",
    "paths.user_presets_dir": "User presets directory (per-user preset configs)",
    # tui
    "tui.default_tmux": "Default to tmux mode when launching the TUI",
    # logs
    "logs.partial_streaming": "Enable typewriter-effect streaming for log viewing",
    # shield (global)
    "shield.bypass_firewall_no_protection": "**Dangerous**: disable egress firewall entirely",
    "shield.profiles": "Named shield profiles for per-project firewall rules",
    "shield.audit": "Enable shield audit logging",
    # gate_server
    "gate_server.port": "Gate server listen port",
    "gate_server.base_path": "Override gate repo directory (default: ``state_root/gate``)",
    "gate_server.suppress_systemd_warning": "Suppress the systemd unit installation suggestion",
}


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

    buf.write(render_model_tables(RawProjectYaml, field_docs=_FIELD_DOCS))

    buf.write("### Full example\n\n")
    buf.write('```yaml title="project.yml"\n')
    buf.write(render_yaml_example(RawProjectYaml, field_docs=_FIELD_DOCS))
    buf.write("```\n\n")

    # --- config.yml ---
    buf.write(_MD_RULE)
    buf.write("## config.yml\n\n")
    buf.write(
        "Global configuration.  Search order:\n\n"
        "1. `$TEROK_CONFIG_FILE` (explicit override)\n"
        "2. `${XDG_CONFIG_HOME:-~/.config}/terok/config.yml`\n"
        "3. `sys.prefix/etc/terok/config.yml`\n"
        "4. `/etc/terok/config.yml`\n\n"
    )

    buf.write(render_model_tables(RawGlobalConfig, field_docs=_FIELD_DOCS))

    buf.write("### Full example\n\n")
    buf.write('```yaml title="config.yml"\n')
    buf.write(render_yaml_example(RawGlobalConfig, field_docs=_FIELD_DOCS))
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
        "never prevents the TUI or CLI from starting.\n"
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
