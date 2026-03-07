# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Podman user-namespace and network helpers for rootless operation."""

import functools
import json
import os
import subprocess


def _podman_userns_args() -> list[str]:
    """Return user namespace args for rootless podman so UID 1000 maps correctly."""
    if os.geteuid() == 0:
        return []
    return ["--userns=keep-id:uid=1000,gid=1000"]


@functools.lru_cache(maxsize=1)
def _detect_rootless_network_mode() -> str:
    """Return ``"slirp4netns"``, ``"pasta"``, or ``"unknown"`` from ``podman info``.

    Reads ``host.rootlessNetworkCmd`` (present on some Podman 4.x and all 5.x).
    When the field is absent, falls back to the Podman major version:
    Podman 5+ defaults to pasta, 4.x defaults to slirp4netns (regardless
    of whether the pasta binary is installed).  On error returns
    ``"unknown"``.
    """
    try:
        raw = subprocess.check_output(
            ["podman", "info", "-f", "json"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        info = json.loads(raw)
        host = info.get("host", {})

        # Primary: rootlessNetworkCmd is the authoritative answer
        cmd = host.get("rootlessNetworkCmd", "")
        if cmd in ("pasta", "slirp4netns"):
            return cmd

        # Fallback: Podman 5+ defaults to pasta, 4.x to slirp4netns.
        version_str = info.get("version", {}).get("Version", "")
        major = int(version_str.split(".")[0]) if version_str else 0
        return "pasta" if major >= 5 else "slirp4netns"
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        TypeError,
        ValueError,
    ):
        return "unknown"


def _podman_network_args(gate_port: int = 9418) -> list[str]:
    """Return network flags so rootless containers can reach host loopback.

    On **slirp4netns** (Podman 4.x), the container's default gateway
    ``10.0.2.2`` is routed to the host's ``127.0.0.1`` when
    ``allow_host_loopback=true`` is set.  We override
    ``host.containers.internal`` to ``10.0.2.2`` so ``git://`` URLs work.

    On **pasta** (Podman 5+), Podman sets ``host.containers.internal`` to a
    link-local address (``169.254.1.2``) which does **not** reach the host's
    loopback.  We use pasta's ``-T`` (``--tcp-ns``) option to forward
    *gate_port* from the container namespace to the host, and override
    ``host.containers.internal`` to ``127.0.0.1`` so the ``git://`` URL
    targets a forwarded port.
    """
    if os.geteuid() == 0:
        return []

    mode = _detect_rootless_network_mode()
    if mode == "slirp4netns":
        return [
            "--network",
            "slirp4netns:allow_host_loopback=true",
            "--add-host",
            "host.containers.internal:10.0.2.2",
        ]
    if mode == "pasta":
        return [
            "--network",
            f"pasta:-T,{gate_port}",
            "--add-host",
            "host.containers.internal:127.0.0.1",
        ]
    return []
