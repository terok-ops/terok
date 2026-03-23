# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Re-export shim — canonical code lives in ``terok_agent.instructions``."""

from terok_agent.instructions import (  # noqa: F401 — re-exported
    bundled_default_instructions,
    has_custom_instructions,
    resolve_instructions,
)

__all__ = [
    "bundled_default_instructions",
    "has_custom_instructions",
    "resolve_instructions",
]
