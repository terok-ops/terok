# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""Re-export shim — canonical code lives in ``terok_agent.headless_providers``."""

from terok_agent.headless_providers import (  # noqa: F401 — re-exported
    HEADLESS_PROVIDERS,
    PROVIDER_NAMES,
    CLIOverrides,
    HeadlessProvider,
    OpenCodeProviderConfig,
    ProviderConfig,
    WrapperConfig,
    apply_provider_config,
    build_headless_command,
    collect_all_auto_approve_env,
    collect_opencode_provider_env,
    generate_agent_wrapper,
    generate_all_wrappers,
    get_provider,
)

__all__ = [
    "CLIOverrides",
    "HeadlessProvider",
    "HEADLESS_PROVIDERS",
    "OpenCodeProviderConfig",
    "PROVIDER_NAMES",
    "ProviderConfig",
    "WrapperConfig",
    "apply_provider_config",
    "build_headless_command",
    "collect_all_auto_approve_env",
    "collect_opencode_provider_env",
    "generate_agent_wrapper",
    "generate_all_wrappers",
    "get_provider",
]
