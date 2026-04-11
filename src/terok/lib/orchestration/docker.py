# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Dockerfile generation, image building, and build-context hashing.

L0 (base dev) and L1 (agent CLI) image builds are delegated to
``terok_agent.container.build``.  This module owns L2 (project customisation)
rendering and the project-level build orchestration that ties all
three layers together.
"""

import hashlib
import shlex
import shutil
import subprocess
from functools import lru_cache
from importlib import resources
from pathlib import Path

from terok_agent import (
    BuildError,
    build_base_images,
    l0_image_tag,
    stage_scripts,
    stage_tmux_config,
    stage_toad_agents,
)

from ..core.config import build_dir
from ..core.images import project_cli_image, project_dev_image
from ..core.project_model import ProjectConfig
from ..core.projects import load_project
from ..util.fs import ensure_dir

# ---------- helpers ----------


def _check_podman_available() -> None:
    """Raise SystemExit if podman is not on PATH."""
    if shutil.which("podman") is None:
        raise SystemExit("podman not found; please install podman")


def _image_exists(image: str) -> bool:
    """Check if a container image exists locally."""
    result = subprocess.run(
        ["podman", "image", "exists", image],
        capture_output=True,
    )
    return result.returncode == 0


# ---------- Hashing ----------


def _hash_traversable_tree(root) -> str:
    """Compute a SHA-256 digest over all files in a Traversable tree."""
    hasher = hashlib.sha256()

    def _walk(node, prefix: str) -> None:
        """Walk a Traversable tree and feed file contents into the hasher."""
        for child in sorted(node.iterdir(), key=lambda item: item.name):
            rel = f"{prefix}{child.name}"
            if child.is_dir():
                _walk(child, f"{rel}/")
            else:
                hasher.update(rel.encode("utf-8"))
                hasher.update(b"\0")
                hasher.update(child.read_bytes())
                hasher.update(b"\0")

    _walk(root, "")
    return hasher.hexdigest()


@lru_cache(maxsize=1)
def _scripts_hash() -> str:
    """Return a cached SHA-256 hash of the bundled helper scripts."""
    scripts_root = resources.files("terok_agent") / "resources" / "scripts"
    return _hash_traversable_tree(scripts_root)


@lru_cache(maxsize=1)
def _tmux_config_hash() -> str:
    """Return a cached SHA-256 hash of the bundled tmux configuration."""
    tmux_root = resources.files("terok_agent") / "resources" / "tmux"
    return _hash_traversable_tree(tmux_root)


# ---------- L2 (project) Dockerfile ----------


def _resolve_user_snippet(project: ProjectConfig) -> str:
    """Resolve the docker user snippet from project config (file and/or inline).

    When both ``docker.user_snippet_file`` and ``docker.user_snippet_inline``
    are set, the file is included first and the inline block is appended.

    Raises :class:`SystemExit` if ``docker.user_snippet_file`` is configured but
    the file does not exist or cannot be read.
    """
    parts: list[str] = []
    if project.docker_snippet_file:
        us_path = Path(project.docker_snippet_file).expanduser()
        if not us_path.is_absolute():
            us_path = project.root / us_path
        if not us_path.is_file():
            raise SystemExit(
                f"docker.user_snippet_file not found: {us_path}\n"
                f"  (configured in project '{project.id}')"
            )
        try:
            parts.append(us_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise SystemExit(f"Failed to read docker.user_snippet_file {us_path}: {exc}")
    if project.docker_snippet_inline and project.docker_snippet_inline.strip():
        parts.append(project.docker_snippet_inline)
    return "\n".join(parts)


def _render_l2(project: ProjectConfig) -> str:
    """Render the L2 (project customisation) Dockerfile.

    L2 contains the user docker snippet wrapped in USER root/dev.
    Runtime env vars (CODE_REPO, GIT_BRANCH) are set by environment.py
    at container launch time.
    """
    tmpl_pkg = resources.files("terok") / "resources" / "templates"
    template = (tmpl_pkg / "l2.project.Dockerfile.template").read_text()

    variables = {
        "PROJECT_ID": project.id,
        "SECURITY_CLASS": project.security_class,
        "UPSTREAM_URL": project.upstream_url or "",
        "DEFAULT_BRANCH": project.default_branch or "",
        "BASE_IMAGE": project.docker_base_image,
        "CODE_REPO_DEFAULT": (
            "file:///git-gate/gate.git"
            if project.security_class == "gatekeeping"
            else (project.upstream_url or "")
        ),
        "USER_SNIPPET": _resolve_user_snippet(project),
    }

    for k, v in variables.items():
        template = template.replace(f"{{{{{k}}}}}", str(v))
    return template


def _render_all_dockerfiles(project: ProjectConfig) -> dict[str, str]:
    """Render all Dockerfile templates for *project*.

    L0 and L1 are rendered by terok-agent; L2 is rendered locally.
    Returns name→content mapping for the build context.
    """
    from terok_agent.container.build import render_l0, render_l1

    return {
        "L0.Dockerfile": render_l0(project.docker_base_image),
        "L1.cli.Dockerfile": render_l1(l0_image_tag(project.docker_base_image)),
        "L2.Dockerfile": _render_l2(project),
    }


# ---------- Build context hash ----------


def build_context_hash(project_id: str) -> str:
    """Compute a SHA-256 digest of the full build context for *project_id*."""
    project = load_project(project_id)
    rendered = _render_all_dockerfiles(project)

    hasher = hashlib.sha256()
    hasher.update(f"base_image={project.docker_base_image}".encode())
    hasher.update(b"\0")
    for name in sorted(rendered):
        hasher.update(name.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(rendered[name].encode("utf-8"))
        hasher.update(b"\0")
    hasher.update(_scripts_hash().encode("utf-8"))
    hasher.update(b"\0")
    hasher.update(_tmux_config_hash().encode("utf-8"))
    return hasher.hexdigest()


def dockerfiles_match_templates(project_id: str) -> bool:
    """Return True if generated Dockerfiles match current templates."""
    project = load_project(project_id)
    out_dir = build_dir() / project.id
    rendered = _render_all_dockerfiles(project)
    for name, expected in rendered.items():
        path = out_dir / name
        if not path.is_file():
            return False
        if path.read_text() != expected:
            return False
    return True


# ---------- Dockerfile generation ----------


def generate_dockerfiles(project_id: str) -> None:
    """Render and write Dockerfiles and auxiliary scripts for *project_id*."""
    project = load_project(project_id)
    out_dir = build_dir() / project.id
    ensure_dir(out_dir)

    rendered = _render_all_dockerfiles(project)
    for name, content in rendered.items():
        (out_dir / name).write_text(content)

    # Stage auxiliary resources from terok-agent into build context.
    try:
        stage_scripts(out_dir / "scripts")
    except OSError as e:
        print(f"Warning: could not stage build scripts: {e}")

    try:
        stage_toad_agents(out_dir / "toad-agents")
    except OSError as e:
        print(f"Warning: could not stage toad agent definitions: {e}")

    try:
        stage_tmux_config(out_dir / "tmux")
    except OSError as e:
        print(f"Warning: could not stage tmux config: {e}")

    print(f"Generated Dockerfiles in {out_dir}")


# ---------- Image building ----------


def build_images(
    project_id: str,
    include_dev: bool = False,
    rebuild_agents: bool = False,
    full_rebuild: bool = False,
) -> None:
    """Build container images for a project.

    L0+L1 builds are delegated to ``terok_agent.build_base_images()``.
    This function handles L2 (project customisation) and ties the layers
    together.

    Args:
        project_id: The project to build images for.
        include_dev: Also build a dev image from L0 (tagged as <project>:l2-dev).
        rebuild_agents: Rebuild L0+L1 with fresh agents (cache bust).
        full_rebuild: Rebuild everything with ``--no-cache --pull=always``.
    """
    _check_podman_available()

    project = load_project(project_id)
    base_image = project.docker_base_image
    stage_dir = build_dir() / project.id

    # Delegate L0+L1 to terok-agent (uses its own temp dir for build context)
    try:
        base_images = build_base_images(
            base_image,
            rebuild=rebuild_agents,
            full_rebuild=full_rebuild,
        )
    except BuildError as e:
        raise SystemExit(str(e)) from e

    l1_cli_image = base_images.l1
    l0_image = base_images.l0
    l2_cli_image = project_cli_image(project.id)
    l2_dev_image = project_dev_image(project.id)

    # Generate L2 build context (Dockerfile + staged resources)
    generate_dockerfiles(project_id)
    l2_path = stage_dir / "L2.Dockerfile"

    context_hash = build_context_hash(project_id)
    context_dir = str(stage_dir)

    def _build_l2(base_arg: str, target: str) -> None:
        """Build one L2 image variant."""
        cmd = ["podman", "build", "-f", str(l2_path)]
        cmd += ["--build-arg", f"BASE_IMAGE={base_arg}"]
        cmd += ["--label", f"terok.build_context_hash={context_hash}"]
        cmd += ["-t", target]
        if full_rebuild:
            cmd.append("--no-cache")
        cmd.append(context_dir)
        print("$", shlex.join(cmd))
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            raise SystemExit("podman not found; please install podman")
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"Build failed: {e}")

    # Always build L2 CLI image (project layer on top of L1)
    _build_l2(l1_cli_image, l2_cli_image)

    # Optionally build L2 dev image (project layer on top of L0)
    if include_dev:
        _build_l2(l0_image, l2_dev_image)
