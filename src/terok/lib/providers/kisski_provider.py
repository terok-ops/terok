# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-FileCopyrightText: 2026 Andreas Knüpfer
# SPDX-License-Identifier: Apache-2.0

"""KISSKI provider implementation using the unified OpenCode base."""

from .opencode_base import OpenCodeProvider


class KISSKIProvider(OpenCodeProvider):
    """KISSKI provider for OpenCode."""

    @property
    def provider_name(self) -> str:
        """Short name of the provider."""
        return "kisski"

    @property
    def display_name(self) -> str:
        """Human-readable name of the provider."""
        return "KISSKI"

    @property
    def default_base_url(self) -> str:
        """Default API base URL."""
        return "https://chat-ai.academiccloud.de/v1"

    @property
    def preferred_model(self) -> str:
        """Preferred model ID."""
        return "devstral-2-123b-instruct-2512"

    @property
    def fallback_model(self) -> str:
        """Fallback model ID if preferred is unavailable."""
        return "mistral-large-3-675b-instruct-2512"

    @property
    def env_var_name(self) -> str:
        """Environment variable name for API key."""
        return "KISSKI_API_KEY"

    @property
    def config_dir_name(self) -> str:
        """Configuration directory name."""
        return ".kisski"

    @property
    def provider_config_key(self) -> str:
        """Key used in opencode.json provider configuration."""
        return "kisski"

    @property
    def provider_display_name(self) -> str:
        """Provider display name in opencode.json."""
        return "KISSKI"


def main() -> int:
    """Entry point for kisski command."""
    provider = KISSKIProvider()
    return provider.main()


if __name__ == "__main__":
    raise SystemExit(main())
