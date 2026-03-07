# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Minimal template rendering via ``{{VAR}}`` token replacement."""

from pathlib import Path


def render_template(template_path: Path, variables: dict) -> str:
    """Read *template_path* and replace ``{{KEY}}`` tokens with *variables* values."""
    content = template_path.read_text()
    # Extremely simple token replacement: {{VAR}} -> variables["VAR"]
    for k, v in variables.items():
        content = content.replace(f"{{{{{k}}}}}", str(v))
    return content
