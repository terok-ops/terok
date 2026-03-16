# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Read-only project state inspection and reporting.

Aggregates infrastructure status (dockerfiles, images, SSH, gate) for a
project by querying podman and the filesystem.  Used by both CLI and TUI
for overview displays.
"""

import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.config import build_root, get_envs_base_dir
from ..core.images import project_cli_image
from ..core.projects import load_project


def get_project_state(
    project_id: str,
    gate_commit_provider: Callable[[str], dict | None] | None = None,
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
    """

    project = load_project(project_id)

    # Dockerfiles: look in the same location generate_dockerfiles writes to.
    stage_dir = build_root() / project.id
    dockerfiles = [
        stage_dir / "L0.Dockerfile",
        stage_dir / "L1.cli.Dockerfile",
        stage_dir / "L2.Dockerfile",
    ]
    has_dockerfiles = all(p.is_file() for p in dockerfiles)

    # Images: rely on podman image tags created by build_images().
    has_images = False
    try:
        required_tags = [project_cli_image(project.id)]
        ok = True
        for tag in required_tags:
            # ``podman image exists`` exits with 0 when the image is present.
            result = subprocess.run(
                ["podman", "image", "exists", tag],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if result.returncode != 0:
                ok = False
                break
        has_images = ok
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        has_images = False

    dockerfiles_old = False
    if has_dockerfiles:
        try:
            from .docker import dockerfiles_match_templates
        except ImportError:
            dockerfiles_old = False
        else:
            try:
                dockerfiles_old = not dockerfiles_match_templates(project_id)
            except Exception:
                # Template comparison is best-effort; treat errors as "not old"
                dockerfiles_old = False

    images_old = False
    if has_images and has_dockerfiles:
        if dockerfiles_old:
            images_old = True
        else:
            docker_mtime = None
            try:
                docker_mtime = max(p.stat().st_mtime for p in dockerfiles if p.is_file())
            except Exception:
                docker_mtime = None

            context_hash = None
            try:
                from .docker import build_context_hash

                context_hash = build_context_hash(project_id)
            except Exception:
                context_hash = None

            if docker_mtime is not None or context_hash is not None:
                docker_dt = (
                    datetime.fromtimestamp(docker_mtime, tz=UTC)
                    if docker_mtime is not None
                    else None
                )
                for tag in required_tags:
                    created, label = _get_image_metadata(tag, "terok.build_context_hash")
                    if created is None and label is None:
                        images_old = True
                        break
                    if docker_dt is not None and created is not None and created < docker_dt:
                        images_old = True
                        break
                    if context_hash is not None:
                        if label is None or label != context_hash:
                            images_old = True
                            break

    # SSH: same resolution logic as init_project_ssh(). Consider SSH
    # "ready" when the directory and its config file exist.
    ssh_dir = project.ssh_host_dir or (get_envs_base_dir() / f"_ssh-config-{project.id}")
    ssh_dir = Path(ssh_dir).expanduser().resolve()
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
        except Exception:
            gate_last_commit = None

    return {
        "dockerfiles": has_dockerfiles,
        "dockerfiles_old": dockerfiles_old,
        "images": has_images,
        "images_old": images_old,
        "ssh": has_ssh,
        "gate": has_gate,
        "gate_last_commit": gate_last_commit,
    }


def _get_image_metadata(tag: str, label_key: str) -> tuple[datetime | None, str | None]:
    """Return (created_datetime, label_value) for a podman image *tag*."""
    try:
        result = subprocess.run(
            [
                "podman",
                "image",
                "inspect",
                "--format",
                f'{{{{.Created}}}}\\t{{{{index .Config.Labels "{label_key}"}}}}',
                tag,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None, None
    if result.returncode != 0:
        return None, None
    created_raw, _, label_raw = result.stdout.partition("\t")
    label = label_raw.strip() or None
    if label == "<no value>":
        label = None
    return _parse_podman_created(created_raw), label


def _parse_podman_created(value: str) -> datetime | None:
    """Parse a podman ``Created`` timestamp string into a datetime."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    if "." in value:
        head, tail = value.split(".", 1)
        tz_sep = None
        for sep in ("+", "-"):
            idx = tail.find(sep)
            if idx != -1:
                tz_sep = idx
                break
        if tz_sep is None:
            frac = tail
            tz = ""
        else:
            frac = tail[:tz_sep]
            tz = tail[tz_sep:]
        frac = (frac[:6]).ljust(6, "0")
        value = f"{head}.{frac}{tz}"
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


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

    container_name = f"{project_id}-{task.mode}-{task.task_id}"
    try:
        result = subprocess.run(
            [
                "podman",
                "container",
                "inspect",
                "--format",
                "{{.State.Running}}\t{{.Image}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None

    running_str, _, image_id = result.stdout.partition("\t")
    if running_str.strip().lower() != "true":
        return None
    image_id = image_id.strip()
    if not image_id:
        return None

    try:
        from .docker import build_context_hash

        current_hash = build_context_hash(project_id)
    except Exception:
        return None

    try:
        label_result = subprocess.run(
            [
                "podman",
                "image",
                "inspect",
                "--format",
                '{{index .Config.Labels "terok.build_context_hash"}}',
                image_id,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if label_result.returncode != 0:
        return None

    label = label_result.stdout.strip()
    if not label or label == "<no value>":
        return True
    return label != current_hash
