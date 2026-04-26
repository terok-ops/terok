# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Platform-aware path resolution for config, state, and runtime directories."""

import getpass
import os
import socket
import warnings
from pathlib import Path

try:
    from platformdirs import (
        user_config_dir as _user_config_dir,
        user_data_dir as _user_data_dir,
    )
except ImportError:  # optional dependency
    _user_config_dir = _user_data_dir = None  # type: ignore[assignment]


APP_NAME = "terok"
_VAULT_SUBDIR = "vault"
_ACP_RUNTIME_SUBDIR = "acp"


def _is_root() -> bool:
    """Return True if the current process is running as root."""
    try:
        return os.geteuid() == 0  # type: ignore[attr-defined]
    except AttributeError:
        return getpass.getuser() == "root"


def config_root() -> Path:
    """
    Base directory for configuration (project.yml, projects/, etc.).

    Priority:
      1. TEROK_CONFIG_DIR
      2. if root   → /etc/terok
         else      → ~/.config/terok
    """
    env = os.getenv("TEROK_CONFIG_DIR")
    if env:
        return Path(env).expanduser()

    if _is_root():
        return Path("/etc") / APP_NAME

    if _user_config_dir is not None:
        return Path(_user_config_dir(APP_NAME))
    return Path.home() / ".config" / APP_NAME


def state_root() -> Path:
    """Namespace state root shared by every terok ecosystem package.

    Single source of truth — delegates to :func:`terok_sandbox.paths.namespace_state_dir`,
    which resolves ``TEROK_ROOT`` → ``config.yml`` ``paths.root`` (via the
    layered config stack) → platform default (``/var/lib/terok`` or
    ``${XDG_DATA_HOME:-~/.local/share}/terok``).
    """
    from terok_sandbox.paths import namespace_state_dir

    return namespace_state_dir("").resolve()


def core_state_dir() -> Path:
    """Terok core state (build artifacts, task metadata, panic lock, log).

    Resolves to ``$state_root/core`` unless ``TEROK_STATE_DIR`` overrides it
    (per-package escape hatch, same convention as ``TEROK_SANDBOX_STATE_DIR``
    etc.).
    """
    env = os.getenv("TEROK_STATE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (state_root() / "core").resolve()


def runtime_root() -> Path:
    """
    Transient runtime bits.

    Priority:
      1. TEROK_RUNTIME_DIR
      2. if root   → /run/terok
         else      → ~/.cache/terok
    """
    env = os.getenv("TEROK_RUNTIME_DIR")
    if env:
        return Path(env).expanduser()

    if _is_root():
        return Path("/run") / APP_NAME

    return Path.home() / ".cache" / APP_NAME


def runtime_dir() -> Path:
    """Per-user runtime directory for ephemeral IPC artefacts.

    For short-lived sockets / pid files / FIFOs — things we'd put in
    ``$XDG_RUNTIME_DIR`` when it's available.

    Delegates to :func:`terok_sandbox.paths.namespace_runtime_dir`,
    which resolves ``$XDG_RUNTIME_DIR/terok`` → ``$XDG_STATE_HOME/terok``
    → ``~/.local/state/terok``.  The chain deliberately avoids ``/tmp``
    so we don't land on a predictable-temp-path footprint (bandit B108).

    Distinct from :func:`runtime_root`: ``runtime_root`` is terok's own
    ``~/.cache/terok`` convention (used for non-namespace-scoped
    transient state), while this function sits under the shared terok
    namespace root for ecosystem packages to co-locate.
    """
    from terok_sandbox.paths import namespace_runtime_dir

    return namespace_runtime_dir()


def _acp_runtime_path(project_id: str, task_id: str, *, suffix: str) -> Path:
    """Per-task ACP runtime artefact path with the given file suffix.

    All ACP daemon files (socket, bound-agent sidecar, future log /
    pid files) live under ``runtime_dir() / "acp" / <project>`` with
    the same ``<task_id><suffix>`` shape, so they move together.
    """
    return runtime_dir() / _ACP_RUNTIME_SUBDIR / project_id / f"{task_id}{suffix}"


def acp_socket_path(project_id: str, task_id: str) -> Path:
    """Return the per-task ACP listener socket path on the host.

    The proxy daemon binds this Unix socket on first
    ``terok acp connect`` and tears it down when the task's container
    exits.  Path layout matches the askpass-service convention
    (``runtime_dir() / <subdir> / …``) so all transient sockets live
    under a single XDG-compliant root.
    """
    return _acp_runtime_path(project_id, task_id, suffix=".sock")


def acp_bound_path(project_id: str, task_id: str) -> Path:
    """Return the per-task ACP "bound agent" sidecar JSON path.

    Written atomically by the proxy daemon when an agent is bound to a
    session; read by the host-side discovery surface to surface the
    bound-agent name in ``acp list`` output.
    """
    return _acp_runtime_path(project_id, task_id, suffix=".bound")


def acp_socket_is_live(path: Path) -> bool:
    """Return ``True`` when a peer is currently accepting on *path*.

    Distinguishes a live ACP daemon from a stale socket file left
    behind by a crash: a successful ``connect`` means a peer is
    listening, while ``ECONNREFUSED`` (and any other ``OSError``)
    means the file is safe to unlink.  Used by the daemon at startup
    (skip-if-already-running) and by ``terok acp connect`` (skip-spawn
    when the daemon is already up).
    """
    if not path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            probe.connect(str(path))
    except OSError:
        return False
    return True


def vault_root() -> Path:
    """Shared vault directory used by all terok ecosystem packages.

    Houses token broker DB, SSH signer keys, and agent config mounts.
    Lives under the ``terok/`` namespace so a single ``rm -rf`` or backup
    captures everything.

    Priority: ``TEROK_VAULT_DIR`` → ``TEROK_CREDENTIALS_DIR`` (deprecated
    fallback) → ``/var/lib/terok/vault`` (root) → XDG data dir.
    """
    env = os.getenv("TEROK_VAULT_DIR")
    if env:
        return Path(env).expanduser()
    legacy_env = os.getenv("TEROK_CREDENTIALS_DIR")
    if legacy_env:
        warnings.warn(
            "TEROK_CREDENTIALS_DIR is deprecated; use TEROK_VAULT_DIR instead",
            DeprecationWarning,
            stacklevel=2,
        )
        return Path(legacy_env).expanduser()
    if _is_root():
        return Path("/var/lib") / APP_NAME / _VAULT_SUBDIR
    if _user_data_dir is not None:
        return Path(_user_data_dir(APP_NAME)) / _VAULT_SUBDIR
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME / _VAULT_SUBDIR
    return Path.home() / ".local" / "share" / APP_NAME / _VAULT_SUBDIR
