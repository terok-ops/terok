# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install ``terok-clearance-notifier.service`` into the user's systemd tree.

Renders the unit template with the resolved ``ExecStart`` path, writes
it to ``$XDG_CONFIG_HOME/systemd/user/``, and asks systemd to reload.
Per-argv-token quoting protects launcher paths that contain spaces
(``/home/me/My Tools/bin/python``) from being split at ``ExecStart=``
render time.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404 — systemctl is a trusted host binary
from importlib import resources as importlib_resources
from pathlib import Path

UNIT_NAME = "terok-clearance-notifier.service"

_UNIT_VERSION = 2
"""Bump when the unit template's semantics change.

Substituted into ``{{UNIT_VERSION}}`` at render time so
:func:`check_units_outdated` can tell a fresh install from an older
generation — mirrors the pattern used by ``terok-gate``,
``terok-vault``, and ``terok-dbus``.
"""

_VERSION_MARKER_PREFIX = "# terok-clearance-notifier-version:"
"""Parser key for :func:`read_installed_unit_version`."""


def install_service(bin_path: Path | list[str]) -> Path:
    """Render the unit template, write it to the user systemd dir, reload.

    Args:
        bin_path: A ``Path`` to the notifier launcher, or a ``list[str]``
            argv (module-fallback form, e.g. ``[sys.executable, "-m",
            "terok.clearance.notifier.app"]``).  Tokens are quoted
            individually so a path with spaces stays one argv element.

    Returns:
        The on-disk path the unit was written to.
    """
    template = _read_template()
    rendered = template.replace("{{BIN}}", _render_exec_start(bin_path)).replace(
        "{{UNIT_VERSION}}", str(_UNIT_VERSION)
    )
    dest = default_unit_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered, encoding="utf-8")
    _daemon_reload()
    return dest


def default_unit_path() -> Path:
    """Return the canonical user-systemd path for the notifier unit."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(xdg) if xdg else Path.home() / ".config"
    return config_home / "systemd" / "user" / UNIT_NAME


def _render_exec_start(bin_path: Path | list[str]) -> str:
    """Quote each argv token so spaces inside one token survive systemd's tokeniser."""
    tokens = [str(bin_path)] if isinstance(bin_path, Path) else [str(t) for t in bin_path]
    for token in tokens:
        if any(ch in token for ch in ("\n", "\r")):
            raise ValueError(f"bin_path token is not safe to embed in ExecStart=: {token!r}")
    return " ".join(_quote_exec_token(t) for t in tokens)


def _quote_exec_token(token: str) -> str:
    """Wrap *token* in systemd double-quotes when it contains whitespace."""
    if any(ch.isspace() for ch in token):
        return f'"{_systemd_quote(token)}"'
    return _systemd_quote(token)


def _systemd_quote(value: str) -> str:
    r"""Escape ``"`` and ``\`` so *value* can live safely inside a quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _read_template() -> str:
    """Load the unit template shipped with the ``terok`` package."""
    source = (
        importlib_resources.files("terok")
        .joinpath("resources")
        .joinpath("systemd")
        .joinpath(UNIT_NAME)
    )
    return source.read_text(encoding="utf-8")


def _daemon_reload() -> None:
    """Ask the user's systemd to re-read its unit files.

    ``systemctl`` absent (container / CI host without systemd) is the
    silent-skip path; every other failure raises so ``install_service``
    surfaces an obvious error instead of reporting success while leaving
    the new unit invisible to systemd.
    """
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    result = subprocess.run(  # nosec B603 — fixed argv, no shell
        [systemctl, "--user", "daemon-reload"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or "<no output>"
        raise RuntimeError(
            f"systemctl --user daemon-reload failed (exit {result.returncode}): {stderr}"
        )


def read_installed_unit_version() -> int | None:
    """Return the ``# terok-clearance-notifier-version:`` stamp, or ``None``.

    ``None`` means either the unit isn't installed or it predates the
    version marker (nothing shipped this file before v1, but sickbay
    still treats ``None`` as "needs rerun" because the notifier itself
    is new in the varlink-era release).
    """
    try:
        unit = default_unit_path().read_text(encoding="utf-8")
    except OSError:
        return None
    for line in unit.splitlines():
        if line.startswith(_VERSION_MARKER_PREFIX):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def check_units_outdated() -> str | None:
    """Return a one-line drift warning if the installed unit is stale, else ``None``.

    ``None`` when nothing is installed — callers (sickbay) treat that
    as "user hasn't run terok setup yet" separately; the goal here is
    to distinguish *installed-but-old* from *installed-and-current*.
    """
    if not default_unit_path().is_file():
        return None
    installed = read_installed_unit_version()
    if installed is None or installed < _UNIT_VERSION:
        installed_label = "unversioned" if installed is None else f"v{installed}"
        return (
            f"{UNIT_NAME} is outdated "
            f"(installed {installed_label}, expected v{_UNIT_VERSION}) — rerun `terok setup`."
        )
    return None
