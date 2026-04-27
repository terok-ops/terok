# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Container image tag conventions for the terok layer system (L0/L1/L2)."""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from typing import TYPE_CHECKING

from terok_executor import AGENTS_LABEL

from . import runtime as _rt

if TYPE_CHECKING:
    from terok.lib.core.project_model import ProjectConfig


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


def project_cli_image(project_id: str) -> str:
    """Return the L2 CLI project image tag for *project_id*."""
    return f"{project_id}:l2-cli"


def project_dev_image(project_id: str) -> str:
    """Return the L2 dev project image tag for *project_id*."""
    return f"{project_id}:l2-dev"


@lru_cache(maxsize=64)
def installed_agents(image_tag: str) -> frozenset[str]:
    """Return the set of agent names baked into *image_tag*.

    Reads the ``ai.terok.agents`` OCI label written by terok-executor's L1
    build (a sorted comma-separated list).  Result is cached per image
    tag, since the label is fixed for the life of an image.

    When the image is not present locally, or the label is missing
    (e.g. a legacy image built before selectable agents), returns an
    empty set — callers treat empty as "unknown / unrestricted" so older
    images keep working.
    """
    csv = (_rt.get_runtime().image(image_tag).labels().get(AGENTS_LABEL) or "").strip()
    if not csv:
        return frozenset()
    return frozenset(name.strip() for name in csv.split(",") if name.strip())


def installed_agents_for_project(project: ProjectConfig) -> frozenset[str]:
    """Return the agents installed in *project*'s L1 image.

    Convenience over [`installed_agents`][] for the very common
    ``installed_agents(agent_cli_image(project.base_image))`` pattern.
    """
    return installed_agents(agent_cli_image(project.base_image))


def is_installed(name: str, image_tag: str) -> bool:
    """Return whether *name* is baked into *image_tag*.

    Treats an unknown / unlabeled image (empty [`installed_agents`][]
    result) as "unrestricted" — every name is considered installed —
    so legacy images keep working until the user rebuilds.
    """
    installed = installed_agents(image_tag)
    return not installed or name in installed


def require_agent_installed(project: ProjectConfig, name: str, *, noun: str = "Agent") -> None:
    """Fail fast if *name* is not baked into *project*'s L1 image.

    Used at CLI / TUI / runtime entry points so the user sees a clear,
    actionable message instead of a deep ``command not found`` later.
    Unlabeled (legacy) images are treated as unrestricted via
    [`is_installed`][].
    """
    image = agent_cli_image(project.base_image)
    if is_installed(name, image):
        return
    available = ", ".join(sorted(installed_agents(image))) or "(none)"
    raise SystemExit(
        f"{noun} {name!r} is not installed in the L1 image for "
        f"project {project.id!r} ({image}).\n"
        f"Installed: {available}\n"
        f"Add it to image.agents and rebuild: "
        f"terok project build --agents {name} {project.id}"
    )
