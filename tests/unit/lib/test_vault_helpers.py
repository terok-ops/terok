# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the thin helpers that bridge terok-main to the vault DB."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestScopeHasVaultKey:
    """``project_state._scope_has_vault_key`` reflects DB assignment state."""

    def test_returns_true_when_scope_has_keys(self) -> None:
        from terok.lib.domain.project_state import _scope_has_vault_key

        db = MagicMock()
        db.list_ssh_keys_for_scope.return_value = [MagicMock(id=1)]
        with (
            patch("terok_sandbox.CredentialDB", return_value=db),
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            assert _scope_has_vault_key("proj") is True
        db.list_ssh_keys_for_scope.assert_called_once_with("proj")
        db.close.assert_called_once()

    def test_returns_false_when_scope_has_no_keys(self) -> None:
        from terok.lib.domain.project_state import _scope_has_vault_key

        db = MagicMock()
        db.list_ssh_keys_for_scope.return_value = []
        with (
            patch("terok_sandbox.CredentialDB", return_value=db),
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            assert _scope_has_vault_key("proj") is False
        db.close.assert_called_once()


class TestUnassignVaultSshKeys:
    """``project._unassign_vault_ssh_keys`` drains assignments and records the count."""

    def test_records_removed_count(self) -> None:
        from terok.lib.domain.project import _unassign_vault_ssh_keys

        db = MagicMock()
        db.unassign_all_ssh_keys.return_value = 3
        deleted: list[str] = []
        with (
            patch("terok_sandbox.CredentialDB", return_value=db),
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            _unassign_vault_ssh_keys("proj", deleted)
        db.unassign_all_ssh_keys.assert_called_once_with("proj")
        db.close.assert_called_once()
        assert len(deleted) == 1
        assert "3 SSH key assignment" in deleted[0]
        assert "'proj'" in deleted[0]

    def test_zero_keys_writes_nothing(self) -> None:
        from terok.lib.domain.project import _unassign_vault_ssh_keys

        db = MagicMock()
        db.unassign_all_ssh_keys.return_value = 0
        deleted: list[str] = []
        with (
            patch("terok_sandbox.CredentialDB", return_value=db),
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            _unassign_vault_ssh_keys("proj", deleted)
        assert deleted == []


class TestLookupVaultPubLine:
    """``tui.project_actions._lookup_vault_pub_line`` renders the public line."""

    def test_renders_ed25519_pub_line(self) -> None:
        from terok.tui.project_actions import _lookup_vault_pub_line

        record = MagicMock()
        record.key_type = "ed25519"
        record.public_blob = b"fake-blob-bytes"
        record.comment = "tk-main:proj"
        db = MagicMock()
        db.load_ssh_keys_for_scope.return_value = [record]
        with (
            patch("terok_sandbox.CredentialDB", return_value=db),
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            line = _lookup_vault_pub_line("proj")
        assert line is not None
        assert line.startswith("ssh-ed25519 ")
        assert line.endswith(" tk-main:proj")
        db.close.assert_called_once()

    def test_returns_none_when_scope_has_no_keys(self) -> None:
        from terok.tui.project_actions import _lookup_vault_pub_line

        db = MagicMock()
        db.load_ssh_keys_for_scope.return_value = []
        with (
            patch("terok_sandbox.CredentialDB", return_value=db),
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            assert _lookup_vault_pub_line("ghost") is None

    def test_renders_rsa_pub_line(self) -> None:
        from terok.tui.project_actions import _lookup_vault_pub_line

        record = MagicMock()
        record.key_type = "rsa"
        record.public_blob = b"rsa-blob"
        record.comment = ""
        db = MagicMock()
        db.load_ssh_keys_for_scope.return_value = [record]
        with (
            patch("terok_sandbox.CredentialDB", return_value=db),
            patch("terok.lib.core.config.make_sandbox_config"),
        ):
            line = _lookup_vault_pub_line("proj")
        assert line is not None
        assert line.startswith("ssh-rsa ")


class TestGateSyncAuthNotConfigured:
    """``_cmd_gate_sync`` translates ``GateAuthNotConfigured`` into a two-door hint."""

    def test_raises_systemexit_with_two_door_message(self) -> None:
        """The hint names both remediation paths so the user isn't stuck."""
        import pytest
        from terok_sandbox.gate.mirror import GateAuthNotConfigured

        from terok.cli.commands.project import _cmd_gate_sync

        args = MagicMock(project_id="proj", use_personal_ssh=False, force_reinit=False)
        with (
            patch("terok.cli.commands.project.load_project"),
            patch(
                "terok.cli.commands.project.make_git_gate",
                side_effect=GateAuthNotConfigured("proj"),
            ),
            pytest.raises(SystemExit) as excinfo,
        ):
            _cmd_gate_sync(args)
        msg = str(excinfo.value)
        assert "terok project ssh-init proj" in msg
        assert "--use-personal-ssh" in msg
