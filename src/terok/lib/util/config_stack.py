# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Re-export shim — canonical code lives in ``terok_agent.config_stack``."""

from terok_agent.config_stack import (  # noqa: F401 — re-exported
    ConfigScope,
    ConfigStack,
    deep_merge,
    load_json_scope,
    load_yaml_scope,
)

__all__ = [
    "ConfigScope",
    "ConfigStack",
    "deep_merge",
    "load_json_scope",
    "load_yaml_scope",
]
