# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Textual-native new-project wizard screens.

These drive the three modal screens via Textual's built-in ``Pilot``
harness.  The heavy semantic tests (question schema, validation,
rendering) live in ``tests/unit/lib/test_wizard.py`` — this module
just confirms the screens wire the right widgets to the shared
``validate_answer`` and dismiss with the expected results.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App
from textual.widgets import Input, Label, RadioButton, RadioSet, Static, TextArea

from terok.lib.domain.wizards.new_project import QUESTIONS, Question
from terok.tui.wizard_screens import ProjectReviewScreen, WizardFormScreen


def _question(key: str) -> Question:
    for q in QUESTIONS:
        if q.key == key:
            return q
    raise AssertionError(f"No question with key {key!r}")


_SENTINEL_PENDING = object()


class _WizardHost(App):
    """Minimal test host that pushes a screen and stashes its dismissal result.

    Uses the callback form of ``push_screen`` because ``push_screen_wait``
    requires a running worker — Textual's ``run_test`` does not provide
    one out of the box.
    """

    def __init__(self, screen) -> None:
        super().__init__()
        self._screen_to_push = screen
        self.result: object = _SENTINEL_PENDING

    def on_mount(self) -> None:
        self.push_screen(self._screen_to_push, self._capture)

    def _capture(self, result: object) -> None:
        self.result = result


# ── WizardFormScreen ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wizard_form_cancel_dismisses_with_none() -> None:
    """Pressing Cancel dismisses the form with ``None``."""
    app = _WizardHost(WizardFormScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#wizard-form-cancel")
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_wizard_form_submit_blocks_on_validation_error() -> None:
    """Empty required project_id surfaces the shared ``validate_answer`` message."""
    app = _WizardHost(WizardFormScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        # Leave project_id empty and click Create — should NOT dismiss.
        await pilot.click("#wizard-form-create")
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, WizardFormScreen)
        error_label = screen.query_one("#wizard-error-project_id", Label)
        rendered = error_label.render()
        assert "required" in str(rendered).lower()
    # Still showing the form when the test exited — no dismissal.
    assert app.result is _SENTINEL_PENDING


@pytest.mark.asyncio
async def test_wizard_form_submit_returns_collected_dict() -> None:
    """Valid inputs across every question dismiss with a complete values dict."""
    app = _WizardHost(WizardFormScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        # Fill project_id so validation passes.
        pid_input = app.screen.query_one("#wizard-field-project_id", Input)
        pid_input.value = "demo-proj"
        # Leave upstream + branch + snippet empty (all optional).
        await pilot.click("#wizard-form-create")
        await pilot.pause()
    assert isinstance(app.result, dict)
    assert app.result["project_id"] == "demo-proj"
    # First radio button is pre-selected on each choice; confirm it mapped
    # through to the slug, not the label.
    assert app.result["security_class"] == _question("security_class").choices[0][0]
    assert app.result["base"] == _question("base").choices[0][0]
    # Optional fields default to empty strings.
    assert app.result["upstream_url"] == ""
    assert app.result["default_branch"] == ""
    assert app.result["user_snippet"] == ""


@pytest.mark.asyncio
async def test_wizard_form_lowercases_project_id() -> None:
    """``str.lower`` transform runs before validation on submit."""
    app = _WizardHost(WizardFormScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.screen.query_one("#wizard-field-project_id", Input).value = "MixedCaseID"
        await pilot.click("#wizard-form-create")
        await pilot.pause()
    assert isinstance(app.result, dict)
    assert app.result["project_id"] == "mixedcaseid"


@pytest.mark.asyncio
async def test_wizard_form_radio_selection_picks_other_option() -> None:
    """Selecting the second radio on a choice question flips the dismissed slug."""
    app = _WizardHost(WizardFormScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        # Select the second option of security_class (gatekeeping).
        sec_radioset = app.screen.query_one("#wizard-field-security_class", RadioSet)
        buttons = list(sec_radioset.query(RadioButton))
        buttons[1].value = True
        app.screen.query_one("#wizard-field-project_id", Input).value = "p1"
        await pilot.click("#wizard-form-create")
        await pilot.pause()
    assert isinstance(app.result, dict)
    assert app.result["security_class"] == _question("security_class").choices[1][0]


# ── ProjectReviewScreen ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_review_screen_back_dismisses_with_sentinel() -> None:
    """Back button returns ``REVIEW_BACK`` — caller re-opens form with prefill."""
    from terok.tui.wizard_screens import REVIEW_BACK

    app = _WizardHost(ProjectReviewScreen("demo", "project:\n  id: demo\n"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#wizard-review-back")
        await pilot.pause()
    assert app.result is REVIEW_BACK


@pytest.mark.asyncio
async def test_review_screen_cancel_action_abandons_with_none() -> None:
    """The ``cancel`` action (Esc binding) abandons the wizard — distinct from Back."""
    app = _WizardHost(ProjectReviewScreen("demo", "project:\n  id: demo\n"))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Pilot key-press forwarding to modal bindings is flaky across
        # Textual versions; invoke the bound action directly — the
        # binding itself is covered by the screen's BINDINGS list.
        screen = app.screen
        assert isinstance(screen, ProjectReviewScreen)
        screen.action_cancel()
        await pilot.pause()
    assert app.result is None


@pytest.mark.asyncio
async def test_review_screen_initialize_returns_edited_yaml() -> None:
    """Edits to the TextArea flow through to the dismissed string."""
    app = _WizardHost(ProjectReviewScreen("demo", "project:\n  id: demo\n"))
    async with app.run_test() as pilot:
        await pilot.pause()
        ta = app.screen.query_one("#wizard-review-yaml", TextArea)
        ta.text = "project:\n  id: demo\n  edited: true\n"
        await pilot.click("#wizard-review-init")
        await pilot.pause()
    assert app.result == "project:\n  id: demo\n  edited: true\n"


# ── Registry-level smoke ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_form_renders_one_widget_per_question() -> None:
    """Each declared question produces a field with the expected ID."""
    app = _WizardHost(WizardFormScreen())
    async with app.run_test() as pilot:
        await pilot.pause()
        for q in QUESTIONS:
            # The field widget exists and has the deterministic ID the
            # read_raw loop relies on.
            assert app.screen.query_one(f"#wizard-field-{q.key}")


def test_touched_wizard_yaml_survives_roundtrip() -> None:
    """Integration: form dict → render → write_project_yaml round-trips content."""
    from terok.lib.domain.wizards.new_project import (
        render_project_yaml,
        write_project_yaml,
    )

    values = {
        "security_class": "online",
        "base": "ubuntu",
        "project_id": "roundtrip",
        "upstream_url": "",
        "default_branch": "main",
        "user_snippet": "",
    }
    rendered = render_project_yaml(values)
    with (
        tempfile.TemporaryDirectory() as td,
        patch("terok.lib.domain.wizards.new_project.user_projects_dir", return_value=Path(td)),
    ):
        path = write_project_yaml("roundtrip", rendered, overwrite=True)
        assert path.read_text() == rendered


# ── InitProgressScreen — error paths ──────────────────────────────────


@pytest.mark.asyncio
async def test_init_screen_write_failure_surfaces_in_log() -> None:
    """A failed ``write_project_yaml`` renders the error and enables Close.

    The worker's facade pipeline is *not* entered — a write that fails
    is a stale project.yml waiting to happen, and downstream steps
    would just fail secondarily on a confusing error.
    """
    from terok.tui.wizard_screens import InitOutcome, InitProgressScreen

    def _boom(*_args, **_kwargs):
        raise OSError("read-only filesystem")

    app = _WizardHost(InitProgressScreen("demo", "project:\n  id: demo\n"))
    with (
        patch("terok.tui.wizard_screens.write_project_yaml", side_effect=_boom),
        patch("terok.tui.wizard_screens.InitProgressScreen._run_init") as mock_run_init,
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, InitProgressScreen)
            close_button = screen.query_one("#wizard-init-close")
            # Close button is enabled so the user can dismiss cleanly.
            assert close_button.disabled is False
            # The worker was never invoked on the failed-write path.
            mock_run_init.assert_not_called()
            # The outcome is FAILED — a write error is a real failure.
            assert screen._outcome is InitOutcome.FAILED


@pytest.mark.asyncio
async def test_init_screen_decline_overwrite_distinguishes_from_failure() -> None:
    """User-declined overwrite sets ``DECLINED`` — not FAILED — so the caller doesn't warn.

    The InitProgressScreen only pushes the confirm modal when the
    project.yml already exists, so we fabricate that via a patched
    ``_existing_project_yaml_path`` and a patched overwrite confirmer
    that returns False.  No filesystem write happens on this path.
    """
    from terok.tui.wizard_screens import InitOutcome, InitProgressScreen

    app = _WizardHost(InitProgressScreen("demo", "project:\n  id: demo\n"))
    with (
        patch.object(
            InitProgressScreen,
            "_existing_project_yaml_path",
            return_value=Path("/tmp/terok-testing/demo/project.yml"),
        ),
        patch.object(InitProgressScreen, "_confirm_overwrite", return_value=False),
        patch("terok.tui.wizard_screens.write_project_yaml") as mock_write,
        patch.object(InitProgressScreen, "_run_init") as mock_run_init,
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, InitProgressScreen)
            # Neither the write nor the worker ran.
            mock_write.assert_not_called()
            mock_run_init.assert_not_called()
            # Outcome distinguishes decline from failure.
            assert screen._outcome is InitOutcome.DECLINED
            # Close button is enabled with the neutral variant.
            close_button = screen.query_one("#wizard-init-close")
            assert close_button.disabled is False
            assert close_button.variant == "default"


# ── Form prefill (Back round-trip preservation) ──────────────────────


@pytest.mark.asyncio
async def test_form_prefill_populates_widgets() -> None:
    """Re-opening the form with an *initial* dict restores every field."""
    initial = {
        "security_class": _question("security_class").choices[1][0],  # second choice
        "base": _question("base").choices[2][0],
        "project_id": "kept-from-back",
        "upstream_url": "https://example.com/r.git",
        "default_branch": "dev",
        "user_snippet": "RUN echo hi",
    }
    app = _WizardHost(WizardFormScreen(initial=initial))
    async with app.run_test() as pilot:
        await pilot.pause()
        # Text and editor fields carry the prefill verbatim.
        assert app.screen.query_one("#wizard-field-project_id", Input).value == "kept-from-back"
        assert (
            app.screen.query_one("#wizard-field-upstream_url", Input).value
            == "https://example.com/r.git"
        )
        assert app.screen.query_one("#wizard-field-default_branch", Input).value == "dev"
        assert app.screen.query_one("#wizard-field-user_snippet", TextArea).text == "RUN echo hi"
        # Radio preselection picks the prefilled slug, not the first option.
        sec_rs = app.screen.query_one("#wizard-field-security_class", RadioSet)
        pressed = sec_rs.pressed_button
        assert pressed is not None
        assert pressed.name == initial["security_class"]


# ── Subprocess isolation helpers ──────────────────────────────────────


def test_run_isolated_propagates_nonzero_as_runtime_error() -> None:
    """A crashing child surfaces its stderr tail in the raised message."""
    from terok.tui.wizard_screens import _run_isolated

    with pytest.raises(RuntimeError) as excinfo:
        _run_isolated(
            "import sys; sys.stderr.write('kaboom\\n'); sys.exit(3)",
            label="toy step",
        )
    msg = str(excinfo.value)
    assert "toy step exited with code 3" in msg
    assert "kaboom" in msg


def test_gate_sync_in_subprocess_returns_result_dict() -> None:
    """The helper shuttles the child's result dict back to the parent verbatim.

    We patch the facade's ``make_git_gate`` inside the *child* process by
    pre-seeding a ``conftest``-style sitecustomize — not worth the
    trouble.  Instead, we verify the helper's tempfile-roundtrip path
    with a trivial child body that writes a known dict to the result
    file directly.
    """
    from unittest.mock import patch as upatch

    from terok.tui.wizard_screens import _gate_sync_in_subprocess

    sentinel = {"success": True, "upstream_url": "https://example.com/r.git", "errors": []}

    def _fake_run_isolated(body: str, *, label: str) -> None:  # noqa: ARG001
        # Extract the result path from the body — it's the last argument
        # passed to ``open(...)``.
        import json
        import re

        match = re.search(r"open\((['\"])(?P<p>[^'\"]+)\1, 'w'\)", body)
        assert match, f"body missing result path: {body!r}"
        Path(match.group("p")).write_text(json.dumps(sentinel), encoding="utf-8")

    with upatch("terok.tui.wizard_screens._run_isolated", side_effect=_fake_run_isolated):
        assert _gate_sync_in_subprocess("demo") == sentinel


# ── SSH fingerprint display ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_ssh_panel_shows_fingerprint_alongside_pubkey() -> None:
    """When the ssh panel pops up, fingerprint + comment render beside the pubkey.

    GitHub and friends only show the SHA256 fingerprint on the deploy
    key settings page once the key is pasted — by then the raw pubkey
    is hidden.  Displaying the fingerprint at mint time is what lets
    the user verify they registered the right key.
    """
    import asyncio as _asyncio

    from terok.tui.wizard_screens import InitProgressScreen

    # Stub every downstream step so ``_run_init`` gets as far as
    # populating the SSH panel, then parks on ``_ssh_continue``.
    minted = {
        "key_id": 42,
        "key_type": "ed25519",
        "fingerprint": "SHA256:abcdefGHIJKLmnop1234567890",
        "comment": "terok@demo",
        "public_line": "ssh-ed25519 AAAAFAKE terok@demo",
    }
    app = _WizardHost(InitProgressScreen("demo", "project:\n  id: demo\n"))
    with (
        patch.object(InitProgressScreen, "_existing_project_yaml_path", return_value=None),
        patch("terok.tui.wizard_screens.write_project_yaml"),
        patch("terok.tui.wizard_screens.project_needs_key_registration", return_value=True),
        patch("terok.lib.domain.facade.provision_ssh_key", return_value=minted),
        patch("terok.lib.domain.facade.summarize_ssh_init"),
    ):
        async with app.run_test() as pilot:
            # Give the worker a chance to reach the ssh_continue wait.
            for _ in range(20):
                await pilot.pause()
                await _asyncio.sleep(0)
                screen = app.screen
                if not isinstance(screen, InitProgressScreen):
                    break
                panel = screen.query_one("#wizard-init-ssh-key")
                if panel.styles.display == "block":
                    break
            screen = app.screen
            assert isinstance(screen, InitProgressScreen)
            pubkey = screen.query_one("#wizard-init-ssh-pubkey", Static)
            fingerprint = screen.query_one("#wizard-init-ssh-fingerprint", Static)
            assert minted["public_line"] in str(pubkey.render())
            assert "SHA256:abcdefGHIJKLmnop1234567890" in str(fingerprint.render())
            assert "terok@demo" in str(fingerprint.render())
            # Unblock the worker so the test exits cleanly.
            screen._ssh_continue.set()
            await pilot.pause()


# ── Esc-cancels-wizard ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_init_screen_esc_cancels_mid_run() -> None:
    """Esc during an in-flight init sets CANCELLED and tears down cleanly.

    Drives the worker to the SSH-key ``continue`` gate — where it
    parks on ``await self._ssh_continue.wait()`` — then presses Esc
    to trigger :meth:`InitProgressScreen.action_cancel`.  Two things
    must hold:

    * The outcome is :attr:`InitOutcome.CANCELLED`, not FAILED — a
      deliberate cancel is not a failure.
    * No teardown exception is raised.  The worker's ``finally``
      runs after the screen has been dismissed; the cleanup path
      early-returns on ``CANCELLED`` and tolerates a missing Close
      button via ``NoMatches``.  Either defence regressing would
      surface here as an uncaught worker error.
    """
    import asyncio as _asyncio

    from terok.tui.wizard_screens import InitOutcome, InitProgressScreen

    minted = {
        "key_id": 7,
        "key_type": "ed25519",
        "fingerprint": "SHA256:cancelme",
        "comment": "terok@cancel",
        "public_line": "ssh-ed25519 AAAAFAKE terok@cancel",
    }
    app = _WizardHost(InitProgressScreen("demo", "project:\n  id: demo\n"))
    with (
        patch.object(InitProgressScreen, "_existing_project_yaml_path", return_value=None),
        patch("terok.tui.wizard_screens.write_project_yaml"),
        patch("terok.tui.wizard_screens.project_needs_key_registration", return_value=True),
        patch("terok.lib.domain.facade.provision_ssh_key", return_value=minted),
        patch("terok.lib.domain.facade.summarize_ssh_init"),
    ):
        async with app.run_test() as pilot:
            # Let the worker reach the ssh_continue.wait() park state —
            # same readiness probe as the fingerprint test above.
            for _ in range(20):
                await pilot.pause()
                await _asyncio.sleep(0)
                screen = app.screen
                if not isinstance(screen, InitProgressScreen):
                    break
                panel = screen.query_one("#wizard-init-ssh-key")
                if panel.styles.display == "block":
                    break
            screen = app.screen
            assert isinstance(screen, InitProgressScreen)

            await pilot.press("escape")
            # Give the worker-cancel + dismiss + finally unwind time
            # to fully settle before we assert.
            for _ in range(10):
                await pilot.pause()
                await _asyncio.sleep(0)

            assert screen._outcome is InitOutcome.CANCELLED
            # The host's capture callback receives the dismissed outcome —
            # confirms dismiss() actually fired (not just the outcome set).
            assert app.result is InitOutcome.CANCELLED
