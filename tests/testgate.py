# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for gate-server-related tests."""

from __future__ import annotations

from terok_sandbox import GateServerStatus

from tests.testnet import GATE_PORT

OUTDATED_UNITS_MESSAGE = "Systemd units are outdated (installed v2, expected v3)."


def make_gate_server_status(
    mode: str = "none",
    *,
    running: bool = False,
    port: int = GATE_PORT,
) -> GateServerStatus:
    """Create a ``GateServerStatus`` with the common test defaults."""
    return GateServerStatus(mode=mode, running=running, port=port)
