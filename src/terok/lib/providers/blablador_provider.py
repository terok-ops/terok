# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Blablador provider implementation using the unified OpenCode base."""

from .opencode_base import OpenCodeProvider


class BlabladorProvider(OpenCodeProvider):
    """Blablador provider for OpenCode."""

    @property
    def provider_name(self) -> str:
        """Short name of the provider."""
        return "blablador"

    @property
    def display_name(self) -> str:
        """Human-readable name of the provider."""
        return "Helmholtz Blablador"

    @property
    def default_base_url(self) -> str:
        """Default API base URL."""
        return "https://api.helmholtz-blablador.fz-juelich.de/v1"

    @property
    def preferred_model(self) -> str:
        """Preferred model ID."""
        return "alias-huge"

    @property
    def fallback_model(self) -> str:
        """Fallback model ID if preferred is unavailable."""
        return "alias-code"

    @property
    def env_var_name(self) -> str:
        """Environment variable name for API key."""
        return "BLABLADOR_API_KEY"

    @property
    def config_dir_name(self) -> str:
        """Configuration directory name."""
        return ".blablador"

    @property
    def provider_config_key(self) -> str:
        """Key used in opencode.json provider configuration."""
        return "blablador"

    @property
    def provider_display_name(self) -> str:
        """Provider display name in opencode.json."""
        return "Helmholtz Blablador"


def main() -> int:
    """Entry point for blablador command."""
    provider = BlabladorProvider()
    return provider.main()


if __name__ == "__main__":
    raise SystemExit(main())
