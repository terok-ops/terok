# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install the XDG desktop entry + icon theme PNG for ``terok-tui``.

``terok setup`` calls :func:`install_desktop_entry` (or the matching
:func:`uninstall_desktop_entry`) as a default-on phase, so the TUI
appears as *Terok* in GNOME / KDE / XFCE application menus without the
operator knowing the template layout.  Every step soft-fails so a
headless host without ``.local/share`` or without ``xdg-utils`` never
kills the wider ``terok setup`` flow.

Preferred path is ``xdg-utils`` — ``xdg-desktop-menu install`` for the
launcher plus ``xdg-icon-resource install --context apps`` for the
hicolor icon theme entry.  Standard freedesktop tooling: it validates
the ``.desktop`` via ``desktop-file-install``, drops the icon into
``hicolor/<size>/apps/`` so ``Icon=terok`` resolves, and kicks the
``update-desktop-database`` + ``gtk-update-icon-cache`` refreshes for
us.  Also the same API we'd hook into later from an rpm/deb ``%post``
with ``--mode=system``.

When ``xdg-utils`` isn't on PATH (minimal container images, some CI
runners) we fall back to writing the XDG tree ourselves and firing the
cache-refresh binaries directly.  This is *best-effort*: the files end
up in the right place on hosts that match the spec, but there's no
``desktop-file-install`` validation and no cover for DE-specific
layout drift.  :func:`install_desktop_entry` returns a
:class:`DesktopBackend` so the caller can surface a gentle warning
when the fallback kicks in.

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
from enum import StrEnum
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
# ``xdg-icon-resource`` registers an icon in the hicolor theme so
# ``Icon=terok`` in the .desktop file resolves.  The similarly-named
# ``xdg-desktop-icon`` would put the PNG on the *user's Desktop folder*
# instead — looks plausible, skips the theme entirely.
_XDG_ICON_RESOURCE_BINARY = "xdg-icon-resource"
_XDG_ICON_CONTEXT = "apps"

_SUBPROCESS_TIMEOUT_S = 10


class DesktopBackend(StrEnum):
    """Which install path :func:`install_desktop_entry` actually took."""

    XDG_UTILS = "xdg-utils"
    FALLBACK = "fallback"


def _resource_dir() -> Traversable:
    """Return a ``Traversable`` rooted at the passive ``resources/desktop/`` assets.

    Uses the namespace-package idiom already used by
    :func:`terok.lib.core.config.bundled_presets_dir`: walk the top-level
    ``terok`` package into the ``resources`` + ``desktop`` subdirs (no
    ``__init__.py`` anywhere under ``resources/``, matching the project's
    "resources hold only data files" convention).
    """
    return importlib_resources.files("terok").joinpath("resources", "desktop")


def install_desktop_entry(bin_path: str | Path) -> DesktopBackend:
    """Render the launcher + copy the icon, via xdg-utils when available.

    Args:
        bin_path: Absolute path (or bare name) to ``terok-tui``.  The
            freedesktop ``Exec=`` / ``TryExec=`` keys need this — the
            launcher's minimal PATH often misses ``~/.local/bin``, so
            ``shutil.which("terok-tui")``'s absolute result is preferred
            over the short name.

    Returns:
        The :class:`DesktopBackend` actually used.  Callers wire this to
        a status-line warning when the fallback kicks in so the operator
        knows ``xdg-utils`` is missing.
    """
    rendered = (
        _load_template().replace("{{BIN}}", str(bin_path)).replace("{{TRY_EXEC}}", str(bin_path))
    )
    logo_bytes = _resource_dir().joinpath(_LOGO_NAME).read_bytes()
    if _xdg_utils_available() and _install_via_xdg_utils(rendered, logo_bytes):
        return DesktopBackend.XDG_UTILS
    # xdg-utils missing *or* it barfed (readonly menu dir, timeout, bad
    # DE detection) — land the files ourselves so the operator still
    # gets a working launcher, and report FALLBACK so the caller can
    # warn.  The DEBUG log carries the xdg-utils failure detail.
    _install_manually(rendered, logo_bytes)
    return DesktopBackend.FALLBACK


def uninstall_desktop_entry() -> DesktopBackend:
    """Remove the launcher + icon, via xdg-utils when available.

    Returns:
        The :class:`DesktopBackend` actually used — symmetric with
        :func:`install_desktop_entry`.  XDG_UTILS only when both
        front-ends reported rc 0; on failure (or xdg-utils absent) we
        retry via manual unlinks and report FALLBACK so the teardown
        leaves no stragglers even when xdg-utils misbehaves.
    """
    if _xdg_utils_available() and _uninstall_via_xdg_utils():
        return DesktopBackend.XDG_UTILS
    _uninstall_manually()
    return DesktopBackend.FALLBACK


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
    return bool(shutil.which(_XDG_MENU_BINARY) and shutil.which(_XDG_ICON_RESOURCE_BINARY))


def _install_via_xdg_utils(desktop_contents: str, logo_bytes: bytes) -> bool:
    """Stage the rendered files and delegate install + cache refresh to xdg-utils.

    ``xdg-desktop-menu install`` runs ``desktop-file-install`` (catches
    malformed keys), drops the file under the user's applications dir,
    and kicks ``update-desktop-database``.  ``xdg-icon-resource install
    --context apps`` does the equivalent for the hicolor theme tree —
    it's the tool that makes ``Icon=terok`` resolvable — and runs
    ``gtk-update-icon-cache`` itself.  We stage the ``.desktop`` to a
    tempdir (xdg-desktop-menu names it by source basename) and pass the
    icon resource name (``terok``) explicitly to ``xdg-icon-resource``
    so the theme entry is deterministic regardless of source filename.

    Returns:
        True only when *both* front-ends reported success.  A partial
        install (menu OK, icon failed — or vice versa) reads as False
        so the caller can retry via the manual path and land in a
        consistent state rather than advertising XDG_UTILS for an
        install that half-failed.
    """
    with tempfile.TemporaryDirectory(prefix="terok-desktop-") as td:
        staged_dir = Path(td)
        staged_desktop = staged_dir / _DESKTOP_FILE
        staged_icon = staged_dir / _ICON_FILE
        staged_desktop.write_text(desktop_contents, encoding="utf-8")
        staged_icon.write_bytes(logo_bytes)
        menu_ok = _run_xdg(
            _XDG_MENU_BINARY,
            "install",
            "--novendor",
            str(staged_desktop),
        )
        icon_ok = _run_xdg(
            _XDG_ICON_RESOURCE_BINARY,
            "install",
            "--novendor",
            "--size",
            _ICON_SIZE,
            "--context",
            _XDG_ICON_CONTEXT,
            str(staged_icon),
            APP_NAME,
        )
    return menu_ok and icon_ok


def _uninstall_via_xdg_utils() -> bool:
    """Delegate removal + cache refresh to xdg-utils.

    Returns:
        True only when *both* front-ends reported success.  A half-
        completed teardown (menu removed, icon theme still holds
        ``terok`` — or vice versa) reads as False so the caller can
        retry via the manual unlinks and actually clear the state.
    """
    menu_ok = _run_xdg(_XDG_MENU_BINARY, "uninstall", "--novendor", _DESKTOP_FILE)
    icon_ok = _run_xdg(
        _XDG_ICON_RESOURCE_BINARY,
        "uninstall",
        "--size",
        _ICON_SIZE,
        "--context",
        _XDG_ICON_CONTEXT,
        APP_NAME,
    )
    return menu_ok and icon_ok


def _run_xdg(binary: str, *args: str) -> bool:
    """Invoke an xdg-utils front-end; return True only on rc-0, False otherwise.

    Never raises — a hung / missing / broken front-end lands in DEBUG
    so an operator chasing a weird install state can grep
    ``journalctl --user`` without ``terok setup`` exploding.  The
    return value lets :func:`_install_via_xdg_utils` decide whether to
    hand off to the manual fallback.
    """
    found = shutil.which(binary)
    if not found:  # pragma: no cover — gated by _xdg_utils_available
        return False
    # nosec B603 — argv is our own literal binary path plus subcommand/arg tokens.
    try:
        result = subprocess.run(  # noqa: S603  # nosec B603
            [found, *args],
            check=False,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("%s %s failed: %s", binary, args, exc)
        return False
    if result.returncode != 0:
        _log.debug(
            "%s %s exited with %d: %s",
            binary,
            args,
            result.returncode,
            (result.stderr or b"").decode(errors="replace").strip(),
        )
        return False
    return True


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
        result = subprocess.run(  # noqa: S603  # nosec B603
            [found, *[str(a) for a in args]],
            check=False,
            capture_output=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _log.debug("%s refresh failed: %s", binary, exc)
        return
    if result.returncode != 0:
        # Same DEBUG trail as _run_xdg — ``check=False`` keeps us quiet,
        # the log makes the failure diagnosable after the fact.
        _log.debug(
            "%s exited with %d: %s",
            binary,
            result.returncode,
            (result.stderr or b"").decode(errors="replace").strip(),
        )
