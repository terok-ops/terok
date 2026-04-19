# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Read-only project state inspection and reporting.

Aggregates infrastructure status (dockerfiles, images, SSH, gate) for a
project by querying podman and the filesystem.  Used by both CLI and TUI
for overview displays.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from terok_sandbox import container_image, image_exists, image_labels, is_container_running

from ..core.config import build_dir
from ..core.images import project_cli_image
from ..core.projects import load_project, resolve_ssh_host_dir
from ..core.task_display import container_name as _container_name

if TYPE_CHECKING:
    from ..core.project_model import ProjectConfig


def get_project_state(
    project_id: str,
    gate_commit_provider: Callable[[str], dict | None] | None = None,
    *,
    project: "ProjectConfig | None" = None,
) -> dict:
    """Return a summary of per-project infrastructure state.

    The resulting dict contains boolean flags that can be used by UIs
    (including the TUI) to give a quick overview of the project:

    - ``dockerfiles`` - True if required Dockerfiles exist under the build root
      (L0/L1.cli/L2).
    - ``images`` - True if the required ``<id>:l2-cli`` project image exists.
    - ``ssh`` - True if the project SSH directory exists and contains
      a ``config`` file.
    - ``gate`` - True if the project's git gate directory exists.
    - ``gate_last_commit`` - Dict with commit info if gate exists, None otherwise.

    Args:
        project_id: The project to inspect.
        gate_commit_provider: Optional callback to retrieve the last gate commit.
        project: Pre-loaded project config; avoids redundant ``load_project``.
    """

    project = project or load_project(project_id)

    # Dockerfiles: look in the same location generate_dockerfiles writes to.
    stage_dir = build_dir() / project.id
    dockerfiles = [
        stage_dir / "L0.Dockerfile",
        stage_dir / "L1.cli.Dockerfile",
        stage_dir / "L2.Dockerfile",
    ]
    has_dockerfiles = all(p.is_file() for p in dockerfiles)

    # Images: rely on image tags created by build_images().
    required_tags = [project_cli_image(project.id)]
    has_images = all(image_exists(tag) for tag in required_tags)

    rendered: dict[str, str] | None = None
    dockerfiles_old = False
    if has_dockerfiles:
        try:
            from ..orchestration.image import render_all_dockerfiles
        except ImportError:
            dockerfiles_old = False
        else:
            try:
                rendered = render_all_dockerfiles(project)
                dockerfiles_old = any(
                    not (stage_dir / name).is_file() or (stage_dir / name).read_text() != expected
                    for name, expected in rendered.items()
                )
            except Exception as exc:
                from ..util.logging_utils import log_warning

                log_warning(f"Template comparison failed for {project_id}: {exc}")
                dockerfiles_old = False

    images_old = False
    stale_layers: list[str] = []
    if has_images and has_dockerfiles:
        if dockerfiles_old:
            images_old = True
            stale_layers = ["l0", "l1", "l2"]
        else:
            stale_layers = _detect_stale_layers(project, rendered)
            images_old = bool(stale_layers)

    # SSH: consider SSH "ready" when the key directory and its config file exist.
    # Falls back to the managed ssh-keys store (same as SSHManager / git gate).
    ssh_dir = resolve_ssh_host_dir(project).expanduser().resolve()
    has_ssh = ssh_dir.is_dir() and (ssh_dir / "config").is_file()

    # Gate: a mirror bare repo initialized by sync_project_gate(). We
    # treat existence of the directory as "gate present".
    gate_dir = project.gate_path
    has_gate = gate_dir.is_dir()

    # Get gate commit info if gate exists (best-effort; errors degrade to None)
    gate_last_commit = None
    if has_gate and gate_commit_provider is not None:
        try:
            gate_last_commit = gate_commit_provider(project_id)
        except Exception as exc:
            from ..util.logging_utils import log_warning

            log_warning(f"Gate commit lookup failed for {project_id}: {exc}")
            gate_last_commit = None

    return {
        "dockerfiles": has_dockerfiles,
        "dockerfiles_old": dockerfiles_old,
        "images": has_images,
        "images_old": images_old,
        "stale_layers": stale_layers,
        "ssh": has_ssh,
        "gate": has_gate,
        "gate_last_commit": gate_last_commit,
    }


def _detect_stale_layers(project: "ProjectConfig", rendered: dict[str, str] | None) -> list[str]:
    """Compare per-layer content hashes against the build manifest.

    Returns a list of stale layer names (``"l0"``, ``"l1"``, ``"l2"``).
    Missing manifest → all layers stale.
    """
    if not rendered:
        return ["l0", "l1", "l2"]

    try:
        from ..orchestration.image import (
            l0_content_hash,
            l1_content_hash,
            l2_content_hash,
            read_build_manifest,
        )

        current = {
            "l0": l0_content_hash(project.base_image, rendered),
            "l1": l1_content_hash(rendered),
            "l2": l2_content_hash(rendered),
        }
        manifest = read_build_manifest(project.id)
    except (ImportError, OSError, ValueError, KeyError):
        return ["l0", "l1", "l2"]

    if manifest is None:
        return ["l0", "l1", "l2"]

    stale: list[str] = []
    for layer in ("l0", "l1"):
        entry = manifest.get(layer)
        if not isinstance(entry, dict) or entry.get("content_hash") != current[layer]:
            stale.append(layer)
    # L2 uses "l2_cli" key in the manifest
    l2_entry = manifest.get("l2_cli")
    if not isinstance(l2_entry, dict) or l2_entry.get("content_hash") != current["l2"]:
        stale.append("l2")
    return stale


def is_task_image_old(project_id: str | None, task: Any) -> bool | None:
    """Check if the image used by a task's container is outdated.

    Compares the build context hash label on the running container's image
    against the current build context hash for the project.

    Args:
        project_id: The project ID, or None.
        task: A TaskMeta instance with task_id and mode attributes.

    Returns:
        True if the image is old, False if current, None if unable to determine.
    """
    if project_id is None:
        return None
    if task.mode != "cli":
        return None

    cname = _container_name(project_id, task.mode, task.task_id)
    if not is_container_running(cname):
        return None
    image_id = container_image(cname)
    if not image_id:
        return None

    try:
        from ..orchestration.image import render_all_dockerfiles

        project = load_project(project_id)
        rendered = render_all_dockerfiles(project)
        stale = _detect_stale_layers(project, rendered)
    except Exception:
        # Fall back to L2 label check if manifest approach fails
        try:
            from ..orchestration.image import build_context_hash

            current_hash = build_context_hash(project_id)
        except Exception:
            return None
        label = image_labels(image_id).get("terok.build_context_hash")
        if not label:
            return True
        return label != current_hash

    return len(stale) > 0
