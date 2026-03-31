# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Platform-aware path resolution for config, state, and runtime directories."""

import getpass
import os
from pathlib import Path

try:
    from platformdirs import (
        user_config_dir as _user_config_dir,
        user_data_dir as _user_data_dir,
    )
except ImportError:  # optional dependency
    _user_config_dir = _user_data_dir = None  # type: ignore[assignment]


APP_NAME = "terok"
CREDENTIALS_APP_NAME = "terok-credentials"


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
    """
    Writable state (tasks, pods, caches).

    Priority:
      1. TEROK_STATE_DIR
      2. if root   → /var/lib/terok
         else      → ${XDG_DATA_HOME:-~/.local/share}/terok
    """
    env = os.getenv("TEROK_STATE_DIR")
    if env:
        return Path(env).expanduser()

    if _is_root():
        return Path("/var/lib") / APP_NAME

    if _user_data_dir is not None:
        return Path(_user_data_dir(APP_NAME))

    # Fallback without platformdirs: honor XDG_DATA_HOME if set
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / APP_NAME
    return Path.home() / ".local" / "share" / APP_NAME


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


def credentials_root() -> Path:
    """Shared credentials directory used by all terok ecosystem packages.

    Priority: ``TEROK_CREDENTIALS_DIR`` → ``/var/lib/terok-credentials`` (root)
    → XDG data dir.
    """
    env = os.getenv("TEROK_CREDENTIALS_DIR")
    if env:
        return Path(env).expanduser()
    if _is_root():
        return Path("/var/lib") / CREDENTIALS_APP_NAME
    if _user_data_dir is not None:
        return Path(_user_data_dir(CREDENTIALS_APP_NAME))
    xdg = os.getenv("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / CREDENTIALS_APP_NAME
    return Path.home() / ".local" / "share" / CREDENTIALS_APP_NAME
