# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok setup`` — global bootstrap command.

Setup now delegates the service stack (shield + vault + gate + clearance)
to :func:`terok_executor.ensure_sandbox_ready`; terok's own phases
shrink to desktop-entry install.  Every service-level assertion now
lives in the executor / sandbox test suites.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from terok.cli.commands.setup import cmd_setup, dispatch

# ── dispatch wiring ──────────────────────────────────────────────────


def test_dispatch_returns_false_for_other_cmds() -> None:
    import argparse

    ns = argparse.Namespace(cmd="not-setup")
    assert dispatch(ns) is False


def test_dispatch_invokes_cmd_setup_with_flag() -> None:
    import argparse

    ns = argparse.Namespace(
        cmd="setup",
        no_desktop_entry=True,
        no_images=False,
        base="ubuntu:24.04",
        family=None,
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        assert dispatch(ns) is True
    mock.assert_called_once_with(
        no_desktop_entry=True,
        no_images=False,
        base="ubuntu:24.04",
        family=None,
    )


def test_dispatch_forwards_image_flags() -> None:
    """``--no-images`` / ``--base`` / ``--family`` travel through the dispatcher as kwargs."""
    import argparse

    ns = argparse.Namespace(
        cmd="setup",
        no_desktop_entry=False,
        no_images=True,
        base="fedora:43",
        family="rpm",
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        dispatch(ns)
    mock.assert_called_once_with(
        no_desktop_entry=False,
        no_images=True,
        base="fedora:43",
        family="rpm",
    )


# ── cmd_setup orchestration ──────────────────────────────────────────


class TestCmdSetup:
    """``cmd_setup`` composes sandbox-ready + image build + desktop-entry in that order."""

    def test_happy_path_runs_all_phases(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("terok_executor.ensure_sandbox_ready") as sandbox,
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
        ):
            cmd_setup()
        sandbox.assert_called_once()
        images.assert_called_once_with(base_image="ubuntu:24.04", family=None)
        desktop.assert_called_once()
        assert "Setup complete" in capsys.readouterr().out

    def test_no_desktop_entry_skips_desktop_phase(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry") as desktop,
        ):
            cmd_setup(no_desktop_entry=True)
        desktop.assert_not_called()

    def test_no_images_skips_image_phase(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``--no-images`` keeps setup fast on management-only hosts."""
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            cmd_setup(no_images=True)
        images.assert_not_called()

    def test_base_and_family_forwarded_to_factory(self) -> None:
        """``--base`` + ``--family`` thread through to :func:`build_base_images`."""
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            cmd_setup(base="fedora:43", family="rpm")
        images.assert_called_once_with(base_image="fedora:43", family="rpm")

    def test_image_build_error_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """A ``BuildError`` from the factory surfaces as a FAIL stage line and exit 1."""
        from terok_executor import BuildError

        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch(
                "terok_executor.build_base_images",
                side_effect=BuildError("dockerfile parse error"),
            ),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_setup()
        assert exc.value.code == 1
        assert "Image build failed" in capsys.readouterr().out

    def test_sandbox_failure_skips_image_phase(self) -> None:
        """When the service stack breaks, don't spend minutes building images on a broken host."""
        with (
            patch("terok_executor.ensure_sandbox_ready", side_effect=SystemExit(1)),
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            with pytest.raises(SystemExit):
                cmd_setup()
        images.assert_not_called()

    def test_sandbox_failure_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``ensure_sandbox_ready`` raising ``SystemExit`` is reported + propagates exit 1."""
        with (
            patch("terok_executor.ensure_sandbox_ready", side_effect=SystemExit(1)),
            patch("terok_executor.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            with pytest.raises(SystemExit) as exc:
                cmd_setup()
        assert exc.value.code == 1
        assert "Setup failed" in capsys.readouterr().out

    def test_sandbox_failure_still_runs_desktop_phase(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A sandbox failure must not short-circuit the desktop-entry phase.

        The desktop entry is independent of the sandbox; an operator
        with a broken sandbox install (missing SELinux policy, say)
        should still get their application launcher so the next
        ``terok setup`` re-run from the menu works.
        """
        with (
            patch("terok_executor.ensure_sandbox_ready", side_effect=SystemExit(1)),
            patch("terok_executor.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
        ):
            with pytest.raises(SystemExit):
                cmd_setup()
        desktop.assert_called_once()

    def test_desktop_failure_reports_warn(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Desktop entry failing is a WARN, not a FAIL — doesn't flip exit code."""
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=False),
        ):
            cmd_setup()  # no SystemExit
        out = capsys.readouterr().out
        assert "reported errors" in out


# ── Desktop entry phase ──────────────────────────────────────────────


class TestEnsureDesktopEntry:
    """The one phase terok still owns: XDG ``.desktop`` + icon install."""

    def test_xdg_utils_backend_reports_ok(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                return_value=DesktopBackend.XDG_UTILS,
            ),
        ):
            assert _ensure_desktop_entry() is True
        assert "ok" in capsys.readouterr().out

    def test_fallback_backend_reports_warn(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Built-in fallback when ``xdg-utils`` isn't on PATH — WARN, still True."""
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                return_value=DesktopBackend.FALLBACK,
            ),
        ):
            assert _ensure_desktop_entry() is True
        assert "WARN" in capsys.readouterr().out

    def test_install_raises_reports_fail(self, capsys: pytest.CaptureFixture[str]) -> None:
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                side_effect=PermissionError("read-only xdg dir"),
            ),
        ):
            assert _ensure_desktop_entry() is False
        assert "FAIL" in capsys.readouterr().out

    def test_missing_binary_falls_back_to_bare_name(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """pipx install → terok-tui may not be on PATH at setup time; install the unit anyway."""
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        captured_bin_path: list[str] = []

        def _record(bin_path: str) -> DesktopBackend:
            captured_bin_path.append(bin_path)
            return DesktopBackend.XDG_UTILS

        with (
            patch("terok.cli.commands.setup.shutil.which", return_value=None),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                side_effect=_record,
            ),
        ):
            _ensure_desktop_entry()
        assert captured_bin_path == ["terok-tui"]
