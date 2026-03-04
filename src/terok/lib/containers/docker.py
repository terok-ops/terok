# SPDX-FileCopyrightText: 2025-2026 Jiri Vyskocil <jiri@vyskocil.com>
#
# SPDX-License-Identifier: Apache-2.0

"""Dockerfile generation, image building, and build-context hashing."""

import hashlib
import shutil
import subprocess
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml  # pip install pyyaml

from ..core.config import build_root, is_experimental
from ..core.images import (
    agent_cli_image,
    agent_ui_image,
    base_dev_image,
    project_cli_image,
    project_dev_image,
    project_web_image,
)
from ..core.projects import effective_ssh_key_name, load_project
from ..util.fs import ensure_dir

# ---------- helpers ----------


def _check_podman_available() -> None:
    """Raise SystemExit if podman is not on PATH."""
    if shutil.which("podman") is None:
        raise SystemExit("podman not found; please install podman")


def _image_exists(image: str) -> bool:
    """Check if a container image exists locally.

    Assumes podman is available (call ``_check_podman_available`` first).
    """
    result = subprocess.run(
        ["podman", "image", "exists", image],
        capture_output=True,
    )
    return result.returncode == 0


# ---------- Dockerfile gen & build ----------


def _copy_package_tree(package: str, rel_path: str, dest: Path) -> None:
    """Copy a directory tree from package resources to a filesystem path.

    Uses importlib.resources Traversable API so it works from wheels/zip installs.
    """
    root = resources.files(package) / rel_path

    def _recurse(src, dst: Path) -> None:
        """Recursively copy a Traversable tree to a filesystem directory."""
        dst.mkdir(parents=True, exist_ok=True)
        for child in src.iterdir():
            out = dst / child.name
            if child.is_dir():
                _recurse(child, out)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(child.read_bytes())

    _recurse(root, dest)


def _stage_scripts_into(dest: Path) -> None:
    """Stage helper scripts from package resources into dest/scripts.

    Single source of truth: terok/resources/scripts bundled in the wheel.
    """
    pkg_rel = "resources/scripts"
    # Replace destination directory atomically-ish
    if dest.exists():
        shutil.rmtree(dest)
    _copy_package_tree("terok", pkg_rel, dest)


def _stage_tmux_config_into(dest: Path) -> None:
    """Stage tmux config from package resources into dest/tmux.

    Single source of truth: terok/resources/tmux bundled in the wheel.
    """
    pkg_rel = "resources/tmux"
    if dest.exists():
        shutil.rmtree(dest)
    _copy_package_tree("terok", pkg_rel, dest)


def _load_docker_config(project_root: Path) -> dict:
    """Load the ``docker:`` section from *project_root*/project.yml."""
    try:
        cfg = yaml.safe_load((project_root / "project.yml").read_text()) or {}
        return cfg.get("docker", {}) or {}
    except (OSError, yaml.YAMLError):
        return {}


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
    scripts_root = resources.files("terok") / "resources" / "scripts"
    return _hash_traversable_tree(scripts_root)


@lru_cache(maxsize=1)
def _tmux_config_hash() -> str:
    """Return a cached SHA-256 hash of the bundled tmux configuration."""
    tmux_root = resources.files("terok") / "resources" / "tmux"
    return _hash_traversable_tree(tmux_root)


def _render_dockerfiles(project) -> dict[str, str]:
    """Render all Dockerfile templates for *project* and return name→content mapping."""
    # Load templates from package resources (terok/resources/templates). Use
    # importlib.resources Traversable API so it works from wheels/zip too.
    tmpl_pkg = resources.files("terok") / "resources" / "templates"
    templates = {
        "L0.Dockerfile": (tmpl_pkg / "l0.dev.Dockerfile.template").read_text(),
        "L1.cli.Dockerfile": (tmpl_pkg / "l1.agent-cli.Dockerfile.template").read_text(),
        "L2.Dockerfile": (tmpl_pkg / "l2.project.Dockerfile.template").read_text(),
    }
    if is_experimental():
        templates["L1.ui.Dockerfile"] = (tmpl_pkg / "l1.agent-ui.Dockerfile.template").read_text()

    # Read additional docker-related settings directly from the project.yml
    docker_cfg = _load_docker_config(project.root)

    # Resolve optional user snippet: prefer inline over file
    user_snippet = ""
    us_inline = docker_cfg.get("user_snippet_inline")
    if isinstance(us_inline, str) and us_inline.strip():
        user_snippet = us_inline
    else:
        us_file = docker_cfg.get("user_snippet_file")
        if isinstance(us_file, str) and us_file:
            us_path = Path(us_file)
            if not us_path.is_absolute():
                us_path = project.root / us_file
            try:
                if us_path.is_file():
                    user_snippet = us_path.read_text()
            except OSError:
                user_snippet = ""

    # SSH_KEY_NAME inside containers should mirror the filename that ssh-init
    # generated (or will generate) for this project. We assume the default
    # key_type (ed25519) here, which matches init_project_ssh's default.
    ssh_key_name = effective_ssh_key_name(project, key_type="ed25519")

    variables = {
        "PROJECT_ID": project.id,
        "SECURITY_CLASS": project.security_class,
        "UPSTREAM_URL": project.upstream_url or "",
        "DEFAULT_BRANCH": project.default_branch,
        # Template-specific extras
        "BASE_IMAGE": str(docker_cfg.get("base_image", "ubuntu:24.04")),
        "SSH_KEY_NAME": ssh_key_name,
        # For gatekeeping projects, default CODE_REPO to the git-gate mount path.
        # For online projects, default to the real upstream URL.
        # These defaults can be overridden at runtime via -e flags.
        "CODE_REPO_DEFAULT": (
            "file:///git-gate/gate.git"
            if project.security_class == "gatekeeping"
            else (project.upstream_url or "")
        ),
        "USER_SNIPPET": user_snippet,
    }

    rendered = {}
    for name, content in templates.items():
        for k, v in variables.items():
            content = content.replace(f"{{{{{k}}}}}", str(v))
        rendered[name] = content
    return rendered


def build_context_hash(project_id: str) -> str:
    """Compute a SHA-256 digest of the full build context for *project_id*."""
    project = load_project(project_id)
    rendered = _render_dockerfiles(project)
    docker_cfg = _load_docker_config(project.root)
    base_image = str(docker_cfg.get("base_image", "ubuntu:24.04"))

    hasher = hashlib.sha256()
    hasher.update(f"base_image={base_image}".encode())
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
    out_dir = build_root() / project.id
    rendered = _render_dockerfiles(project)
    for name, expected in rendered.items():
        path = out_dir / name
        if not path.is_file():
            return False
        if path.read_text() != expected:
            return False
    return True


def generate_dockerfiles(project_id: str) -> None:
    """Render and write Dockerfiles and auxiliary scripts for *project_id*."""
    project = load_project(project_id)
    out_dir = build_root() / project.id
    ensure_dir(out_dir)

    rendered = _render_dockerfiles(project)
    for name, content in rendered.items():
        (out_dir / name).write_text(content)

    # Stage auxiliary scripts into build context so Dockerfile COPY works.
    try:
        _stage_scripts_into(out_dir / "scripts")
    except OSError as e:
        print(f"Warning: could not stage build scripts: {e}")

    # Stage tmux config for container login sessions.
    try:
        _stage_tmux_config_into(out_dir / "tmux")
    except OSError as e:
        print(f"Warning: could not stage tmux config: {e}")

    print(f"Generated Dockerfiles in {out_dir}")


def build_images(
    project_id: str,
    include_dev: bool = False,
    rebuild_agents: bool = False,
    full_rebuild: bool = False,
) -> None:
    """Build container images for a project.

    Args:
        project_id: The project to build images for
        include_dev: Also build a dev image from L0 (tagged as <project>:l2-dev)
        rebuild_agents: Rebuild L0+L1+L2 with fresh agent installs (cache bust)
        full_rebuild: Full rebuild with --no-cache and --pull=always
    """
    import time

    _check_podman_available()

    project = load_project(project_id)
    docker_cfg = _load_docker_config(project.root)
    stage_dir = build_root() / project.id
    context_hash = build_context_hash(project_id)

    l0 = stage_dir / "L0.Dockerfile"
    l1_cli = stage_dir / "L1.cli.Dockerfile"
    l1_ui = stage_dir / "L1.ui.Dockerfile"
    l2 = stage_dir / "L2.Dockerfile"

    required = [l0, l1_cli, l2]
    if is_experimental():
        required.append(l1_ui)
    if not all(f.is_file() for f in required):
        raise SystemExit("Dockerfiles are missing. Run 'terokctl generate <project>' first.")

    context_dir = str(stage_dir)

    base_image = str(docker_cfg.get("base_image", "ubuntu:24.04"))
    l0_image = base_dev_image(base_image)
    l1_cli_image = agent_cli_image(base_image)
    l1_ui_image = agent_ui_image(base_image)
    l2_cli_image = project_cli_image(project.id)
    l2_ui_image = project_web_image(project.id)
    l2_dev_image = project_dev_image(project.id)

    # Cache bust timestamp for agent installs
    cache_bust = str(int(time.time()))

    def _build_cmd(
        dockerfile: Path,
        base_image_arg: str,
        target_image: str,
        *,
        build_args: dict[str, str] | None = None,
        labels: dict[str, str] | None = None,
        pull: bool = False,
    ) -> list[str]:
        """Assemble the podman build command list for a single image stage."""
        cmd = ["podman", "build", "-f", str(dockerfile)]
        cmd += ["--build-arg", f"BASE_IMAGE={base_image_arg}"]
        for k, v in (build_args or {}).items():
            cmd += ["--build-arg", f"{k}={v}"]
        for k, v in (labels or {}).items():
            cmd += ["--label", f"{k}={v}"]
        cmd += ["-t", target_image]
        if full_rebuild:
            cmd.append("--no-cache")
        if pull:
            cmd.append("--pull=always")
        cmd.append(context_dir)
        return cmd

    cmds = []

    # Auto-detect missing base layers and build them if needed
    need_base_layers = rebuild_agents or full_rebuild
    if not need_base_layers:
        if not _image_exists(l0_image):
            print(f"L0 image {l0_image} not found locally, will build all layers (L0+L1+L2).")
            need_base_layers = True
        elif not _image_exists(l1_cli_image) or (
            is_experimental() and not _image_exists(l1_ui_image)
        ):
            print("L1 image(s) not found locally, will build all layers (L0+L1+L2).")
            need_base_layers = True

    # Build L0 and L1 layers when needed
    if need_base_layers:
        cmds.append(_build_cmd(l0, base_image, l0_image, pull=full_rebuild))
        cmds.append(
            _build_cmd(
                l1_cli,
                l0_image,
                l1_cli_image,
                build_args={"AGENT_CACHE_BUST": cache_bust},
            )
        )
        if is_experimental():
            cmds.append(_build_cmd(l1_ui, l0_image, l1_ui_image))

    # Always build L2 project images
    hash_label = {"terok.build_context_hash": context_hash}
    cmds.append(_build_cmd(l2, l1_cli_image, l2_cli_image, labels=hash_label))
    if is_experimental():
        cmds.append(_build_cmd(l2, l1_ui_image, l2_ui_image, labels=hash_label))

    if include_dev:
        cmds.append(_build_cmd(l2, l0_image, l2_dev_image, labels=hash_label))

    for cmd in cmds:
        print("$", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True)
        except FileNotFoundError:
            raise SystemExit("podman not found; please install podman")
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"Build failed: {e}")
