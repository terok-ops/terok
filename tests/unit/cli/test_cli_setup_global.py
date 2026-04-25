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
        install_desktop_entry=False,
        with_images=None,
        family=None,
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        assert dispatch(ns) is True
    mock.assert_called_once_with(
        no_desktop_entry=True,
        install_desktop_entry=False,
        with_images=None,
        family=None,
    )


def test_dispatch_forwards_install_desktop_entry() -> None:
    """``--install-desktop-entry`` travels through the dispatcher as a kwarg."""
    import argparse

    ns = argparse.Namespace(
        cmd="setup",
        no_desktop_entry=False,
        install_desktop_entry=True,
        with_images=None,
        family=None,
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        dispatch(ns)
    mock.assert_called_once_with(
        no_desktop_entry=False,
        install_desktop_entry=True,
        with_images=None,
        family=None,
    )


def test_dispatch_forwards_with_images_and_family() -> None:
    """``--with-images`` + ``--family`` travel through the dispatcher as kwargs."""
    import argparse

    ns = argparse.Namespace(
        cmd="setup",
        no_desktop_entry=False,
        install_desktop_entry=False,
        with_images="fedora:43",
        family="rpm",
    )
    with patch("terok.cli.commands.setup.cmd_setup") as mock:
        dispatch(ns)
    mock.assert_called_once_with(
        no_desktop_entry=False,
        install_desktop_entry=False,
        with_images="fedora:43",
        family="rpm",
    )


# ── cmd_setup orchestration ──────────────────────────────────────────


class TestCmdSetup:
    """``cmd_setup`` runs sandbox-ready + desktop-entry by default; images are opt-in."""

    def test_default_skips_image_build(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Normal ``terok setup`` doesn't touch the image factory.

        Base images are a per-project decision (each ``project.yml``
        declares its own ``image.base_image``); at host-setup time
        there's nothing sensible to pre-build.  L0/L1 materialises
        lazily on first ``terok task run`` / ``terok project init``.
        """
        with (
            patch("terok_executor.ensure_sandbox_ready") as sandbox,
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
        ):
            cmd_setup()
        sandbox.assert_called_once()
        images.assert_not_called()
        desktop.assert_called_once()
        assert "Setup complete" in capsys.readouterr().out

    def test_no_desktop_entry_resolves_to_skip_policy(self) -> None:
        """``--no-desktop-entry`` resolves to ``policy="skip"`` for ``_ensure_desktop_entry``.

        The phase is still invoked — the silent-skip branch lives inside
        ``_ensure_desktop_entry`` so the call site stays uniform — but
        the resolved policy is ``"skip"``.
        """
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
        ):
            cmd_setup(no_desktop_entry=True)
        desktop.assert_called_once_with(policy="skip")

    def test_install_desktop_entry_resolves_to_install_policy(self) -> None:
        """``--install-desktop-entry`` resolves to ``policy="install"``."""
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
        ):
            cmd_setup(install_desktop_entry=True)
        desktop.assert_called_once_with(policy="install")

    def test_default_policy_comes_from_config(self) -> None:
        """Without CLI flags, the policy comes from ``tui.desktop_entry`` (default ``auto``)."""
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images"),
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True) as desktop,
            patch(
                "terok.lib.core.config.get_tui_desktop_entry",
                return_value="install",
            ),
        ):
            cmd_setup()
        desktop.assert_called_once_with(policy="install")

    def test_with_images_builds_requested_base(self) -> None:
        """``--with-images=ubuntu:24.04`` triggers the factory with that base + auto-detected family."""
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            cmd_setup(with_images="ubuntu:24.04")
        images.assert_called_once_with(base_image="ubuntu:24.04", family=None)

    def test_with_images_plus_family_override(self) -> None:
        """``--family`` overrides auto-detection when paired with ``--with-images``."""
        with (
            patch("terok_executor.ensure_sandbox_ready"),
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            cmd_setup(with_images="my-registry.example.com/odd-base:1.0", family="rpm")
        images.assert_called_once_with(
            base_image="my-registry.example.com/odd-base:1.0", family="rpm"
        )

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
                cmd_setup(with_images="ubuntu:24.04")
        assert exc.value.code == 1
        assert "Image build failed" in capsys.readouterr().out

    def test_sandbox_failure_skips_requested_image_phase(self) -> None:
        """``--with-images`` is still suppressed when the service stack is broken.

        No point burning minutes on L0/L1 against a host that can't
        yet mount it; the user needs to fix the sandbox install first.
        """
        with (
            patch("terok_executor.ensure_sandbox_ready", side_effect=SystemExit(1)),
            patch("terok_executor.build_base_images") as images,
            patch("terok.cli.commands.setup._ensure_desktop_entry", return_value=True),
        ):
            with pytest.raises(SystemExit):
                cmd_setup(with_images="ubuntu:24.04")
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

    def test_skip_policy_emits_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``policy="skip"`` returns True silently — no stage line, no install call."""
        from terok.cli.commands.setup import _ensure_desktop_entry

        with patch("terok.cli.commands._desktop_entry.install_desktop_entry") as do_install:
            assert _ensure_desktop_entry(policy="skip") is True
        do_install.assert_not_called()
        assert capsys.readouterr().out == ""

    def test_auto_without_xdg_utils_warns_with_hints(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``auto`` + missing xdg-utils → WARN that names both escape hatches."""
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch(
                "terok.cli.commands._desktop_entry.xdg_utils_available",
                return_value=False,
            ),
            patch("terok.cli.commands._desktop_entry.install_desktop_entry") as do_install,
        ):
            assert _ensure_desktop_entry(policy="auto") is True
        do_install.assert_not_called()
        out = capsys.readouterr().out
        assert "WARN" in out
        assert "--install-desktop-entry" in out
        assert "tui.desktop_entry: skip" in out

    def test_auto_with_xdg_utils_installs(self, capsys: pytest.CaptureFixture[str]) -> None:
        """``auto`` + xdg-utils present → install via xdg-utils, OK stage line."""
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch(
                "terok.cli.commands._desktop_entry.xdg_utils_available",
                return_value=True,
            ),
            patch("terok.cli.commands.setup.shutil.which", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                return_value=DesktopBackend.XDG_UTILS,
            ) as do_install,
        ):
            assert _ensure_desktop_entry(policy="auto") is True
        do_install.assert_called_once()
        assert "ok" in capsys.readouterr().out

    def test_install_uses_fallback_when_xdg_utils_missing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``install`` always installs — fallback backend when xdg-utils is missing."""
        from terok.cli.commands._desktop_entry import DesktopBackend
        from terok.cli.commands.setup import _ensure_desktop_entry

        with (
            patch(
                "terok.cli.commands._desktop_entry.xdg_utils_available",
                return_value=False,
            ),
            patch("terok.cli.commands.setup.shutil.which", return_value="/usr/bin/terok-tui"),
            patch(
                "terok.cli.commands._desktop_entry.install_desktop_entry",
                return_value=DesktopBackend.FALLBACK,
            ) as do_install,
        ):
            assert _ensure_desktop_entry(policy="install") is True
        do_install.assert_called_once()
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
            assert _ensure_desktop_entry(policy="install") is False
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
            _ensure_desktop_entry(policy="install")
        assert captured_bin_path == ["terok-tui"]


class TestResolveDesktopPolicy:
    """``--no-desktop-entry`` / ``--install-desktop-entry`` win over the config key."""

    def test_no_flags_returns_config(self) -> None:
        from terok.cli.commands.setup import _resolve_desktop_policy

        with patch(
            "terok.lib.core.config.get_tui_desktop_entry",
            return_value="auto",
        ):
            assert (
                _resolve_desktop_policy(no_desktop_entry=False, install_desktop_entry=False)
                == "auto"
            )

    def test_no_desktop_entry_overrides_config(self) -> None:
        from terok.cli.commands.setup import _resolve_desktop_policy

        with patch(
            "terok.lib.core.config.get_tui_desktop_entry",
            return_value="install",
        ):
            assert (
                _resolve_desktop_policy(no_desktop_entry=True, install_desktop_entry=False)
                == "skip"
            )

    def test_install_desktop_entry_overrides_config(self) -> None:
        from terok.cli.commands.setup import _resolve_desktop_policy

        with patch(
            "terok.lib.core.config.get_tui_desktop_entry",
            return_value="skip",
        ):
            assert (
                _resolve_desktop_policy(no_desktop_entry=False, install_desktop_entry=True)
                == "install"
            )
