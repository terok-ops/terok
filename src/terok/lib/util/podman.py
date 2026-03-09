# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Podman user-namespace helpers for rootless operation."""

import os


def _podman_userns_args() -> list[str]:
    """Return user namespace args for rootless podman so UID 1000 maps correctly."""
    if os.geteuid() == 0:
        return []
    return ["--userns=keep-id:uid=1000,gid=1000"]
