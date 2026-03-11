# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Authentication workflows for AI coding agents.

Provides a data-driven registry of auth providers (``AUTH_PROVIDERS``) and a
single entry point ``authenticate(project_id, provider)`` that runs the
appropriate flow inside a temporary L2 CLI container.

The shared helper ``_run_auth_container`` handles the common lifecycle:
check podman, load project, ensure host dir, cleanup old container, run.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

from ..core.config import get_envs_base_dir
from ..core.images import project_cli_image
from ..core.projects import load_project
from ..util.fs import ensure_dir_writable
from ..util.podman import _podman_userns_args

_LOCALHOST = "127.0.0.1"

# ---------------------------------------------------------------------------
# Provider descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthProvider:
    """Describes how to authenticate one tool/agent."""

    name: str
    """Short key used in CLI and TUI dispatch (e.g. ``"codex"``)."""

    label: str
    """Human-readable display name (e.g. ``"Codex"``)."""

    host_dir_name: str
    """Directory name under ``get_envs_base_dir()`` (e.g. ``"_codex-config"``)."""

    container_mount: str
    """Mount point inside the container (e.g. ``"/home/dev/.codex"``)."""

    command: list[str]
    """Command to execute inside the container."""

    banner_hint: str
    """Provider-specific help text shown before the container runs."""

    extra_run_args: tuple[str, ...] = field(default_factory=tuple)
    """Additional ``podman run`` arguments (e.g. port forwarding)."""


# ---------------------------------------------------------------------------
# Helper for API-key-style providers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthKeyConfig:
    """Describes how to prompt for and store an API key."""

    label: str
    """Human name shown in the prompt (e.g. ``"Claude"``)."""

    key_url: str
    """URL where the user can obtain the key."""

    env_var: str
    """Name shown in the ``read -p`` prompt (e.g. ``"ANTHROPIC_API_KEY"``)."""

    config_path: str
    """Destination inside the container (e.g. ``"~/.claude/config.json"``)."""

    printf_template: str
    """``printf`` format string (e.g. ``'{\"api_key\": \"%s\"}'``)."""

    tool_name: str
    """Name shown in the success message (e.g. ``"claude"``)."""


def _api_key_command(cfg: AuthKeyConfig) -> list[str]:
    """Build a bash command that prompts for an API key and writes it to a config file."""
    config_dir = cfg.config_path.rsplit("/", 1)[0]
    parts = [
        f"echo 'Enter your {cfg.label} API key (get one at {cfg.key_url}):'",
        f"read -r -p '{cfg.env_var}=' api_key",
        f"mkdir -p {config_dir}",
        f"printf '{cfg.printf_template}\\n' \"$api_key\" > {cfg.config_path}",
        "echo",
        f"echo 'API key saved to {cfg.config_path}'",
        f"echo 'You can now use {cfg.tool_name} in task containers.'",
    ]
    return ["bash", "-c", " && ".join(parts)]


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------

AUTH_PROVIDERS: dict[str, AuthProvider] = {}
"""All known auth providers, keyed by name."""

_ALL_PROVIDERS: list[AuthProvider] = [
    AuthProvider(
        name="codex",
        label="Codex",
        host_dir_name="_codex-config",
        container_mount="/home/dev/.codex",
        command=["setup-codex-auth.sh"],
        banner_hint=(
            "This will set up port forwarding (using socat) and open a browser "
            "for authentication.\n"
            "After completing authentication, press Ctrl+C to stop the container."
        ),
        extra_run_args=("-p", f"{_LOCALHOST}:1455:1455"),
    ),
    AuthProvider(
        name="claude",
        label="Claude",
        host_dir_name="_claude-config",
        container_mount="/home/dev/.claude",
        command=_api_key_command(
            AuthKeyConfig(
                label="Claude",
                key_url="https://console.anthropic.com/settings/keys",
                env_var="ANTHROPIC_API_KEY",
                config_path="~/.claude/config.json",
                printf_template='{"api_key": "%s"}',
                tool_name="claude",
            )
        ),
        banner_hint=(
            "You will be prompted to enter your Claude API key.\n"
            "Get your API key at: https://console.anthropic.com/settings/keys"
        ),
    ),
    AuthProvider(
        name="mistral",
        label="Mistral Vibe",
        host_dir_name="_vibe-config",
        container_mount="/home/dev/.vibe",
        command=_api_key_command(
            AuthKeyConfig(
                label="Mistral",
                key_url="https://console.mistral.ai/api-keys",
                env_var="MISTRAL_API_KEY",
                config_path="~/.vibe/.env",
                printf_template="MISTRAL_API_KEY=%s",
                tool_name="vibe",
            )
        ),
        banner_hint=(
            "You will be prompted to enter your Mistral API key.\n"
            "Get your API key at: https://console.mistral.ai/api-keys"
        ),
    ),
    AuthProvider(
        name="blablador",
        label="Blablador",
        host_dir_name="_blablador-config",
        container_mount="/home/dev/.blablador",
        command=_api_key_command(
            AuthKeyConfig(
                label="Blablador",
                key_url="https://codebase.helmholtz.cloud/-/user_settings/personal_access_tokens",
                env_var="BLABLADOR_API_KEY",
                config_path="~/.blablador/config.json",
                printf_template='{"api_key": "%s"}',
                tool_name="blablador",
            )
        ),
        banner_hint=(
            "You will be prompted to enter your Blablador API key.\n"
            "Get your API key at: "
            "https://codebase.helmholtz.cloud/-/user_settings/personal_access_tokens"
        ),
    ),
    AuthProvider(
        name="gh",
        label="GitHub CLI",
        host_dir_name="_gh-config",
        container_mount="/home/dev/.config/gh",
        command=["gh", "auth", "login"],
        banner_hint=(
            "You will be guided through GitHub authentication.\n"
            "Recommended: choose 'Login with a web browser' or paste a token."
        ),
    ),
    AuthProvider(
        name="glab",
        label="GitLab CLI",
        host_dir_name="_glab-config",
        container_mount="/home/dev/.config/glab-cli",
        command=["glab", "auth", "login"],
        banner_hint=(
            "You will be guided through GitLab authentication.\n"
            "You will need a GitLab personal access token.\n"
            "Create one at: https://gitlab.com/-/user_settings/personal_access_tokens"
        ),
    ),
]

for _p in _ALL_PROVIDERS:
    if _p.name in AUTH_PROVIDERS:
        raise RuntimeError(f"Duplicate auth provider name: {_p.name!r}")
    AUTH_PROVIDERS[_p.name] = _p


# ---------------------------------------------------------------------------
# Shared container lifecycle
# ---------------------------------------------------------------------------


def _check_podman() -> None:
    """Verify podman is available."""
    if shutil.which("podman") is None:
        raise SystemExit("podman not found; please install podman")


def _cleanup_existing_container(container_name: str) -> None:
    """Remove an existing container if it exists."""
    result = subprocess.run(
        ["podman", "container", "exists", container_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        print(f"Removing existing auth container: {container_name}")
        subprocess.run(
            ["podman", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _run_auth_container(project_id: str, provider: AuthProvider) -> None:
    """Run an auth container for the given provider."""
    _check_podman()

    project = load_project(project_id)

    envs_base = get_envs_base_dir()
    host_dir = envs_base / provider.host_dir_name
    ensure_dir_writable(host_dir, f"{provider.label} config")

    container_name = f"{project.id}-auth-{provider.name}"
    _cleanup_existing_container(container_name)

    cmd = [
        "podman",
        "run",
        "--rm",
        "-it",
        "-v",
        f"{host_dir}:{provider.container_mount}:Z",
        "--name",
        container_name,
    ]
    userns = _podman_userns_args()
    cmd[3:3] = userns
    if provider.extra_run_args:
        cmd[3 + len(userns) : 3 + len(userns)] = list(provider.extra_run_args)
    cmd.append(project_cli_image(project.id))
    cmd.extend(provider.command)

    # Banner
    print(f"Authenticating {provider.label} for project: {project_id}")
    print()
    for line in provider.banner_hint.splitlines():
        print(line)
    print()
    print("$", " ".join(map(str, cmd)))
    print()

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        if e.returncode == 130:
            print("\nAuthentication container stopped.")
        else:
            raise SystemExit(f"Auth failed: {e}")
    except KeyboardInterrupt:
        print("\nAuthentication interrupted.")
        subprocess.run(
            ["podman", "rm", "-f", container_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )


def authenticate(project_id: str, provider: str) -> None:
    """Run the auth flow for *provider* against *project_id*.

    Raises ``SystemExit`` if the provider name is unknown.
    """
    info = AUTH_PROVIDERS.get(provider)
    if not info:
        available = ", ".join(AUTH_PROVIDERS)
        raise SystemExit(f"Unknown auth provider: {provider}. Available: {available}")
    _run_auth_container(project_id, info)
