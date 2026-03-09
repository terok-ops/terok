# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Adapter for terok-shield egress firewall.

Maps terok global config to :class:`ShieldConfig` and wraps the
``terok_shield`` public API for use by the task runner and CLI.
"""

from collections.abc import Iterator

from terok_shield import (
    ShieldConfig,
    ShieldMode,
    list_log_files,
    list_profiles,
    shield_allow,
    shield_deny,
    shield_pre_start,
    shield_resolve,
    shield_rules,
    shield_setup,
    shield_status,
    tail_log,
)

from ..core.config import get_gate_server_port, get_global_section

_DEFAULT_PROFILES = ("dev-standard",)


def get_shield_config() -> ShieldConfig:
    """Build a :class:`ShieldConfig` from the terok global ``shield:`` section.

    Recognised keys::

        shield:
          profiles: [dev-standard, custom]
          audit: true
          audit_log_allowed: true
    """
    sec = get_global_section("shield")
    profiles = sec.get("profiles", _DEFAULT_PROFILES)
    if isinstance(profiles, str):
        profiles = (profiles,)
    elif isinstance(profiles, (list, tuple)):
        # Ensure it's an iterable of strings, rejecting nested lists/dicts/etc.
        for item in profiles:
            if not isinstance(item, str):
                raise TypeError(
                    f"shield.profiles must be a list of strings, but found {type(item).__name__}: {item!r}"
                )
        profiles = tuple(profiles)
    else:
        raise TypeError(
            f"shield.profiles must be a string or a list of strings, but found {type(profiles).__name__}: {profiles!r}"
        )
    return ShieldConfig(
        mode=ShieldMode.HOOK,
        default_profiles=profiles,
        gate_port=get_gate_server_port(),
        audit_enabled=bool(sec.get("audit", True)),
        audit_log_allowed=bool(sec.get("audit_log_allowed", True)),
    )


def pre_start(container: str) -> list[str]:
    """Return extra ``podman run`` args for egress firewalling."""
    return shield_pre_start(container, config=get_shield_config())


def setup() -> None:
    """Install the OCI hook for shield."""
    shield_setup(config=get_shield_config())


def status() -> dict:
    """Return shield status dict."""
    return shield_status(config=get_shield_config())


def allow(container: str, target: str) -> list[str]:
    """Dynamically allow *target* for a running container."""
    return shield_allow(container, target, config=get_shield_config())


def deny(container: str, target: str) -> list[str]:
    """Dynamically deny *target* for a running container."""
    return shield_deny(container, target, config=get_shield_config())


def rules(container: str) -> str:
    """Return current nft rules for a container."""
    return shield_rules(container, config=get_shield_config())


def resolve(container: str) -> list[str]:
    """Resolve DNS for a container's allowed domains."""
    return shield_resolve(container, config=get_shield_config())


def logs(container: str, n: int = 50) -> Iterator[dict]:
    """Tail audit log entries for a container."""
    return tail_log(container, n=n)


def get_log_containers() -> list[str]:
    """List containers that have audit log files."""
    return list_log_files()


def get_profiles() -> list[str]:
    """List available shield profiles."""
    return list_profiles()
