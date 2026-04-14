# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit-test fixtures.

Auto-mocks sandbox, shield, and credential proxy helpers so existing tests
do not require a real OCI hook, nftables, podman, proxy daemon, or root
privileges.

The ``_isolate_port_registry`` fixture ensures that the flock-based port
registry never writes to the real ``/tmp/terok-ports/`` or persists claims
to ``~/.local/share/terok/sandbox/``.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_config_caches() -> Iterator[None]:
    """Clear config caches between tests to prevent cross-test pollution."""
    import terok_sandbox.paths as _sandbox_paths

    import terok.lib.core.config as _config

    _sandbox_paths._config_section_cache.clear()
    _config._validated_config_cache = None
    yield
    _sandbox_paths._config_section_cache.clear()
    _config._validated_config_cache = None


@pytest.fixture(autouse=True)
def _isolate_port_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect port registry to tmp dirs so tests never touch the real FS.

    Patches the flock directory and suppresses claims-file writes so that
    tests never create ``port-claims.json`` in the real state directory.
    """
    import terok_sandbox.port_registry as _reg

    registry = tmp_path / "terok-ports"
    registry.mkdir()
    monkeypatch.setattr(_reg, "REGISTRY_DIR", registry)
    monkeypatch.setattr(_reg, "_save_ports", lambda _sd, _p: None)
    _reg.reset_cache()


@pytest.fixture(autouse=True)
def _mock_infrastructure() -> Iterator[None]:
    """Replace Sandbox.run, shield down, and credential proxy with no-ops."""
    with (
        patch(
            "terok.lib.orchestration.task_runners._sandbox",
        ),
        patch(
            "terok.lib.orchestration.task_runners._shield_down_impl",
        ),
        patch(
            "terok.lib.core.config.get_credential_proxy_bypass",
            return_value=True,
        ),
    ):
        yield
