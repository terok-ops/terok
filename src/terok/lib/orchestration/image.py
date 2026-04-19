# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Dockerfile generation, image building, and build-context hashing.

L0 (base dev) and L1 (agent CLI) image builds are delegated to
``terok_executor.container.build``.  This module owns L2 (project customisation)
rendering and the project-level build orchestration that ties all
three layers together.
"""

import hashlib
import json
import logging
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any

from terok_executor import (
    BuildError,
    build_base_images,
    build_project_image,
    detect_family,
    l0_image_tag,
    parse_agent_selection,
    stage_scripts,
    stage_tmux_config,
    stage_toad_agents,
)
from terok_sandbox import image_exists as _sandbox_image_exists

from ..core.config import build_dir
from ..core.images import project_cli_image, project_dev_image
from ..core.project_model import ProjectConfig
from ..core.projects import load_project
from ..util.fs import ensure_dir

# ---------- helpers ----------


def _image_exists(image: str) -> bool:
    """Check if a container image exists locally.

    Thin wrapper over :func:`terok_sandbox.image_exists` kept as a
    same-module symbol so existing test mocks (``patch("terok.lib.
    orchestration.image._image_exists")``) keep working.
    """
    return _sandbox_image_exists(image)


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
    scripts_root = resources.files("terok_executor") / "resources" / "scripts"
    return _hash_traversable_tree(scripts_root)


@lru_cache(maxsize=1)
def _tmux_config_hash() -> str:
    """Return a cached SHA-256 hash of the bundled tmux configuration."""
    tmux_root = resources.files("terok_executor") / "resources" / "tmux"
    return _hash_traversable_tree(tmux_root)


# ---------- L2 (project) Dockerfile ----------


def _resolve_user_snippet(project: ProjectConfig) -> str:
    """Resolve the user snippet from project config (file and/or inline).

    When both ``image.user_snippet_file`` and ``image.user_snippet_inline``
    are set, the file is included first and the inline block is appended.

    Raises :class:`SystemExit` if ``image.user_snippet_file`` is configured but
    the file does not exist or cannot be read.
    """
    parts: list[str] = []
    if project.snippet_file:
        us_path = Path(project.snippet_file).expanduser()
        if not us_path.is_absolute():
            us_path = project.root / us_path
        if not us_path.is_file():
            raise SystemExit(
                f"image.user_snippet_file not found: {us_path}\n"
                f"  (configured in project '{project.id}')"
            )
        try:
            parts.append(us_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError) as exc:
            raise SystemExit(f"Failed to read image.user_snippet_file {us_path}: {exc}")
    if project.snippet_inline and project.snippet_inline.strip():
        parts.append(project.snippet_inline)
    return "\n".join(parts)


def _render_l2(project: ProjectConfig) -> str:
    """Render the L2 (project customisation) Dockerfile.

    L2 contains the user image snippet wrapped in USER root/dev.
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
        "BASE_IMAGE": project.base_image,
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


def render_all_dockerfiles(project: ProjectConfig, *, family: str | None = None) -> dict[str, str]:
    """Render all Dockerfile templates for *project*.

    L0 and L1 are rendered by terok-executor; L2 is rendered locally.
    Returns name→content mapping for the build context.

    Pass *family* to reuse a value already resolved by the caller; when
    omitted it is detected from ``project.base_image`` (with
    ``project.family`` as override).
    """
    from terok_executor.container.build import render_l0, render_l1

    fam = family or detect_family(project.base_image, override=project.family)
    return {
        "L0.Dockerfile": render_l0(project.base_image, family=fam),
        "L1.cli.Dockerfile": render_l1(l0_image_tag(project.base_image), family=fam),
        "L2.Dockerfile": _render_l2(project),
    }


# ---------- Build context hash ----------


def _sha256(*parts: str) -> str:
    """Compute SHA-256 from a sequence of string parts, null-separated."""
    hasher = hashlib.sha256()
    for i, part in enumerate(parts):
        if i:
            hasher.update(b"\0")
        hasher.update(part.encode("utf-8"))
    return hasher.hexdigest()


def l0_content_hash(base_image: str, rendered: dict[str, str]) -> str:
    """Content hash for the L0 (base dev) layer."""
    return _sha256(f"base_image={base_image}", rendered["L0.Dockerfile"])


def l1_content_hash(rendered: dict[str, str]) -> str:
    """Content hash for the L1 (agent CLI) layer."""
    return _sha256(rendered["L1.cli.Dockerfile"], _scripts_hash(), _tmux_config_hash())


def l2_content_hash(rendered: dict[str, str]) -> str:
    """Content hash for the L2 (project customisation) layer."""
    return _sha256(rendered["L2.Dockerfile"])


def build_context_hash_from_rendered(project: ProjectConfig, rendered: dict[str, str]) -> str:
    """Compute a combined SHA-256 digest from pre-rendered Dockerfiles.

    The combined hash is derived from the three per-layer hashes.  It is
    stored on L2 images as the ``terok.build_context_hash`` label.

    Args:
        project: The resolved project configuration.
        rendered: name→content mapping returned by :func:`render_all_dockerfiles`.
    """
    return _sha256(
        l0_content_hash(project.base_image, rendered),
        l1_content_hash(rendered),
        l2_content_hash(rendered),
    )


def build_context_hash(project_id: str) -> str:
    """Compute a SHA-256 digest of the full build context for *project_id*."""
    project = load_project(project_id)
    rendered = render_all_dockerfiles(project)
    return build_context_hash_from_rendered(project, rendered)


# ---------- Build manifest ----------

_MANIFEST_SCHEMA = 1
_logger = logging.getLogger(__name__)


def _manifest_path(project_id: str) -> Path:
    """Return the path to the build manifest for *project_id*."""
    return build_dir() / project_id / "build_manifest.json"


def read_build_manifest(project_id: str) -> dict[str, Any] | None:
    """Load the build manifest for *project_id*, or ``None`` if absent/corrupt."""
    path = _manifest_path(project_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("schema") == _MANIFEST_SCHEMA else None


def _write_build_manifest(project_id: str, manifest: dict[str, Any]) -> None:
    """Atomically write the build manifest for *project_id*."""
    path = _manifest_path(project_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------- Dockerfile generation ----------


def generate_dockerfiles(project_id: str, *, family: str | None = None) -> None:
    """Render and write Dockerfiles and auxiliary scripts for *project_id*.

    Pass *family* to skip a redundant :func:`detect_family` call when the
    caller has already resolved it (typically from inside :func:`build_images`).
    """
    project = load_project(project_id)
    out_dir = build_dir() / project.id
    ensure_dir(out_dir)

    rendered = render_all_dockerfiles(project, family=family)
    for name, content in rendered.items():
        (out_dir / name).write_text(content)

    # Stage auxiliary resources from terok-executor into build context.
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
    refresh_agents: bool = False,
    full_rebuild: bool = False,
    agents: str | None = None,
) -> None:
    """Build container images for a project.

    L0+L1 builds are delegated to ``terok_executor.build_base_images()``.
    This function handles L2 (project customisation) and ties the layers
    together.

    Args:
        project_id: The project to build images for.
        include_dev: Also build a dev image from L0 (tagged as <project>:l2-dev).
        refresh_agents: Rebuild L0+L1 with fresh agents (cache bust).
        full_rebuild: Rebuild everything with ``--no-cache --pull=always``.
        agents: One-shot override for the agent selection (comma-list or
            ``"all"``).  When ``None``, ``project.agents`` is used (which
            itself inherits from the global ``image.agents`` config).
    """
    project = load_project(project_id)
    base_image = project.base_image
    stage_dir = build_dir() / project.id
    rebuilt_base = refresh_agents or full_rebuild

    agents_arg = parse_agent_selection(agents if agents is not None else project.agents)

    # Delegate L0+L1 to terok-executor (uses its own temp dir for build context)
    try:
        base_images = build_base_images(
            base_image,
            family=project.family,
            agents=agents_arg,
            rebuild=refresh_agents,
            full_rebuild=full_rebuild,
        )
    except BuildError as e:
        raise SystemExit(str(e)) from e

    # If we got here without an override, the build either reused a cached
    # image (no detection needed) or completed a full build (detection
    # already happened inside the executor).  Either way it's now safe to
    # resolve once and thread the result through subsequent renders.
    fam = detect_family(base_image, override=project.family)

    l1_cli_image = base_images.l1
    l0_image = base_images.l0
    l2_cli_image = project_cli_image(project.id)
    l2_dev_image = project_dev_image(project.id)

    # Generate L2 build context (Dockerfile + staged resources)
    generate_dockerfiles(project_id, family=fam)
    l2_path = stage_dir / "L2.Dockerfile"

    rendered = render_all_dockerfiles(project, family=fam)
    l0_hash = l0_content_hash(base_image, rendered)
    l1_hash = l1_content_hash(rendered)
    l2_hash = l2_content_hash(rendered)
    context_hash = _sha256(l0_hash, l1_hash, l2_hash)

    # Resolve manifest L0/L1 hashes: use current hashes if rebuilt,
    # carry forward from previous manifest if skipped.
    if rebuilt_base:
        manifest_l0_hash, manifest_l1_hash = l0_hash, l1_hash
    else:
        prev = read_build_manifest(project_id)
        manifest_l0_hash = prev["l0"]["content_hash"] if prev else l0_hash
        manifest_l1_hash = prev["l1"]["content_hash"] if prev else l1_hash

    def _build_l2(base_arg: str, target: str) -> None:
        """Build one L2 image variant — thin delegate to the executor's factory."""
        try:
            build_project_image(
                dockerfile=l2_path,
                context_dir=stage_dir,
                target_tag=target,
                build_args={"BASE_IMAGE": base_arg},
                labels={"terok.build_context_hash": context_hash},
                no_cache=full_rebuild,
            )
        except BuildError as exc:
            raise SystemExit(str(exc)) from exc

    # Always build L2 CLI image (project layer on top of L1)
    _build_l2(l1_cli_image, l2_cli_image)

    # Optionally build L2 dev image (project layer on top of L0)
    if include_dev:
        _build_l2(l0_image, l2_dev_image)

    # Write build manifest so staleness detection knows what each
    # layer was actually built from.
    _write_build_manifest(
        project_id,
        {
            "schema": _MANIFEST_SCHEMA,
            "base_image": base_image,
            "l0": {"tag": l0_image, "content_hash": manifest_l0_hash},
            "l1": {"tag": l1_cli_image, "content_hash": manifest_l1_hash},
            "l2_cli": {"tag": l2_cli_image, "content_hash": l2_hash},
            "combined_hash": context_hash,
        },
    )
