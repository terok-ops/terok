# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Re-export shim — canonical code lives in ``terok_agent._util._podman``."""

from terok_agent._util._podman import podman_userns_args as _podman_userns_args  # noqa: F401

__all__ = ["_podman_userns_args"]
