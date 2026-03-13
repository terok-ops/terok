# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for terok-shield egress firewall.

Creates per-task :class:`Shield` instances from the terok global config.
Each task gets its own ``state_dir`` under ``{task_dir}/shield/``.
"""

from pathlib import Path

from terok_shield import (
    NftNotFoundError,  # noqa: F401 — re-exported
    Shield,
    ShieldConfig,
    ShieldMode,
    ShieldState,  # noqa: F401 — re-exported
)

from ..core.config import get_gate_server_port, get_global_section
from ..core.paths import config_root

_DEFAULT_PROFILES = ("dev-standard",)


def _state_dir(task_dir: Path) -> Path:
    """Return the per-task shield state directory."""
    return task_dir / "shield"


def _profiles_dir() -> Path:
    """Return the terok-managed shield profiles directory.

    Custom ``.txt`` allowlist files placed here are visible to all
    terok-managed Shield instances.  This is separate from the
    standalone ``terok-shield`` CLI's own config directory.
    """
    return config_root() / "shield" / "profiles"


def _normalize_profiles(raw: object) -> tuple[str, ...]:
    """Normalize a profiles config value to a tuple of strings.

    Raises:
        TypeError: If *raw* is not a string or list of strings.
    """
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, str):
                raise TypeError(
                    f"shield.profiles must be a list of strings, "
                    f"but found {type(item).__name__}: {item!r}"
                )
        return tuple(raw)
    raise TypeError(
        f"shield.profiles must be a string or a list of strings, "
        f"but found {type(raw).__name__}: {raw!r}"
    )


def make_shield(task_dir: Path) -> Shield:
    """Construct a per-task :class:`Shield` from the terok global config.

    Reads the ``shield:`` section of the global config and builds a
    :class:`ShieldConfig` with ``state_dir`` scoped to *task_dir*.

    The ``Shield`` constructor validates that the ``nft`` binary is
    available on the host and raises :class:`~terok_shield.NftNotFoundError`
    if it is missing.
    """
    sec = get_global_section("shield")
    profiles = _normalize_profiles(sec.get("profiles", _DEFAULT_PROFILES))

    config = ShieldConfig(
        state_dir=_state_dir(task_dir),
        mode=ShieldMode.HOOK,
        default_profiles=profiles,
        loopback_ports=(get_gate_server_port(),),
        audit_enabled=bool(sec.get("audit", True)),
        profiles_dir=_profiles_dir(),
    )
    return Shield(config)


def pre_start(container: str, task_dir: Path) -> list[str]:
    """Return extra ``podman run`` args for egress firewalling."""
    return make_shield(task_dir).pre_start(container)


def down(container: str, task_dir: Path) -> None:
    """Set shield to bypass mode (allow egress) for a running container."""
    make_shield(task_dir).down(container)


def up(container: str, task_dir: Path) -> None:
    """Set shield to deny-all mode for a running container."""
    make_shield(task_dir).up(container)


def state(container: str, task_dir: Path) -> ShieldState:
    """Return the live shield state for a running container."""
    return make_shield(task_dir).state(container)


def status() -> dict:
    """Return shield status dict from the global config.

    This reads the terok config directly rather than constructing a
    :class:`Shield`, because ``Shield.status()`` returns *available*
    profiles (filesystem scan) while terok needs *configured* profiles.
    """
    sec = get_global_section("shield")
    profiles = _normalize_profiles(sec.get("profiles", _DEFAULT_PROFILES))
    return {
        "mode": "hook",
        "profiles": list(profiles),
        "audit_enabled": bool(sec.get("audit", True)),
    }
