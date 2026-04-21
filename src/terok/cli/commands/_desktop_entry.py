# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install the XDG desktop entry + icon theme PNG for ``terok-tui``.

``terok setup`` calls :func:`install_desktop_entry` (or the matching
:func:`uninstall_desktop_entry`) as a default-on phase, so the TUI
appears as *Terok* in GNOME / KDE / XFCE application menus without the
operator knowing the template layout.  Every step soft-fails so a
headless host without ``.local/share`` or without ``xdg-utils`` never
kills the wider ``terok setup`` flow.

Two backends, picked at install time:

1. **``xdg-utils``** (preferred — ``xdg-desktop-menu install`` +
   ``xdg-desktop-icon install``).  Standard freedesktop tooling;
   handles file validation via ``desktop-file-install`` and runs the
   ``update-desktop-database`` + ``gtk-update-icon-cache`` refreshes
   itself.  Also the same API we'd hook into from an rpm/deb
   ``%post`` script later with ``--mode=system``.
2. **Manual fallback** — direct write to
   ``$XDG_DATA_HOME/applications`` + icon tree, then invoke the two
   cache-refresh binaries ourselves.  Used when ``xdg-utils`` isn't
   installed (minimal container images, some CI runners).

The passive assets (``.desktop`` template, logo PNG) live under
``terok/resources/desktop/`` — this module is the *builder* that reads
them, renders the ``{{BIN}}`` placeholder, stages them to a tempdir,
and delegates to the XDG tool of choice.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess  # nosec B404 — cache refresh binaries are trusted
import tempfile
from importlib import resources as importlib_resources
from importlib.resources.abc import Traversable
from pathlib import Path

_log = logging.getLogger(__name__)

#: Base name of the application launcher and icon — must match
#: ``Icon=terok`` in the template for GNOME's icon-theme resolver.
APP_NAME = "terok"

_DESKTOP_FILE = f"{APP_NAME}.desktop"
_ICON_FILE = f"{APP_NAME}.png"
_ICON_SIZE = "256"  # logo is 283x283, close enough for the 256x256 bucket
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
_ICON_SIZE_DIR = f"{_ICON_SIZE}x{_ICON_SIZE}"
_DEFAULT_DATA_HOME = (".local", "share")  # $HOME/.local/share — XDG fallback

_XDG_MENU_BINARY = "xdg-desktop-menu"
_XDG_ICON_BINARY = "xdg-desktop-icon"

_SUBPROCESS_TIMEOUT_S = 10


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
    """Render the launcher + copy the icon, via xdg-utils when available.

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
    logo_bytes = _resource_dir().joinpath(_LOGO_NAME).read_bytes()
    if _xdg_utils_available():
        _install_via_xdg_utils(rendered, logo_bytes)
    else:
        _install_manually(rendered, logo_bytes)


def uninstall_desktop_entry() -> None:
    """Remove the launcher + icon, via xdg-utils when available."""
    if _xdg_utils_available():
        _uninstall_via_xdg_utils()
    else:
        _uninstall_manually()


def is_desktop_entry_installed() -> bool:
    """Return True when both the ``.desktop`` and icon files exist on disk.

    Probes the install tree directly rather than asking xdg-utils — both
    backends land the same files in the same XDG-spec locations, so the
    presence check is backend-agnostic.
    """
    return _desktop_entry_path().is_file() and _icon_path().is_file()


# ── xdg-utils backend ─────────────────────────────────────────────────


def _xdg_utils_available() -> bool:
    """Return True only when *both* xdg-utils front-ends are on PATH."""
    return bool(shutil.which(_XDG_MENU_BINARY) and shutil.which(_XDG_ICON_BINARY))


def _install_via_xdg_utils(desktop_contents: str, logo_bytes: bytes) -> None:
    """Stage the rendered files and delegate install + cache refresh to xdg-utils.

    ``xdg-desktop-menu install`` runs ``desktop-file-install`` (catches
    malformed keys), drops the file under the user's applications dir,
    and kicks ``update-desktop-database``.  ``xdg-desktop-icon install``
    does the equivalent for the hicolor tree including
    ``gtk-update-icon-cache``.  Both are name-by-filename — we stage to
    a tempdir so the basenames carry the destination names we want.
    """
    with tempfile.TemporaryDirectory(prefix="terok-desktop-") as td:
        staged_dir = Path(td)
        staged_desktop = staged_dir / _DESKTOP_FILE
        staged_icon = staged_dir / _ICON_FILE
        staged_desktop.write_text(desktop_contents, encoding="utf-8")
        staged_icon.write_bytes(logo_bytes)
        _run_xdg(
            _XDG_MENU_BINARY,
            "install",
            "--novendor",
            str(staged_desktop),
        )
        _run_xdg(
            _XDG_ICON_BINARY,
            "install",
            "--novendor",
            "--size",
            _ICON_SIZE,
            str(staged_icon),
        )


def _uninstall_via_xdg_utils() -> None:
    """Delegate removal + cache refresh to xdg-utils."""
    _run_xdg(_XDG_MENU_BINARY, "uninstall", "--novendor", _DESKTOP_FILE)
    _run_xdg(_XDG_ICON_BINARY, "uninstall", "--novendor", "--size", _ICON_SIZE, _ICON_FILE)


def _run_xdg(binary: str, *args: str) -> None:
    """Invoke an xdg-utils front-end, swallow failures — install is best-effort."""
    found = shutil.which(binary)
    if not found:  # pragma: no cover — gated by _xdg_utils_available
        return
    # nosec B603 — argv is our own literal binary path plus subcommand/arg tokens.
    try:
        subprocess.run(  # noqa: S603  # nosec B603
            [found, *args],
            check=False,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("%s %s failed: %s", binary, args, exc)


# ── Manual fallback ───────────────────────────────────────────────────


def _install_manually(desktop_contents: str, logo_bytes: bytes) -> None:
    """Write the launcher + icon directly and trigger cache refreshes by hand."""
    desktop_path = _desktop_entry_path()
    desktop_path.parent.mkdir(parents=True, exist_ok=True)
    desktop_path.write_text(desktop_contents, encoding="utf-8")
    desktop_path.chmod(0o644)

    icon_path = _icon_path()
    icon_path.parent.mkdir(parents=True, exist_ok=True)
    icon_path.write_bytes(logo_bytes)
    icon_path.chmod(0o644)

    _refresh_desktop_database()
    _refresh_icon_cache()


def _uninstall_manually() -> None:
    """Unlink the launcher + icon and refresh caches so menus forget."""
    for path in (_desktop_entry_path(), _icon_path()):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            _log.warning("failed to unlink %s: %s", path, exc)
    _refresh_desktop_database()
    _refresh_icon_cache()


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


# ── Template loading ──────────────────────────────────────────────────


def _load_template() -> str:
    """Read the bundled ``terok.desktop.template`` as text."""
    return _resource_dir().joinpath(_TEMPLATE_NAME).read_text(encoding="utf-8")


# ── Manual cache refresh (fallback backend only) ──────────────────────


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
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("%s refresh failed: %s", binary, exc)
