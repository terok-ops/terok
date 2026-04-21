# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install the XDG desktop entry + icon theme PNG for ``terok-tui``.

``terok setup`` calls :func:`install_desktop_entry` (or the matching
:func:`uninstall_desktop_entry`) as a default-on phase, so the TUI
appears as *Terok* in GNOME / KDE / XFCE application menus without the
operator knowing the template layout.  The work is three writes +
two best-effort cache refreshes; every step soft-fails so a headless
host without a ``.local/share`` or without ``update-desktop-database``
never kills the wider ``terok setup`` flow.

The passive assets (``.desktop`` template, logo PNG) live under
``terok/resources/desktop/`` — this module is the *builder* that reads
them, renders the ``{{BIN}}`` placeholder, and copies the output to
the operator's XDG tree.  Keeping the builder outside ``resources/``
matches the rest of the tree: that directory holds only data files
consumed by code that lives elsewhere.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess  # nosec B404 — cache refresh binaries are trusted
from importlib import resources as importlib_resources
from importlib.resources.abc import Traversable
from pathlib import Path

_log = logging.getLogger(__name__)

#: Base name of the application launcher and icon — must match
#: ``Icon=terok`` in the template for GNOME's icon-theme resolver.
APP_NAME = "terok"

_DESKTOP_FILE = f"{APP_NAME}.desktop"
_ICON_FILE = f"{APP_NAME}.png"
_ICON_SIZE_DIR = "256x256"  # fixed size; logo is 283x283, close enough for display
_TEMPLATE_NAME = "terok.desktop.template"
_LOGO_NAME = "terok-logo.png"

# XDG Base Directory + Icon Theme spec path fragments.  Named so a
# future theme-dir shift (e.g. an Adwaita-symbolic install path) is a
# single-constant change and so ``grep`` for the fragment lands on the
# canonical definition rather than every join site.
_APPLICATIONS_SUBDIR = "applications"
_ICONS_SUBDIR = "icons"
_HICOLOR_THEME = "hicolor"
_APPS_SUBDIR = "apps"
_DEFAULT_DATA_HOME = (".local", "share")  # $HOME/.local/share — XDG fallback


def _resource_dir() -> Traversable:
    """Return a ``Traversable`` rooted at the passive ``resources/desktop/`` assets.

    Uses the namespace-package idiom already used by
    :func:`terok.lib.core.config.bundled_presets_dir`: walk the top-level
    ``terok`` package into the ``resources`` + ``desktop`` subdirs (no
    ``__init__.py`` anywhere under ``resources/``, matching the project's
    "resources hold only data files" convention).
    """
    return importlib_resources.files("terok").joinpath("resources", "desktop")


def install_desktop_entry(bin_path: str | Path) -> None:
    """Write ``terok.desktop``, copy the logo into the icon theme, refresh caches.

    Args:
        bin_path: Absolute path (or bare name) to ``terok-tui``.  The
            freedesktop ``Exec=`` / ``TryExec=`` keys need this — the
            launcher's minimal PATH often misses ``~/.local/bin``, so
            ``shutil.which("terok-tui")``'s absolute result is preferred
            over the short name.
    """
    rendered = (
        _load_template().replace("{{BIN}}", str(bin_path)).replace("{{TRY_EXEC}}", str(bin_path))
    )
    desktop_path = _desktop_entry_path()
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.write_text(rendered, encoding="utf-8")
    desktop_path.chmod(0o644)

    _install_icon()
    _refresh_desktop_database()
    _refresh_icon_cache()


def uninstall_desktop_entry() -> None:
    """Remove the ``.desktop`` file and icon; refresh caches so menus forget."""
    desktop_path = _desktop_entry_path()
    icon_path = _icon_path()
    for path in (desktop_path, icon_path):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _log.warning("failed to unlink %s: %s", path, exc)
    _refresh_desktop_database()
    _refresh_icon_cache()


def is_desktop_entry_installed() -> bool:
    """Return True when both the ``.desktop`` and icon files exist on disk."""
    return _desktop_entry_path().is_file() and _icon_path().is_file()


# ── Path derivation ───────────────────────────────────────────────────


def _desktop_entry_path() -> Path:
    """Return ``$XDG_DATA_HOME/applications/terok.desktop`` (XDG default)."""
    return _data_home() / _APPLICATIONS_SUBDIR / _DESKTOP_FILE


def _icon_path() -> Path:
    """Return ``$XDG_DATA_HOME/icons/hicolor/256x256/apps/terok.png``."""
    return (
        _data_home() / _ICONS_SUBDIR / _HICOLOR_THEME / _ICON_SIZE_DIR / _APPS_SUBDIR / _ICON_FILE
    )


def _data_home() -> Path:
    """Return the user's XDG data home, honouring ``$XDG_DATA_HOME`` when set."""
    override = os.environ.get("XDG_DATA_HOME")
    return Path(override) if override else Path.home().joinpath(*_DEFAULT_DATA_HOME)


# ── Template + icon source loading ────────────────────────────────────


def _load_template() -> str:
    """Read the bundled ``terok.desktop.template`` as text."""
    return _resource_dir().joinpath(_TEMPLATE_NAME).read_text(encoding="utf-8")


def _install_icon() -> None:
    """Copy the bundled logo into the hicolor icon theme tree."""
    dest = _icon_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(_resource_dir().joinpath(_LOGO_NAME).read_bytes())
    dest.chmod(0o644)


# ── Cache refresh (best-effort) ───────────────────────────────────────


def _refresh_desktop_database() -> None:
    """Nudge ``update-desktop-database`` if present; silent otherwise."""
    _run_cache_refresh(
        "update-desktop-database",
        [_data_home() / _APPLICATIONS_SUBDIR],
    )


def _refresh_icon_cache() -> None:
    """Nudge ``gtk-update-icon-cache`` on the hicolor theme if present."""
    _run_cache_refresh(
        "gtk-update-icon-cache",
        ["-q", "-t", _data_home() / _ICONS_SUBDIR / _HICOLOR_THEME],
    )


def _run_cache_refresh(binary: str, args: list[str | Path]) -> None:
    """Invoke *binary* with *args*, swallow every failure — caches are optional."""
    found = shutil.which(binary)
    if not found:
        return
    # nosec B603 — argv is a literal + controlled Path; no shell, no user input.
    try:
        subprocess.run(  # noqa: S603  # nosec B603
            [found, *[str(a) for a in args]],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("%s refresh failed: %s", binary, exc)
