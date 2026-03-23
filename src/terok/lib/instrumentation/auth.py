# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""Re-export shim — canonical code lives in ``terok_agent.auth``."""

from terok_agent.auth import (  # noqa: F401 — re-exported
    AUTH_PROVIDERS,
    AuthKeyConfig,
    AuthProvider,
    authenticate,
)

__all__ = [
    "AUTH_PROVIDERS",
    "AuthKeyConfig",
    "AuthProvider",
    "authenticate",
]
