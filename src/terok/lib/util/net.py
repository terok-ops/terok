# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Small URL helpers — host formatting, authority normalisation."""

from __future__ import annotations


def url_host(host: str) -> str:
    """*host* formatted for an HTTP URL authority.

    IPv6 literals are wrapped in square brackets so ``::1`` becomes
    ``[::1]``; IPv4 addresses and hostnames pass through unchanged.
    Already-bracketed input is left alone to avoid double-wrapping.
    """
    return f"[{host}]" if ":" in host and not host.startswith("[") else host
