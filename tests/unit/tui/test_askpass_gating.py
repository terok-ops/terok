# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Gating tests for the askpass integration — zero cost when opted out.

The :class:`InitProgressScreen._askpass_subprocess_env` helper is the
single choke point that decides whether a subprocess gets askpass env
plumbing.  These tests verify:

- ``ssh_use_personal=False`` returns ``None`` and the app's
  ``ensure_askpass_service`` is never called — so no unix socket is
  ever bound for users who don't opt in.
- ``ssh_use_personal=True`` calls ``ensure_askpass_service`` and
  returns an env dict containing both ``SSH_ASKPASS`` and
  ``TEROK_ASKPASS_SOCKET`` pointed at that service's paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from terok.tui.wizard_screens import InitProgressScreen


@dataclass
class _FakeProject:
    """Stand-in for :class:`ProjectConfig` — only the one attribute we read."""

    ssh_use_personal: bool


class _FakeService:
    """Stand-in for :class:`AskpassService` exposing the two properties the env builder reads."""

    def __init__(self, socket_path: Path, helper_bin: Path) -> None:
        self.socket_path = socket_path
        self.helper_bin = helper_bin


class _FakeApp:
    """Stand-in for :class:`TerokTUI` that counts ``ensure_askpass_service`` calls."""

    def __init__(self, service: _FakeService | None = None) -> None:
        self._service = service or _FakeService(Path("/run/x.sock"), Path("/usr/bin/terok-askpass"))
        self.ensure_calls = 0

    async def ensure_askpass_service(self) -> _FakeService:
        self.ensure_calls += 1
        return self._service


def _new_screen() -> InitProgressScreen:
    """Construct a bare :class:`InitProgressScreen` without Textual's setup machinery."""
    screen = InitProgressScreen.__new__(InitProgressScreen)
    screen._project_id = "demo"
    return screen


@pytest.mark.asyncio
async def test_env_is_none_when_project_opts_out() -> None:
    """Default ``ssh_use_personal=False`` skips service start *and* env injection."""
    app = _FakeApp()
    screen = _new_screen()

    with patch.object(InitProgressScreen, "app", new_callable=PropertyMock, return_value=app):
        env = await screen._askpass_subprocess_env(_FakeProject(ssh_use_personal=False))

    assert env is None
    assert app.ensure_calls == 0  # socket never bound


@pytest.mark.asyncio
async def test_env_is_built_when_project_opts_in(monkeypatch: pytest.MonkeyPatch) -> None:
    """``ssh_use_personal=True`` starts the service and injects the askpass vars."""
    # Strip any ambient SSH_ASKPASS / DISPLAY so we hit the "inject ours" branch.
    monkeypatch.delenv("SSH_ASKPASS", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    service = _FakeService(Path("/run/demo.sock"), Path("/usr/bin/terok-askpass"))
    app = _FakeApp(service)
    screen = _new_screen()

    with patch.object(InitProgressScreen, "app", new_callable=PropertyMock, return_value=app):
        env = await screen._askpass_subprocess_env(_FakeProject(ssh_use_personal=True))

    assert env is not None
    assert app.ensure_calls == 1
    assert env["SSH_ASKPASS"] == str(service.helper_bin)
    assert env["TEROK_ASKPASS_SOCKET"] == str(service.socket_path)
    assert env["SSH_ASKPASS_REQUIRE"] == "force"


@pytest.mark.asyncio
async def test_env_respects_existing_gui_askpass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt-in + usable GUI askpass → service is NOT started, env uses user's helper.

    This is the "zero cost when not needed" branch on the opt-in side
    of the fence — the user's desktop askpass (seahorse, gnome-keyring)
    handles the prompt natively, so we don't bother binding our
    socket.  Only ``SSH_ASKPASS_REQUIRE=force`` is added so OpenSSH
    still prefers the GUI helper over any ``/dev/tty`` fallback.
    """
    monkeypatch.setenv("SSH_ASKPASS", "/usr/libexec/seahorse-askpass")
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    app = _FakeApp()
    screen = _new_screen()

    with patch.object(InitProgressScreen, "app", new_callable=PropertyMock, return_value=app):
        env = await screen._askpass_subprocess_env(_FakeProject(ssh_use_personal=True))

    assert env is not None
    assert app.ensure_calls == 0  # no socket bound when GUI helper can render
    assert env["SSH_ASKPASS"] == "/usr/libexec/seahorse-askpass"
    assert "TEROK_ASKPASS_SOCKET" not in env
    assert env["SSH_ASKPASS_REQUIRE"] == "force"
