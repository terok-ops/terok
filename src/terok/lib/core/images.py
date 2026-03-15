# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container image tag conventions for the terok layer system (L0/L1/L2)."""

import hashlib
import re


def _base_tag(base_image: str) -> str:
    """Derive a safe OCI tag fragment from an arbitrary *base_image* string."""
    raw = (base_image or "").strip()
    if not raw:
        raw = "ubuntu-24.04"
    tag = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-.").lower()
    if not tag:
        tag = "ubuntu-24.04"
    if len(tag) > 120:
        digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:8]
        tag = f"{tag[:111]}-{digest}"
    return tag


def base_dev_image(base_image: str) -> str:
    """Return the L0 base dev image tag for *base_image*."""
    return f"terok-l0:{_base_tag(base_image)}"


def agent_cli_image(base_image: str) -> str:
    """Return the L1 CLI agent image tag for *base_image*."""
    return f"terok-l1-cli:{_base_tag(base_image)}"


def agent_ui_image(base_image: str) -> str:
    """Return the L1 UI agent image tag for *base_image*."""
    return f"terok-l1-ui:{_base_tag(base_image)}"


def project_cli_image(project_id: str) -> str:
    """Return the L2 CLI project image tag for *project_id*."""
    return f"{project_id}:l2-cli"


def project_web_image(project_id: str) -> str:
    """Return the L2 web project image tag for *project_id*."""
    return f"{project_id}:l2-web"


def project_dev_image(project_id: str) -> str:
    """Return the L2 dev project image tag for *project_id*."""
    return f"{project_id}:l2-dev"
