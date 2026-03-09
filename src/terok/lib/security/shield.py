# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield adapter: maps terok project config to terok-shield's public API.

terok-shield is a **hard dependency** — containers must never start without
egress firewalling.  This module constructs a :class:`ShieldConfig` from
terok's global configuration and delegates to terok-shield's lifecycle
functions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from terok_shield import (
    ShieldConfig,
    ShieldMode,
    list_log_files,
    list_profiles,
    shield_allow,
    shield_deny,
    shield_post_start,
    shield_pre_start,
    shield_pre_stop,
    shield_rules,
    shield_setup,
    shield_status,
    tail_log,
)

from ..core.config import get_gate_server_port, get_global_section

if TYPE_CHECKING:
    from collections.abc import Iterator


def get_shield_config() -> ShieldConfig:
    """Build a :class:`ShieldConfig` from terok's global ``shield`` section.

    The gate port is always sourced from terok's own gate-server config so that
    the two components agree on the forwarded port.
    """
    section = get_global_section("shield")

    mode_str = section.get("mode", "hook")
    try:
        mode = ShieldMode(mode_str)
    except ValueError:
        raise SystemExit(f"Invalid shield mode in config: {mode_str!r}") from None

    raw_profiles = section.get("profiles", ["dev-standard"])
    profiles = tuple(raw_profiles) if isinstance(raw_profiles, list) else ("dev-standard",)

    audit = section.get("audit", {})
    if not isinstance(audit, dict):
        audit = {}

    return ShieldConfig(
        mode=mode,
        default_profiles=profiles,
        gate_port=get_gate_server_port(),
        audit_enabled=audit.get("enabled", True) is True,
        audit_log_allowed=audit.get("log_allowed", True) is True,
    )


# ── Lifecycle wrappers ────────────────────────────────────────────


def pre_start(cname: str) -> list[str]:
    """Prepare shield for container start; return extra ``podman run`` args."""
    cfg = get_shield_config()
    return shield_pre_start(cname, config=cfg)


def post_start(cname: str) -> None:
    """Post-start hook (bridge mode only)."""
    cfg = get_shield_config()
    shield_post_start(cname, config=cfg)


def pre_stop(cname: str) -> None:
    """Pre-stop hook (bridge mode cleanup, no-op in hook mode)."""
    cfg = get_shield_config()
    shield_pre_stop(cname, config=cfg)


# ── Management wrappers ──────────────────────────────────────────


def setup() -> None:
    """Run shield setup (install OCI hook or verify bridge)."""
    shield_setup(config=get_shield_config())


def status() -> dict:
    """Return shield status dict."""
    return shield_status(config=get_shield_config())


def allow(container: str, target: str) -> list[str]:
    """Live-allow a domain or IP for a running container."""
    return shield_allow(container, target, config=get_shield_config())


def deny(container: str, target: str) -> list[str]:
    """Live-deny a domain or IP for a running container."""
    return shield_deny(container, target, config=get_shield_config())


def rules(container: str) -> str:
    """Return current nft rules for a container."""
    return shield_rules(container, config=get_shield_config())


def logs(container: str | None = None, n: int = 50) -> Iterator[dict]:
    """Yield audit log entries for *container* (or list containers if None)."""
    if container is None:
        return iter([])
    return tail_log(container, n=n)


def get_log_containers() -> list[str]:
    """Return container names that have audit logs."""
    return list_log_files()


def get_profiles() -> list[str]:
    """Return available shield profile names."""
    return list_profiles()


# Re-export for facade / CLI convenience
__all__ = [
    "ShieldConfig",
    "ShieldMode",
    "allow",
    "deny",
    "get_log_containers",
    "get_profiles",
    "get_shield_config",
    "logs",
    "post_start",
    "pre_start",
    "pre_stop",
    "rules",
    "setup",
    "status",
]
