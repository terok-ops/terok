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
    rendered = template.replace("{{BIN}}", _render_exec_start(bin_path))
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
    """Ask the user's systemd to re-read its unit files; silently skip if unavailable."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    subprocess.run(  # nosec B603 — fixed argv, no shell
        [systemctl, "--user", "daemon-reload"],
        check=False,
        capture_output=True,
        text=True,
    )
