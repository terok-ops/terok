# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""terok package.

Modules:
- terok.cli: CLI entry point package (terok)
- terok.tui: Text UI entry point package (terok)
- terok.ui_utils: Shared UI helpers (terminal ANSI, editor launch)
- terok.lib: Business logic layer (core, containers, security, wizards, integrations, util)
"""

__all__ = [
    "cli",
    "tui",
    "ui_utils",
    "lib",
]

# Version information - single source of truth using importlib.metadata
try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("terok")
except PackageNotFoundError:
    # Fallback for development mode when package is not installed
    try:
        import tomllib
        from pathlib import Path

        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        if pyproject_path.exists():
            with open(pyproject_path, "rb") as f:
                pyproject_data = tomllib.load(f)
                __version__ = pyproject_data["tool"]["poetry"]["version"]
        else:
            __version__ = "unknown"
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError):
        __version__ = "unknown"
