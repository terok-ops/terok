# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Cheap stamp-based gate that runs before ``terok task run`` / ``task restart``.

Mirrors the executor-side contract (``terok-executor run`` exit codes
3 / 4; see sandbox setup-stamp primitive and epic terok-ai/terok#685
phase 6): scripts driving either entry point see the same signal for
"setup needed" vs "real task failure".
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from terok_sandbox import SetupVerdict

from terok.cli.commands.task import _setup_verdict_or_exit

# ── Verdict → exit-code mapping ───────────────────────────────────────


@pytest.mark.parametrize(
    ("verdict", "expected_fragment"),
    [
        pytest.param(SetupVerdict.FIRST_RUN, "no setup stamp found", id="first-run-exits-3"),
        pytest.param(
            SetupVerdict.STALE_AFTER_UPDATE,
            "package versions changed",
            id="stale-after-update-exits-3",
        ),
        pytest.param(SetupVerdict.STAMP_CORRUPT, "stamp is unreadable", id="stamp-corrupt-exits-3"),
    ],
)
def test_setup_needed_verdicts_all_exit_three(
    verdict: SetupVerdict,
    expected_fragment: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """FIRST_RUN / STALE_AFTER_UPDATE / STAMP_CORRUPT all collapse to exit 3 with a fix hint."""
    with patch("terok_sandbox.needs_setup", return_value=verdict):
        with pytest.raises(SystemExit) as excinfo:
            _setup_verdict_or_exit()
    assert excinfo.value.code == 3
    err = capsys.readouterr().err
    assert expected_fragment in err
    # Every "setup needed" path points at the canonical terok-side fix.
    assert "terok setup" in err


def test_downgrade_exits_four_with_named_packages(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """STALE_AFTER_DOWNGRADE refuses with exit 4, names the offending packages."""
    with (
        patch(
            "terok_sandbox.needs_setup",
            return_value=SetupVerdict.STALE_AFTER_DOWNGRADE,
        ),
        patch(
            "terok.cli.commands.task._name_downgraded_packages",
            return_value=["terok 0.8.1 → 0.8.0"],
        ),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _setup_verdict_or_exit()
    assert excinfo.value.code == 4
    err = capsys.readouterr().err
    assert "downgrade detected" in err
    assert "terok 0.8.1 → 0.8.0" in err
    # The refusal points at the deliberate-override path, not the easy fix.
    assert "rm" in err and "stamp" in err


def test_downgrade_falls_back_to_generic_when_diff_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the stamp can't be re-read for diffing, surface a generic refusal — never crash."""
    with (
        patch(
            "terok_sandbox.needs_setup",
            return_value=SetupVerdict.STALE_AFTER_DOWNGRADE,
        ),
        patch("terok.cli.commands.task._name_downgraded_packages", return_value=[]),
    ):
        with pytest.raises(SystemExit) as excinfo:
            _setup_verdict_or_exit()
    assert excinfo.value.code == 4
    assert "one or more packages" in capsys.readouterr().err


def test_ok_verdict_returns_silently() -> None:
    """OK is the happy path — no print, no exit, control flows back to the task runner."""
    with patch("terok_sandbox.needs_setup", return_value=SetupVerdict.OK):
        assert _setup_verdict_or_exit() is None


# ── _name_downgraded_packages helper ──────────────────────────────────


def test_name_downgraded_packages_lists_each_offender(tmp_path) -> None:
    """Helper compares stamped vs installed and names every package that regressed."""
    from terok.cli.commands.task import _name_downgraded_packages

    stamp = tmp_path / "setup.stamp"

    def fake_read(_path):
        return {"terok": "0.8.1", "terok-sandbox": "0.0.98", "terok-shield": "0.6.31"}

    def fake_installed():
        # terok downgraded, sandbox kept its version, shield gone entirely.
        return {"terok": "0.8.0", "terok-sandbox": "0.0.98"}

    out = _name_downgraded_packages(stamp, fake_read, fake_installed)
    assert "terok 0.8.1 → 0.8.0" in out
    assert "terok-shield (uninstalled)" in out
    # Equal versions don't get listed.
    assert not any(entry.startswith("terok-sandbox ") for entry in out)


def test_name_downgraded_packages_swallows_read_error(tmp_path) -> None:
    """A racing setup overwriting the stamp can't crash the diagnostic helper."""
    from terok.cli.commands.task import _name_downgraded_packages

    def boom(_path):
        raise RuntimeError("stamp went away mid-diff")

    out = _name_downgraded_packages(tmp_path / "x", boom, lambda: {})
    assert out == []
