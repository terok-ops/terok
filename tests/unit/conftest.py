# SPDX-FileCopyrightText: 2025 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit-test fixtures.

Auto-mocks sandbox, shield, and credential proxy helpers so existing tests
do not require a real OCI hook, nftables, podman, proxy daemon, or root
privileges.

The ``_isolate_port_registry`` fixture ensures that the file-based port
registry never writes to the real ``/tmp/terok-ports/`` or persists claims
to ``~/.local/share/terok/sandbox/``.
"""

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_config_caches() -> Iterator[None]:
    """Clear config caches between tests to prevent cross-test pollution."""
    import terok_sandbox.paths as _sandbox_paths

    import terok.lib.core.config as _config

    _sandbox_paths._config_section_cache.clear()
    _config._validated_config_cache = None
    _config._raw_config_cache = None
    yield
    _sandbox_paths._config_section_cache.clear()
    _config._validated_config_cache = None
    _config._raw_config_cache = None


@pytest.fixture(autouse=True)
def _isolate_port_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect port registry to tmp dirs so tests never touch the real FS.

    Patches the shared claims directory and suppresses per-user backup
    writes so that tests never touch ``/tmp/terok-ports/`` or the real
    state directory.
    """
    import terok_sandbox.port_registry as _reg

    registry = tmp_path / "terok-ports"
    registry.mkdir()
    monkeypatch.setattr(_reg._default, "registry_dir", registry)
    monkeypatch.setattr(_reg, "_save_ports", lambda _sd, _p: None)
    monkeypatch.setenv("TEROK_PORT_REGISTRY_DIR", str(registry))
    _reg.reset_cache()


@pytest.fixture(autouse=True)
def _mock_infrastructure() -> Iterator[None]:
    """Replace Sandbox.run, shield down, and vault with no-ops."""
    with (
        patch(
            "terok.lib.orchestration.task_runners._agent_runner",
        ),
        patch(
            "terok.lib.orchestration.task_runners._shield_down_impl",
        ),
        patch(
            "terok.lib.core.config.get_vault_bypass",
            return_value=True,
        ),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_runtime() -> Iterator[MagicMock]:
    """Install a fresh [`MagicMock`][] as the process-wide ``ContainerRuntime``.

    Every unit test gets an isolated mock runtime patched into
    [`terok.lib.core.runtime.get_runtime`][].  Tests that care about
    specific container-level behaviour configure the mock directly
    (``mock_runtime.container.return_value.state = "running"``); tests
    that don't care pay no cost beyond the patch overhead.

    Defaults are set so that common code paths don't trip on
    "a Mock is not iterable" or similar:

    - ``container_states`` / ``container_rw_sizes`` → ``{}``
    - ``images`` / ``force_remove`` → ``[]``
    - ``container(...).wait()`` → ``0`` (benign exit code)
    - ``container(...).login_command(...)`` → a realistic podman argv
    - ``container(...).stream_initial_logs(...)`` → ``True`` (ready)

    Runs *after* ``_mock_infrastructure`` so its ``_agent_runner``
    patch is still in place.
    """
    fake = MagicMock(name="mock_runtime")
    fake.container_states.return_value = {}
    fake.container_rw_sizes.return_value = {}
    fake.images.return_value = []
    fake.force_remove.return_value = []
    container = fake.container.return_value
    container.wait.return_value = 0
    container.login_command.return_value = ["podman", "exec", "-it", "ctr", "bash"]
    container.stream_initial_logs.return_value = True
    with patch("terok.lib.core.runtime.get_runtime", return_value=fake):
        yield fake
