# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``terok vault serve`` (local extension of the sibling-wired group)."""

from __future__ import annotations

import argparse
import sys
from unittest.mock import patch

from terok.cli.commands.vault_local import _SENTINEL, dispatch, register


class TestRegister:
    """Verify ``serve`` registers under a sibling-wired vault group."""

    def test_serve_parseable_under_vault_group(self) -> None:
        """``vault serve`` parses and carries the local-dispatch sentinel."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="cmd")
        vault = sub.add_parser("vault")
        vault_sub = vault.add_subparsers(dest="vault_cmd")

        register(vault_sub)

        args = parser.parse_args(["vault", "serve"])
        assert args.cmd == "vault"
        assert args.vault_cmd == "serve"
        assert getattr(args, "_terok_local_cmd", None) == _SENTINEL
        # _wired_cmd must be set (even to None) so wire_dispatch doesn't
        # short-circuit with the group's help-on-empty behaviour.
        assert hasattr(args, "_wired_cmd") and args._wired_cmd is None


class TestDispatch:
    """Dispatch strips the prefix and delegates to the token broker."""

    def test_ignores_unrelated_namespace(self) -> None:
        """Dispatch returns False for commands that aren't ours."""
        assert dispatch(argparse.Namespace(cmd="task")) is False

    def test_handles_vault_serve(self) -> None:
        """``vault serve`` invokes the token broker with stripped argv."""
        captured_argv: list[str] = []

        def fake_serve() -> None:
            captured_argv.extend(sys.argv)

        fake_module = type(sys)("fake_vault_module")
        fake_module.main = fake_serve

        args = argparse.Namespace(_terok_local_cmd=_SENTINEL)

        with (
            patch.dict(
                sys.modules,
                {"terok_sandbox.vault.token_broker": fake_module},
            ),
            patch.object(
                sys,
                "argv",
                ["terok", "vault", "serve", "--log-level", "DEBUG"],
            ),
        ):
            assert dispatch(args) is True

        assert captured_argv == ["terok-vault-serve", "--log-level", "DEBUG"]
